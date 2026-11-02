#!/usr/bin/env python3
"""
Link Budget Calculator — Streamlit wizard.
Ports the Java Swing tool (LinkBudgetCalculator.java + *Dialog.java) into a
single Streamlit app backed directly by the core/ Python modules.
"""
import math
import os
import locale as _locale
import streamlit as st
from core import calculations
from core import Pr_calculations
from core import propagation_calculator as prop
from core import tables_data
from core import country_borders as borders
from core import streamlit_map_component as map_ui
from core import itu_import
from core import draft_state as ds   # persistent draft/default values for every dialog & manual field

# pyproj is only needed for the "compute azimuth from station coordinates"
# convenience feature in the Gt/Gr dialog (great-circle bearing between two
# lat/lon points). Everything else in this file works without it, so this
# import is optional/guarded the same way the horizon-grid DB lookups below
# are guarded (they need pyproj too, via core.query_horizon).
try:
    from pyproj import Geod
    _GEOD = Geod(ellps="WGS84")
except ImportError:
    _GEOD = None

st.set_page_config(page_title="Link Budget Calculator", page_icon="", layout="wide")

# DEM GeoTIFF used for the optional hillshade overlay in Step 9's map. Only
# read if it exists on disk (see map_ui.render_coordination_map) — no error
# if it's missing, the DEM toggle in Step 9 is just disabled.
DEM_TIFF_PATH = os.environ.get("LBC_DEM_PATH", "data_sources/azerbaijan_dem.tif")

# ---------------------------------------------------------------------------
# Session-state scaffolding
# ---------------------------------------------------------------------------
DEFAULTS = {
    "gt": None, "gt_source": None,
    # gt_geometry holds the full Annex 3 geometry "recipe" (mode, zeta,
    # delta0, K, i/is_max/step, gmax, d_over_lambda) behind the current Gt
    # value - ONLY populated when Gt was produced via the Eq. 84-97
    # calculator (Step 1); stays None when Gt was entered manually, which
    # Step 5 uses to decide whether Gt(alpha) can be recomputed per azimuth.
    "gt_geometry": None,
    "pt": None, "pt_source": None,
    "gr": None, "pr_p": None, "gr_pr_source": None,
    "lb_p": None,
    "step5_ready": False,
    "s3_workflow_open": False,
    "mode1_result": None,
    "mode1_profile_result": None, "mode1_profile_diag": None, "mode1_profile_errors": None,
    # Propagation mode (2) (Annex 2, rain scatter) - azimuth-independent result dict from
    # prop.run_mode2(), the error text if it failed, and the AP7 S1.6 combined contour
    # [(azimuth, max(mode1_km, mode2_km)), ...] used by Steps 7 and 9.
    "mode2_result": None, "mode2_error": None, "final_contour_result": None,
    "ppd_horizon_profile": None,
    # Step 5 auto-fetched altitude (fixes the "always 0.0" bug - see
    # _fetch_es_altitude()) and the (lat, lon, db_path) key it was fetched
    # for, so the dialog only re-queries the DB when something relevant
    # actually changed.
    "ppd_altitude_val": 0.0,
    "ppd_altitude_note": None,
    "ppd_fetch_key": None,
    # Propagation auto-fill states
    "ppd_auto_zone": None,
    "ppd_auto_zone_name": None,
    "ppd_auto_dc": 0.0,
    "ppd_n0_val": 0.0,
    "ppd_be_val": 0.0,
    "ppd_dm_val": 0.0,
    "ppd_dx1_val": 0.0,
    "ppd_dc_val": 0.0,
    # Signature (lat, lon, altitude, freq, p) the Step 5 climatic auto-fill was last
    # computed for, so it is only recomputed - and user overrides only reset - when
    # one of those inputs actually changes.
    "ppd_autofill_sig": None,
    # Step 1 calculator coordinates (for auto-population in Step 5)
    "step1_es_lat": None,
    "step1_es_lon": None,
    "step1_sat_lon": None,
    # Step 3 auto-lookup band selection (for frequency/p auto-fill in Step 5)
    "step3_selected_freq": None,
    "step3_selected_band": None,
    # Step 3 multi-band candidates - every frequency band the user has
    # selected/accepted via the Step 3 band picker, {band_str: center_freq_ghz},
    # so Step 5 can offer a choice among all of them instead of just the last one.
    "step3_band_freq_map": {},
    "step3_freq_candidates": [],
    # Step 6 - blind I/N compliance check (test-distance based)
    "blind_in_result": None,
    # Step 7 - border analysis & mitigation
    "border_home_country": "Azerbaijan",
    "border_analysis_rows": None,       # list of per-azimuth result dicts
    "border_analysis_warning": None,    # dependency/download error message, if any
    "border_manual_km": {},             # {azimuth: manually-entered border km} fallback
    "border_mitigation_az": None,       # azimuth the mitigation sliders are targeting
    # ITU SNS (.mdb) filing database import — see itu_import.py
    "itu_mdb_file_key": None,           # (filename, size) of the last file parsed, to avoid re-parsing every rerun
    "itu_mdb_tables": None,             # {"e_stn":..., "geo":..., "non_geo":..., "grp":..., "e_ant":...} DataFrames or None
    "itu_mdb_stations": None,           # list of station dicts from itu_import.extract_earth_stations()
    "itu_mdb_error": None,              # str error message from the last parse attempt, if any
    "itu_mdb_last_import_msg": None,    # summary of the last successful "Import this earth station", shown post-rerun
    "itu_imported_frequencies": None,   # {"transmit": [...], "receive": [...], "unknown": [...]} committed by the ITU import
    "itu_imported_azm_fr": None,        # e_stn.azm_fr of the committed import (deg) or None
    "itu_imported_az_to": None,         # e_stn.azm_to of the committed import (deg) or None
    "itu_imported_altitude": None,      # reviewed e_stn.ant_alt (m above MSL) of the committed import, or None
    "itu_imported_altitude_pos": None,  # (lat, lon) the altitude belongs to
    "itu_mdb_pending": None,            # extracted-but-not-yet-committed values awaiting the user's review ("Proceed")
}
for k, v in DEFAULTS.items():
    st.session_state.setdefault(k, v)

def fmt(v, unit=""):
    return "—" if v is None else f"{v:.4f}{(' ' + unit) if unit else ''}"

def status_ok(msg):
    st.success(msg)

def status_err(msg):
    st.error(msg)

def status_info(msg):
    st.info(msg)


# Whether Step 5 restricts the contour to the ITU operating azimuth sector (e_stn.azm_fr/azm_to)
# by default. OFF on purpose: azm_fr/azm_to are the azimuths the main beam may be pointed at; the
# coordination contour is still defined (with off-axis Gt) around the whole station, so restricting
# it leaves the other azimuths unassessed. The user can switch it on in Step 5 per analysis.
ITU_RESTRICT_SECTOR_DEFAULT = False


def _sector_title_suffix(params=None):
    """Second title line for the contour plot: the sector actually applied, or why it is the full 360."""
    sec = (params or {}).get("azimuth_sector")
    if sec:
        return f"\nAzimuth sector: {sec['azm_fr']:.1f}\u00b0 to {sec['az_to']:.1f}\u00b0"
    info = itu_import.normalize_azimuth_sector(st.session_state.get("itu_imported_azm_fr"),
                                               st.session_state.get("itu_imported_az_to"))
    if info["restrict"]:
        return (f"\nFull 360\u00b0 (operating sector {info['azm_fr']:.1f}\u00b0\u2013{info['az_to']:.1f}\u00b0 not applied)")
    return "\nFull 360\u00b0 (no azimuth range specified)"


def _itu_altitude_for(lat, lon):
    """The imported (reviewed) ITU antenna altitude in m MSL if it exists, is finite and still
    belongs to (lat, lon) (the dialog's coordinates are editable, so an altitude imported for a
    different station must not be reused); otherwise None -> the caller falls back to the DEM/grid DB."""
    alt = _finite_or_none(st.session_state.get("itu_imported_altitude"))
    pos = st.session_state.get("itu_imported_altitude_pos")
    if alt is None or not pos:
        return None
    if abs(lat - pos[0]) > 0.01 or abs(lon - pos[1]) > 0.01:   # ~1 km
        return None
    return alt


def _adjust_horizon_for_altitude(profile, dz_m):
    """First-order correction of a horizon profile [(az, eps_h, dh_km), ...] computed for the DEM
    altitude when the antenna is dz_m higher (+) / lower (-). The limiting terrain point is kept:
    tan(eps_new) = tan(eps) - dz / distance. (A higher antenna may expose a different, farther
    limiting point, in which case the true eps_h is slightly higher than this - the correction
    never overstates shielding.) Entries without a valid distance are left unchanged."""
    out = []
    for az, eps, dh in profile:
        if dh and dh > 0:
            eps = math.degrees(math.atan(math.tan(math.radians(eps)) - dz_m / (dh * 1000.0)))
        out.append((az, eps, dh))
    return out


def _itu_freq_label(f):
    """One-line label for an imported frequency-group dict (see itu_import.extract_all_frequencies)."""
    d = {"E": "Tx", "R": "Rx"}.get(f.get("emi_rcp"), "direction unknown")
    label = (f"{f['freq_min_mhz']:.3f} - {f['freq_max_mhz']:.3f} MHz "
             f"(center {f['freq_center_ghz']:.4f} GHz) [{d}]")
    if f.get("beam_name"):
        label += f" — beam {f['beam_name']}"
    return label


def _itu_imported_freq_list(direction="all"):
    """Flat list of committed imported frequency groups ('transmit' | 'receive' | 'unknown' | 'all')."""
    imp = st.session_state.get("itu_imported_frequencies") or {}
    if direction == "all":
        return imp.get("transmit", []) + imp.get("receive", []) + imp.get("unknown", [])
    return imp.get(direction, [])

# ---------------------------------------------------------------------------
# Shared helper: locate the precomputed horizon-grid SQLite DB (same search
# behaviour as Step 5's "Full profile from precomputed grid DB" option).
# ---------------------------------------------------------------------------
def _find_horizon_db(db_override):
    search_paths = [db_override] if (db_override and db_override.strip()) else [
        "data_sources/horizon_grid.db", "horizon_grid.db",
    ]
    return next((p for p in search_paths if p and os.path.exists(p)), None), search_paths


def _normalize_azimuth(az_deg):
    """Wrap an azimuth into [0, 360)."""
    return az_deg % 360.0


def _interp_eps_h(profile, azimuth_increment, target_az):
    """
    Linearly interpolate the horizon elevation angle εh at an arbitrary
    azimuth `target_az` from a profile sampled at `azimuth_increment`
    degree steps (as produced by horizon_core.calculate_horizon_profile /
    stored by build_horizon_grid.py). profile: {azimuth_int: {'elevation_angle', 'distance_km'}}.
    Returns (eps_h_interpolated, az_lo, az_hi, eps_h_lo, eps_h_hi).
    """
    target_az = _normalize_azimuth(target_az)
    az_lo = int(azimuth_increment * math.floor(target_az / azimuth_increment)) % 360
    az_hi = (az_lo + azimuth_increment) % 360
    if az_lo not in profile or az_hi not in profile:
        raise ValueError(
            f"Profile does not contain the azimuth bins needed to interpolate "
            f"{target_az:.2f}° (looked for {az_lo}° and {az_hi}°)."
        )
    eps_lo = profile[az_lo]["elevation_angle"]
    eps_hi = profile[az_hi]["elevation_angle"]
    # Fraction of the way from az_lo to az_hi (handles the 350->0 wrap).
    span = azimuth_increment
    offset = (target_az - az_lo) % 360
    frac = offset / span if span else 0.0
    eps_interp = eps_lo + frac * (eps_hi - eps_lo)
    return eps_interp, az_lo, az_hi, eps_lo, eps_hi


def _compute_azimuth_and_eps_h(own_lat, own_lon, other_lat, other_lon, db_override):
    """
    Given the coordinates of the station whose horizon/antenna is under
    consideration (own_lat/lon) and the coordinates of the other station
    being pointed toward (other_lat/lon):
      1. Compute the geodesic (great-circle) forward azimuth from own ->
         other using pyproj.Geod — this is 'α: azimuth of the direction
         under consideration' per AP7 Annex 3.
      2. Look up the own station's nearest precomputed horizon profile in
         the grid DB, and linearly interpolate εh (horizon elevation) at
         that azimuth.
    Returns a dict with alpha_deg, eps_h_deg, and diagnostic fields, or
    raises ValueError/ImportError/RuntimeError describing what went wrong.
    """
    if _GEOD is None:
        raise ImportError(
            "pyproj is not installed, so the azimuth between the two stations "
            "cannot be computed automatically. Install pyproj, or enter α and "
            "εh manually instead."
        )

    fwd_az, _back_az, _dist_m = _GEOD.inv(own_lon, own_lat, other_lon, other_lat)
    alpha_deg = _normalize_azimuth(fwd_az)

    db_path, searched = _find_horizon_db(db_override)
    if db_path is None:
        raise RuntimeError(
            "horizon_grid.db not found (checked: " + ", ".join(searched) + "). "
            "Azimuth α was computed (" + f"{alpha_deg:.4f}°" + ") but εh could not be "
            "looked up automatically — build/locate the grid DB, or enter εh manually."
        )

    from core.query_horizon import query_location
    record = query_location(db_path, own_lat, own_lon)

    eps_h_deg, az_lo, az_hi, eps_lo, eps_hi = _interp_eps_h(
        record["profile"], record["azimuth_increment"], alpha_deg
    )

    return {
        "alpha_deg": alpha_deg,
        "eps_h_deg": eps_h_deg,
        "grid_lat": record["lat"], "grid_lon": record["lon"],
        "offset_km": record["offset_km"],
        "azimuth_increment": record["azimuth_increment"],
        "az_lo": az_lo, "az_hi": az_hi, "eps_lo": eps_lo, "eps_hi": eps_hi,
    }


# ===========================================================================
# Step 1 sub-workflow: Gt / Gr via calculations.py (Eq. 84-97)
# ===========================================================================
def _reset_gain_results(ns):
    """Forget the intermediate results (phi, G(phi), auto alpha/eps_h diagnostics)
    of one gain-calculator namespace ('gw_Gt' / 'gw_Gr'). They belong to the
    inputs they were computed from, so they must not outlive a new ITU import
    or 'Clear all'."""
    for suffix in ("phi", "gain", "gain_branch", "dl_used", "dl_estimated", "auto_diag"):
        st.session_state.pop(f"{ns}_{suffix}", None)


_GAIN_MODES = [
    "Direct geometry (Eq. 84-89)",
    "Case 1 – no orbital inclination (Eq. 90)",
    "Case 2 – slightly inclined orbit (Eq. 91-96)",
]


@st.dialog("Determine antenna gain (Eq. 84-97)", width="large")
def gain_workflow_dialog(field_name: str, description: str, on_accept):
    st.caption(f"**{field_name}** — {description}")
    ns = f"gw_{field_name}"
    is_gt = field_name == "Gt"

    # Every input in this dialog is a persisted draft (see core/draft_state.py):
    # what you type - or what the ITU .mdb import pre-filled - is still there
    # when you close and reopen the calculator.
    #
    # The Gt calculator's earth-station latitude/longitude and satellite
    # longitude are SHARED drafts ("es_lat"/"es_lon"/"sat_lon"): Step 5's
    # propagation dialog shows and edits the very same values. Gr belongs to
    # the victim station, so it keeps its own separate, un-shared drafts.
    share_lat = "es_lat" if is_gt else None
    share_lon = "es_lon" if is_gt else None
    share_sat = "sat_lon" if is_gt else None

    st.markdown("#### §1-2 — Station & Satellite Geometry")
    st.caption("Enter the earth station and satellite positions. The longitude difference (δ) will be calculated automatically."
               + (" These values are saved as defaults and are reused in Step 5." if is_gt else ""))

    c1, c2, c3 = st.columns(3)
    with c1:
        zeta = ds.number("Earth station latitude ζ (deg, +N/-S)", f"{ns}_zeta", 0.0,
                         share=share_lat, format="%.4f")
    with c2:
        es_lon = ds.number("Earth station longitude (deg, +E/-W)", f"{ns}_es_lon", 0.0,
                           share=share_lon, format="%.4f")
    with c3:
        sat_lon = ds.number("Satellite orbital longitude (deg, +E/-W)", f"{ns}_sat_lon", 0.0,
                            share=share_sat, format="%.4f",
                            help="e.g., 46.0 for Azerspace-1. Latitude is assumed 0° (Equator).")

    # Automatically calculate longitude difference behind the scenes
    delta = sat_lon - es_lon
    st.info(f"Calculated longitude difference (δ) = {delta:.4f}°")

    K = ds.number("Orbit radius / Earth radius K", f"{ns}_K", calculations.K_DEFAULT, format="%.4f")

    mode = ds.radio("Orbital inclination method", _GAIN_MODES, f"{ns}_mode")

    i_val = is_max = step = None
    if mode.startswith("Direct"):
        i_val = ds.number("Sub-satellite latitude i (deg, +N/-S)", f"{ns}_i", 0.0, format="%.4f")
    elif mode.startswith("Case 2"):
        cc1, cc2 = st.columns(2)
        with cc1:
            is_max = ds.number("Max orbital inclination iₛ (deg)", f"{ns}_ismax", 0.0, format="%.4f")
        with cc2:
            step = ds.number("Grid search step (deg)", f"{ns}_step", 1.0, format="%.4f")

    st.markdown("#### α (azimuth) and εh (horizon elevation)")
    st.caption("These are auto-calculated from the coordinates above, but you can manually adjust them if needed.")

    db_override = ds.text(
        "Grid DB path override (optional — leave blank to auto-search)",
        f"{ns}_db_override",
    )

    if st.button("🔄 Auto-calculate α and εh from coordinates", key=f"{ns}_compute_az", type="primary"):
        try:
            # other_lat is always 0.0 for GSO
            result = _compute_azimuth_and_eps_h(zeta, es_lon, 0.0, sat_lon, db_override)
            # ds.put() (not a direct widget-key write) so the fields below re-seed
            # from the new values AND the values persist as drafts.
            ds.put(f"{ns}_alpha", result["alpha_deg"])
            ds.put(f"{ns}_epsh", result["eps_h_deg"])
            st.session_state[f"{ns}_auto_diag"] = result
            status_ok(
                f"α = {result['alpha_deg']:.4f}°, εh = {result['eps_h_deg']:.4f}° "
                f"(interpolated from nearest grid point {result['offset_km']:.2f} km away)."
            )
        except Exception as e:
            st.session_state[f"{ns}_auto_diag"] = None
            status_err(f"Error: {e}")

    c4, c5 = st.columns(2)
    with c4:
        alpha = ds.number("Azimuth under consideration α (deg)", f"{ns}_alpha", 0.0, format="%.4f")
    with c5:
        eps_h = ds.number("Horizon elevation εh at that azimuth (deg)", f"{ns}_epsh", 0.0, format="%.4f")
    diag = st.session_state.get(f"{ns}_auto_diag")
    if diag:
        st.caption(
            f"ℹ️ Nearest grid point: {diag['grid_lat']:.5f}, {diag['grid_lon']:.5f}. "
            f"εh interpolated from εh({diag['az_lo']}°)={diag['eps_lo']:.4f}° and "
            f"εh({diag['az_hi']}°)={diag['eps_hi']:.4f}°."
        )

    if st.button("Calculate φ", key=f"{ns}_calc_phi"):
        if alpha is None or eps_h is None:
            st.session_state[f"{ns}_phi"] = None
            status_err("Please provide α and εh values.")
        else:
            try:
                if mode.startswith("Direct"):
                    out = calculations.compute_all(zeta, i_val, delta, K, alpha, eps_h)
                    case_note = "Eq. 84-89"
                elif mode.startswith("Case 1"):
                    out = calculations.phi_case1(alpha, eps_h, zeta, delta, K)
                    case_note = out.get("case", "Eq. 90")
                else:
                    out = calculations.phi_case2(alpha, eps_h, zeta, delta, is_max, K, step)
                    case_note = out.get("case", "Eq. 91-96")

                if "phi" not in out:
                    st.session_state[f"{ns}_phi"] = None
                    status_err("No φ returned — check inputs.")
                else:
                    st.session_state[f"{ns}_phi"] = out["phi"]
                    status_ok(f"φ = {out['phi']:.4f}° calculated ({case_note}). Now compute G(φ) below.")
            except Exception as e:
                st.session_state[f"{ns}_phi"] = None
                status_err(f"Error: {e}")

    phi_val = st.session_state.get(f"{ns}_phi")
    st.text_input("φ =", value=fmt(phi_val, "°"), disabled=True, key=f"{ns}_phi_display")

    st.divider()
    st.markdown("#### §3 — Antenna gain towards the horizon G(φ) (Eq. 97)")
    st.caption("Enter D/λ directly, or λ and D, or leave both blank and click "
               "\u201CEstimate D/λ from Gmax\u201D (requires D/λ ≥ 35).")
    gc1, gc2, gc3, gc4 = st.columns(4)
    with gc1:
        # NOTE: the gain stored in an ITU SNS e_ant row is the MAIN-BEAM AXIS gain
        # (Gmax). It is only the *input* to Eq. 97 - the value used as Gt/Gr in the
        # link budget is the horizon gain G(φ) computed below, not Gmax itself.
        gmax = ds.number("Main beam axis gain Gmax (dBi)", f"{ns}_gmax", 0.0, format="%.4f",
                         help="Pre-filled from the ITU SNS e_ant gain when a station was imported. "
                              f"This is Gmax, not {field_name}: {field_name} is the horizon gain G(φ) "
                              "calculated from it below.")
    with gc2:
        wavelength = ds.number("Wavelength λ (m)", f"{ns}_lambda", 0.0, format="%.6f")
    with gc3:
        diameter = ds.number("Antenna diameter D (m)", f"{ns}_D", 0.0, format="%.4f")
    with gc4:
        d_over_lambda_manual = ds.number("D/λ (ratio, overrides λ/D)", f"{ns}_dl", 0.0, format="%.4f")

    bcol1, bcol2 = st.columns(2)
    if bcol1.button("Estimate D/λ from Gmax", key=f"{ns}_estimate_dl"):
        try:
            dl = calculations.estimate_d_over_lambda(gmax)
            st.session_state[f"{ns}_dl_estimated"] = dl
            status_ok(f"D/λ estimated from Gmax: {dl:.4f}")
        except Exception as e:
            status_err(f"Error: {e}")

    dl_estimated = st.session_state.get(f"{ns}_dl_estimated")
    if dl_estimated is not None:
        st.caption(f"Last estimated D/λ = {dl_estimated:.4f} (copy into the D/λ field above to use it)")

    if bcol2.button("Calculate G(φ)", key=f"{ns}_calc_gain"):
        if phi_val is None:
            status_err("Calculate φ above first.")
        else:
            try:
                if d_over_lambda_manual > 0:
                    dl = d_over_lambda_manual
                elif wavelength > 0 and diameter > 0:
                    dl = diameter / wavelength
                elif dl_estimated is not None:
                    dl = dl_estimated
                else:
                    raise ValueError("Provide D/λ directly, or both λ and D, or estimate D/λ from Gmax first.")

                res = calculations.gain_at_phi(phi_val, dl, gmax)
                st.session_state[f"{ns}_gain"] = res["gain"]
                st.session_state[f"{ns}_gain_branch"] = res["branch"]
                st.session_state[f"{ns}_dl_used"] = dl  # persisted so "Use this value" can save it into geometry
                status_ok(f"G(φ) calculated (branch: {res['branch']}).")
            except Exception as e:
                st.session_state[f"{ns}_gain"] = None
                st.session_state[f"{ns}_dl_used"] = None
                status_err(f"Error: {e}")

    gain_val = st.session_state.get(f"{ns}_gain")
    st.text_input("G(φ) =", value=fmt(gain_val, "dBi"), disabled=True, key=f"{ns}_gain_display")

    st.divider()
    cbtn1, cbtn2 = st.columns([1, 1])
    if cbtn1.button(f"Use this value as {field_name}", type="primary", key=f"{ns}_use"):
        if gain_val is None:
            status_err("Calculate G(φ) first.")
        else:
            geometry = {
                "mode": mode,
                "zeta": zeta,
                "delta0": delta,
                "K": K,
                "i": i_val,
                "is_max": is_max,
                "step": step,
                "gmax": gmax,
                "d_over_lambda": st.session_state.get(f"{ns}_dl_used"),
                # Wavelength/diameter are only known if the user entered
                # both directly (rather than D/lambda directly, or
                # "Estimate D/lambda from Gmax") - kept here (None if
                # unavailable) so Step 7's antenna-diameter mitigation
                # slider can recover a real D/lambda baseline instead of
                # having to assume one.
                "wavelength": wavelength if wavelength > 0 else None,
                "diameter": diameter if diameter > 0 else None,
            }
            on_accept(gain_val, geometry)
            # Commit coordinates for the downstream steps (Step 7 mitigation reads
            # these; only for Gt, not Gr). The editable defaults themselves already
            # live in the shared drafts ("es_lat"/"es_lon"/"sat_lon").
            if is_gt:
                st.session_state["step1_es_lat"] = zeta
                st.session_state["step1_es_lon"] = es_lon
                st.session_state["step1_sat_lon"] = sat_lon
            st.rerun()
    if cbtn2.button("Cancel", key=f"{ns}_cancel"):
        st.rerun()


# ===========================================================================
# Step 2 sub-workflow: Pt
# ===========================================================================
@st.dialog("Determine Pt — interferer max transmit power")
def pt_dialog(gain_prefill, on_accept):
    option = ds.radio(
        "Choose input method",
        ["Option A – Manual (Watts → dBW)", "Option B – From EIRP"],
        "ptd_option",
    )
    pt_calc = None
    if option.startswith("Option A"):
        watts = ds.number("Maximum aggregate power (W)", "ptd_watts", 0.0, min_value=0.0,
                          format="%.6f", help="Pt = 10·log₁₀(Watts)")
        if st.button("Calculate Pt", key="ptd_calc_a"):
            if watts <= 0:
                status_err("Power must be > 0 W.")
            else:
                pt_calc = 10.0 * math.log10(watts)
                st.session_state["ptd_result"] = pt_calc
                status_ok(f"Pt = 10·log₁₀({watts}) dBW.")
    else:
        eirp = ds.number("Maximum EIRP (dBW)", "ptd_eirp", 0.0, format="%.4f")
        # EIRP is defined on the antenna's main-beam axis, so the gain that
        # belongs here is Gmax (from the Gt calculator / ITU import), NOT the
        # horizon gain Gt = G(phi). Falls back to Gt only if no Gmax is known.
        gt_field = ds.number("Antenna gain used for this EIRP (dBi)", "ptd_gt", gain_prefill or 0.0,
                             follow_default=True, format="%.4f",
                             help="EIRP is a main-beam-axis quantity, so this is normally Gmax "
                                  "(pre-filled from the Gt calculator / ITU import if known); editable.")
        if st.button("Calculate Pt", key="ptd_calc_b"):
            pt_calc = eirp - gt_field
            st.session_state["ptd_result"] = pt_calc
            status_ok(f"Pt = EIRP − G = {eirp} − {gt_field} dBW.")

    result = st.session_state.get("ptd_result")
    st.text_input("Pt =", value=fmt(result, "dBW"), disabled=True, key="ptd_display")

    c1, c2 = st.columns(2)
    if c1.button("Use this value as Pt", type="primary", key="ptd_use"):
        if result is None:
            status_err("Click \u201CCalculate Pt\u201D first.")
        else:
            on_accept(result)
            st.rerun()
    if c2.button("Cancel", key="ptd_cancel"):
        st.rerun()

# ===========================================================================
# Step 3 sub-workflow A: Pr(p)
# ===========================================================================
@st.dialog("Pr(p) — Threshold Interference Level (Rec. ITU-R S.1430 Eq. 3)")
def pr_sub_dialog(freq_prefill, on_accept):
    freq = ds.number("Operating frequency f (GHz)", "prd_freq", freq_prefill or 0.0,
                     follow_default=True, format="%.4f",
                     help="Used only by \u201CLook up Te & Ms\u201D to pre-fill recommended defaults")
    if st.button("Look up Te & Ms from frequency", key="prd_lookup"):
        ds.put("prd_te_in", Pr_calculations.lookup_te(freq))
        ds.put("prd_ms_in", Pr_calculations.lookup_ms_default(freq))
        status_ok(f"Te and Ms filled with recommended defaults for {freq} GHz (editable).")

    c1, c2 = st.columns(2)
    with c1:
        te = ds.number("Receiving system noise temperature Te (K)", "prd_te_in", 150.0, format="%.4f",
                       help="Recommended: 75 K (<10 GHz), 150 K (10-17 GHz), 300 K (>17 GHz)")
    with c2:
        b = ds.number("Reference bandwidth B (Hz)", "prd_b", 12500.0, format="%.2f")

    c3, c4, c5 = st.columns(3)
    with c3:
        nl = ds.number("Link noise contribution NL (dB)", "prd_nl", Pr_calculations.NL_DEFAULT,
                       format="%.4f", help="Default 1 dB for fixed-satellite links absent other data")
    with c4:
        ms = ds.number("Link performance margin Ms (dB)", "prd_ms_in", 4.0, format="%.4f",
                       help="Practical defaults: 2 dB (<10 GHz), 4 dB (10-17 GHz), 6 dB (>17 GHz)")
    with c5:
        w = ds.number("Equivalence factor W (dB)", "prd_w", 0.0, format="%.4f",
                      help="Usually \u2264 0 dB for digital wanted signals")

    if st.button("Calculate Pr(p)", key="prd_calc"):
        try:
            res = Pr_calculations.pr_p(te, b, nl, ms, w)
            st.session_state["prd_result"] = res["Pr_p"]
            status_ok("Pr(p) calculated (Eq. 3).")
        except Exception as e:
            st.session_state["prd_result"] = None
            status_err(f"Error: {e}")

    result = st.session_state.get("prd_result")
    st.text_input("Pr(p) =", value=fmt(result, "dBW"), disabled=True, key="prd_display")

    c6, c7 = st.columns(2)
    if c6.button("Use this value as Pr(p)", type="primary", key="prd_use"):
        if result is None:
            status_err("Click \u201CCalculate Pr(p)\u201D first.")
        else:
            # Save the Te and B that were used, for the I/N steps
            st.session_state["final_te"] = te
            st.session_state["final_b"] = b
            on_accept(result)
            st.rerun()
    if c7.button("Cancel", key="prd_cancel"):
        st.rerun()

# ===========================================================================
# Step 3 sub-workflow B: Victim parameters
# ===========================================================================
def victim_workflow_ui(on_accept):
    _choices = ["Auto-lookup from ITU coordination-distance tables (Tables 7/8/9)",
                "Calculate from station specs (Gr via Eq. 84-97, Pr(p) via Eq. 3)"]
    if _itu_imported_freq_list():   # only offered once an ITU import has been committed
        _choices.insert(0, "Use frequencies from imported ITU MDB file")
    choice = st.radio("How do you know the victim's parameters?", _choices, key="vd_choice")
    if choice.startswith("Auto-lookup") or choice.startswith("Use frequencies"):
        sheet_label = st.selectbox(
            "Table", ["Transmitting Earth Station (Table 7)",
                      "Receiving Earth Station (Table 8)",
                      "Bidirectional Transmitting ES (Table 9)"],
            key="vd_sheet",
        )
        sheet_alias = {"Transmitting Earth Station (Table 7)": "7",
                       "Receiving Earth Station (Table 8)": "8",
                       "Bidirectional Transmitting ES (Table 9)": "9"}[sheet_label]

        if choice.startswith("Use frequencies"):
            lookup_mode = "__imported__"
        else:
            lookup_mode = st.radio(
                "How do you want to find rows?",
                ["Select a frequency band (recommended)", "Manual frequency entry"],
                key="vd_lookup_mode", horizontal=True,
            )

        if lookup_mode == "__imported__":
            st.caption("The victim shares the band the imported station transmits in, so \u201CTransmit\u201D "
                       "is usually the right direction. Note: grp.noise_t belongs to the imported station's "
                       "own receiver, not the victim, so it is shown for reference only and is not used for Te.")
            direction = st.radio("Select frequency direction",
                                 ["Transmit (emi_rcp='E')", "Receive (emi_rcp='R')", "Unknown/All"],
                                 key="vd_freq_direction", horizontal=True)
            dkey = "transmit" if direction.startswith("Transmit") else "receive" if direction.startswith("Receive") else "all"
            freq_list = _itu_imported_freq_list(dkey)
            if not freq_list:
                st.info(f"No {direction.split(' (')[0].lower()} frequencies available in the imported file.")
            else:
                fi = st.selectbox("Select frequency band", options=list(range(len(freq_list))),
                                  format_func=lambda i: _itu_freq_label(freq_list[i]), key="vd_freq_select")
                sel_f = freq_list[fi]
                st.markdown(f"**Selected frequency: {sel_f['freq_center_ghz']:.4f} GHz**")
                if sel_f.get("noise_t") is not None:
                    st.caption(f"(Imported station's own receive noise temperature: {sel_f['noise_t']:.0f} K — reference only.)")
                svc_i = st.text_input("Filter: Space Service Designation contains (optional)", key="vd_svc_imp")
                c3, c4 = st.columns(2)
                orbit_i = c3.selectbox("Orbit (optional)", ["(any)", "GSO", "Non-GSO"], key="vd_orbit_imp")
                mod_i = c4.selectbox("Modulation (optional)", ["(any)", "A", "N", "A and N"], key="vd_mod_imp")
                if st.button("Find matching rows", key="vd_find_imp"):
                    try:
                        filters = {}
                        if svc_i.strip():
                            filters["service_contains"] = svc_i.strip()
                        if orbit_i != "(any)":
                            filters["orbit"] = orbit_i
                        if mod_i != "(any)":
                            filters["modulation"] = mod_i
                        rows = tables_data.get_victim_parameters(
                            sheet_alias, frequency_ghz=sel_f["freq_center_ghz"], **filters)
                        st.session_state["vd_rows"] = rows
                        if rows:
                            status_ok(f"{len(rows)} matching row(s) found. Pick one below.")
                        else:
                            status_err(f"No rows found for {sel_f['freq_center_ghz']:.4f} GHz with the given filters.")
                    except FileNotFoundError as e:
                        st.session_state["vd_rows"] = []
                        status_err(f"Workbook not available: {e}")
                    except Exception as e:
                        st.session_state["vd_rows"] = []
                        status_err(f"Error: {e}")
        elif lookup_mode.startswith("Select a frequency band"):
            try:
                bands = tables_data.get_all_distinct_bands(sheet_alias)
            except FileNotFoundError as e:
                bands = []
                st.session_state["vd_rows"] = []
                status_err(f"Workbook not available: {e}")

            if bands:
                bands_selected = st.multiselect(
                    "Frequency Band(s) — select one or more",
                    bands, key="vd_band_multiselect",
                    help="Pick one or more ITU frequency bands. Matching rows from every "
                         "selected band are shown together below, and each selected band is "
                         "also added to Step 5's frequency picker so you can choose among them there.",
                )

                c3, c4 = st.columns(2)
                orbit_filter = c3.selectbox("Orbit (optional)", ["(any)", "GSO", "Non-GSO"], key="vd_orbit")
                modulation_filter = c4.selectbox("Modulation (optional)", ["(any)", "A", "N", "A and N"], key="vd_mod")

                # get_rows_by_band() returns raw cells (engineering
                # notation/footnotes not yet parsed) - route through the
                # same numeric parser get_victim_parameters() uses so
                # rows selected from the band dropdown look/behave
                # identically to the manual-frequency lookup path below.
                numeric_cols = ("Gx (dBi)", "Gm (dBi)", "Gr (dBi)", "Emin (deg)",
                                 "Te (K)", "B (Hz)", "Pr(p) (dBW)")

                if bands_selected:
                    try:
                        band_rows = []
                        # Accumulate onto any candidates already gathered
                        # (e.g. from a previous table/sheet or an earlier
                        # multiselect state) rather than replacing them, so
                        # switching tables doesn't lose bands picked earlier.
                        candidate_map = dict(st.session_state.get("step3_band_freq_map") or {})
                        for b in bands_selected:
                            rows_b = tables_data.get_rows_by_band(sheet_alias, b)
                            for r in rows_b:
                                for k in numeric_cols:
                                    if k in r:
                                        r[k] = tables_data.parse_engineering_number(r.get(k))
                            band_rows.extend(rows_b)

                            ranges = tables_data.parse_frequency_ranges_ghz(b)
                            if ranges:
                                candidate_map[b] = (ranges[0][0] + ranges[0][1]) / 2.0

                        if orbit_filter != "(any)":
                            band_rows = [r for r in band_rows
                                         if str(r.get("Orbit") or "").strip().lower() == orbit_filter.lower()]
                        if modulation_filter != "(any)":
                            band_rows = [r for r in band_rows
                                         if str(r.get("Modulation") or "").strip().lower() == modulation_filter.lower()]

                        st.session_state["step3_band_freq_map"] = candidate_map
                        st.session_state["step3_freq_candidates"] = [
                            {"band": b, "freq": f} for b, f in candidate_map.items()
                        ]
                        st.session_state["vd_rows"] = band_rows
                        if band_rows:
                            status_ok(
                                f"{len(band_rows)} row(s) found across {len(bands_selected)} "
                                f"band(s). Pick one below to use its Gx/Gr and Pr(p). All "
                                f"{len(candidate_map)} band(s) selected so far are available "
                                f"in Step 5's frequency picker."
                            )
                        else:
                            status_err("No rows found for the selected band(s) with the given filters.")
                    except FileNotFoundError as e:
                        st.session_state["vd_rows"] = []
                        status_err(f"Workbook not available: {e}")
                    except Exception as e:
                        st.session_state["vd_rows"] = []
                        status_err(f"Error: {e}")
                else:
                    st.info("Select one or more frequency bands above to see matching rows.")

                candidates_now = st.session_state.get("step3_freq_candidates") or []
                if candidates_now:
                    with st.expander(f"Frequency bands available for Step 5 ({len(candidates_now)})"):
                        for c in candidates_now:
                            st.caption(f"• {c['band']}  (~{c['freq']:.4f} GHz)")
                        if st.button("Clear Step 5 frequency candidates", key="vd_clear_candidates"):
                            st.session_state["step3_band_freq_map"] = {}
                            st.session_state["step3_freq_candidates"] = []
                            st.rerun()
            else:
                st.warning("No frequency bands available for this table (check the workbook).")
        else:
            c1, c2 = st.columns(2)
            freq = c1.number_input("Operating frequency (GHz)", value=0.0, format="%.4f", key="vd_freq")
            service_contains = c2.text_input("Filter: Space Service Designation contains (optional)", key="vd_svc")

            c3, c4 = st.columns(2)
            orbit_filter = c3.selectbox("Orbit (optional)", ["(any)", "GSO", "Non-GSO"], key="vd_orbit_manual")
            modulation_filter = c4.selectbox("Modulation (optional)", ["(any)", "A", "N", "A and N"], key="vd_mod_manual")

            if st.button("Find matching rows", key="vd_find"):
                try:
                    filters = {}
                    if service_contains.strip():
                        filters["service_contains"] = service_contains.strip()
                    if orbit_filter != "(any)":
                        filters["orbit"] = orbit_filter
                    if modulation_filter != "(any)":
                        filters["modulation"] = modulation_filter
                    rows = tables_data.get_victim_parameters(sheet_alias, frequency_ghz=freq, **filters)
                    st.session_state["vd_rows"] = rows
                    if rows:
                        status_ok(f"{len(rows)} matching row(s) found. Pick one below.")
                    else:
                        status_err(f"No rows found for {freq} GHz with the given filters.")
                except FileNotFoundError as e:
                    st.session_state["vd_rows"] = []
                    status_err(f"Workbook not available: {e}")
                except Exception as e:
                    st.session_state["vd_rows"] = []
                    status_err(f"Error: {e}")

        rows = st.session_state.get("vd_rows") or []
        if rows:
            def _row_label(i):
                r = rows[i]
                gx = r.get("Gx (dBi)") if r.get("Gx (dBi)") is not None else r.get("Gr (dBi)")
                return (f"{r.get('Table')} | {r.get('Frequency Band')} | "
                        f"{r.get('Space Service Designation')} | Gx/Gr={gx} | "
                        f"Pr(p)={r.get('Pr(p) (dBW)')}")
            idx = st.selectbox("Matching rows", options=list(range(len(rows))),
                               format_func=_row_label, key="vd_row_idx")
            selected = rows[idx]
            st.json({k: v for k, v in selected.items() if v is not None}, expanded=False)
            
            gain_candidate = selected.get("Gx (dBi)")
            if gain_candidate is None:
                gain_candidate = selected.get("Gr (dBi)") or selected.get("Gm (dBi)")
            pr_candidate = selected.get("Pr(p) (dBW)")
            
            if st.button("Use this row's values", type="primary", key="vd_use_row"):
                if gain_candidate is None or pr_candidate is None:
                    status_err("Selected row is missing Gx/Gr or Pr(p) — pick a different row.")
                else:
                    # tables_data.get_victim_parameters() already returns clean
                    # floats (or None) for Te (K)/B (Hz) - engineering notation
                    # ("4x10^3", "10^6") and inline footnotes ("500 (Note 2)")
                    # are parsed there, and "-"/blank cells come back as None.
                    te_row = selected.get("Te (K)")
                    b_row = selected.get("B (Hz)")
                    st.session_state["final_te"] = te_row
                    st.session_state["final_b"] = b_row

                    # Save the selected row's frequency band (and its
                    # midpoint, in GHz) so Step 5 can auto-fill "Frequency
                    # (GHz)" and the ITU-R AP7 default p (%) without the
                    # user having to type the frequency a second time.
                    band_str = selected.get("Frequency Band")
                    st.session_state["step3_selected_band"] = band_str
                    band_ranges = tables_data.parse_frequency_ranges_ghz(band_str)
                    accepted_freq = (band_ranges[0][0] + band_ranges[0][1]) / 2.0 if band_ranges else None
                    st.session_state["step3_selected_freq"] = accepted_freq

                    # Also fold this band into the running multi-band
                    # candidate list Step 5 offers - covers rows accepted
                    # via the manual-frequency fallback path too, not just
                    # the band multiselect above.
                    if band_str and accepted_freq is not None:
                        candidate_map = dict(st.session_state.get("step3_band_freq_map") or {})
                        candidate_map[band_str] = accepted_freq
                        st.session_state["step3_band_freq_map"] = candidate_map
                        st.session_state["step3_freq_candidates"] = [
                            {"band": b, "freq": f} for b, f in candidate_map.items()
                        ]

                    on_accept(float(gain_candidate), float(pr_candidate),
                            f"(auto-lookup from {selected.get('Table')}, {sheet_label})")

                    if te_row is None or b_row is None:
                        missing_fields = ", ".join(
                            n for n, v in (("Te", te_row), ("B", b_row)) if v is None
                        )
                        status_ok(
                            f"Gx/Gr and Pr(p) saved. {missing_fields} not given for this row/sheet in "
                            f"the source table — enter it manually via Step 3's Pr(p) dialog, or Step "
                            f"7/8's Te/B fields, before using I/N-related steps."
                        )
                    st.rerun()
    else:
        st.markdown("**Step A — Gr** (victim earth station, toward the interferer's horizon)")
        _gmax_rx = ds.get("gw_Gr_gmax")
        if _gmax_rx:
            st.caption(f"Main-beam gain Gmax = {_gmax_rx:.4f} dBi is saved as the default in the Gr calculator "
                       "(from the ITU import or your last entry). Gr itself is the horizon gain G(φ) — "
                       "open the calculator to derive it.")
        if st.button("Open Gr calculator (Eq. 84-97)", key="vd_open_gr"):
            gain_workflow_dialog(
                "Gr", "victim earth station, toward the interferer's horizon",
                # Gr's geometry recipe is intentionally discarded here - per the
                # main body's Lb(p) formula, Gr is the victim's fixed horizon
                # gain and does not vary with the coordinating station's own
                # azimuth, unlike Gt (see Step 1 below).
                lambda value, geometry=None: st.session_state.__setitem__("vd_gr_calculated", value),
            )
        gr_calc = st.session_state.get("vd_gr_calculated")
        st.text_input("Gr =", value=fmt(gr_calc, "dBi"), disabled=True, key="vd_gr_display")

        st.markdown("**Step B — Pr(p)**")
        freq_hint = ds.number("Victim operating frequency (GHz, for Te/Ms lookup)", "vd_freq_hint2", 0.0,
                              format="%.4f")
        st.session_state["vd_freq_hint"] = freq_hint
        
        if st.button("Open Pr(p) calculator (Eq. 3)", key="vd_open_pr"):
            pr_sub_dialog(freq_hint, lambda value: st.session_state.__setitem__("vd_pr_calculated", value))
        pr_calc = st.session_state.get("vd_pr_calculated")
        st.text_input("Pr(p) =", value=fmt(pr_calc, "dBW"), disabled=True, key="vd_pr_display")

        if st.button("Use these values for Gr / Pr(p)", type="primary", key="vd_use_calc"):
            if gr_calc is None or pr_calc is None:
                status_err("Calculate both Gr and Pr(p) above first.")
            else:
                on_accept(gr_calc, pr_calc, "(Gr calculated via Eq. 84-97; Pr(p) calculated via Eq. 3)")
                st.rerun()

# ===========================================================================
# Step 5 sub-workflow: Propagation parameters + coordination distance
# ===========================================================================
def _run_mode1(Lb_p, frequency_GHz, p_percent, zone, horizon_elevation_deg, horizon_distance_km,
               latitude_deg, N0_override, dtm_km, dlm_km, dc_km, is_transmitter, land_fraction, step_km):
    kwargs = dict(
        Lb_p=Lb_p, frequency_GHz=frequency_GHz, p_percent=p_percent, zone=zone,
        horizon_elevation_deg=horizon_elevation_deg, horizon_distance_km=horizon_distance_km,
        latitude_deg=latitude_deg, N0_override=N0_override, dtm_km=dtm_km, dlm_km=dlm_km,
        dc_km=dc_km, is_transmitter=is_transmitter, land_fraction=land_fraction, step_km=step_km,
    )
    if 0.1 <= frequency_GHz < 0.79:
        return prop.run_mode1_100_790(**kwargs)
    elif 0.79 <= frequency_GHz < 60.0:
        return prop.run_mode1_790_60000(**kwargs)
    elif 60.0 <= frequency_GHz <= 105.0:
        return prop.run_mode1_60_105(**kwargs)
    else:
        raise ValueError("frequency band not yet implemented (only 100 MHz-105 GHz supported in this build)")


# Band-appropriate required-loss key(s) already returned by each run_mode1_*
# function (see propagation_calculator.py): L1_p for 100-790 MHz (Eq. 27a),
# L3_p/L4_p for 790 MHz-60 GHz (Eq. 39a/42a - both must be met per Eq. 32a),
# L8_p for 60-105 GHz (Eq. 59a). This just reads them back out of the result
# dict already produced by _run_mode1 - it does not compute anything new.
_MODE1_LOSS_KEYS = ("L1_p", "L3_p", "L4_p", "L8_p")


def _mode1_diagnostics(m1_result):
    """Pull the per-azimuth diagnostic fields (Ah, band-appropriate required
    loss value(s), terminated_by) out of a run_mode1_* result dict, for
    display alongside required_distance_km. All values here are already
    computed and returned by propagation_calculator.py - this only selects
    which keys are present for the active band."""
    diag = {
        "Ah": m1_result.get("Ah"),
        "terminated_by": m1_result.get("terminated_by"),
    }
    for k in _MODE1_LOSS_KEYS:
        if k in m1_result:
            diag[k] = m1_result[k]
    return diag


def _compute_mode2_and_combine(params, mode1_by_az):
    """Run propagation mode (2) (Annex 2, Eq. 62-83) and combine it with the mode (1)
    contour per AP7 main body S1.6: the required distance on every azimuth is
    max(mode1_distance, mode2_distance).

    Mode (2) does not depend on azimuth here, so it is computed ONCE and its distance
    dr (km) is compared against each azimuth's mode (1) distance. It uses
    L(p) = Pt - Pr(p) (NOT Lb(p)): Gx is folded into Eq. 82 itself, so Gt/Gr are not
    added to the required loss as they are for mode (1).

    Stores mode2_result / mode2_error / final_contour_result in session state (the
    contour is only built when mode1_by_az - [(az, km), ...] - is given, i.e. a full
    horizon profile). Returns True on success. A mode (2) failure never discards the
    mode (1) results; it is recorded in mode2_error and surfaced in Step 5.

    Shared by the Step 5 button and _recompute_contour() (Step 7's mitigation re-run)
    so both always apply the same rule.
    """
    st.session_state["mode2_result"] = None
    st.session_state["mode2_error"] = None
    st.session_state["final_contour_result"] = None
    try:
        missing = [k for k in ("rain_zone", "elevation_angle", "gx") if params.get(k) is None]
        if missing:
            raise ValueError(
                "Mode 2 inputs missing (" + ", ".join(missing) + ") - reopen \u201CEnter propagation "
                "parameters\u2026\u201D, fill the \u201CRain climatic zone & geometry (Mode 2)\u201D "
                "section and click \u201CUse these parameters\u201D."
            )
        if params["p_percent"] <= 0.001:
            # Eq. 63-67 are defined for 0.001% < p < 0.3% (and Eq. 68 above that); at/below
            # 0.001% the log terms vanish or go negative and the maths fails obscurely.
            raise ValueError(
                f"p = {params['p_percent']:g}% is outside the Annex 2 range (p must be > 0.001%)."
            )
        m2 = prop.run_mode2(
            Pt=st.session_state["pt"],
            Pr_p=st.session_state["pr_p"],
            frequency_GHz=params["frequency_GHz"],
            p_percent=params["p_percent"],
            rain_zone=params["rain_zone"],
            latitude_deg=params["latitude"],
            longitude_deg=params["longitude"],
            elevation_angle_deg=params["elevation_angle"],
            Gx=params["gx"],
            step_km=params["step"],
        )
    except Exception as e:
        st.session_state["mode2_error"] = str(e)
        return False
    st.session_state["mode2_result"] = m2
    if mode1_by_az:
        d2 = m2["dr_km"]
        st.session_state["final_contour_result"] = [(az, max(d1, d2)) for az, d1 in mode1_by_az]
    return True


def _active_contour():
    """The per-azimuth required-distance contour that downstream steps (7, 9) should use:
    the AP7 S1.6 combined max(mode 1, mode 2) contour when mode (2) was computed,
    otherwise the plain mode (1) contour."""
    return (st.session_state.get("final_contour_result")
            or st.session_state.get("mode1_profile_result"))


def _gt_at_azimuth(geometry, alpha, eps_h):
    """Recompute Gt(alpha) for one azimuth from the geometry 'recipe' saved
    by Step 1's Eq. 84-97 calculator (gain_workflow_dialog), reusing this
    profile's own horizon elevation eps_h at that azimuth - the same value
    already used for site shielding Ah - as the eps_h input to Eq. 89. This
    calls the exact same calculations.py functions the dialog itself uses
    for a single azimuth; no new math, just different alpha/eps_h inputs
    per azimuth. Returns (Gt_alpha_dBi, phi_deg)."""
    dl = geometry.get("d_over_lambda")
    gmax = geometry.get("gmax")
    zeta = geometry.get("zeta")
    delta0 = geometry.get("delta0")
    K = geometry.get("K")
    mode = geometry.get("mode") or ""

    if dl is None:
        raise ValueError("Gt geometry is missing D/\u03bb (recalculate Gt via the Step 1 calculator)")

    if mode.startswith("Direct"):
        out = calculations.compute_all(zeta, geometry.get("i"), delta0, K, alpha, eps_h)
    elif mode.startswith("Case 1"):
        out = calculations.phi_case1(alpha, eps_h, zeta, delta0, K)
    elif mode.startswith("Case 2"):
        out = calculations.phi_case2(alpha, eps_h, zeta, delta0, geometry.get("is_max"), K,
                                      geometry.get("step") or 1.0)
    else:
        raise ValueError(f"unrecognized Gt geometry mode: {mode!r}")

    if "phi" not in out:
        raise ValueError("phi could not be determined for this azimuth/geometry")

    phi_val = out["phi"]
    res = calculations.gain_at_phi(phi_val, dl, gmax)
    return res["gain"], phi_val


def _default_p_percent(frequency_ghz):
    """ITU-R AP7 default percentage-of-time p (%) for a given frequency, per
    the rule of thumb used for the p_percent auto-fill in Step 5: p = 1% for
    f < 10 GHz, p = 0.01% for 10 GHz <= f < 60 GHz. At/above 60 GHz Eq. 19
    applies and the p that goes into the mode 1 loss equations is not a
    fixed constant, so this still returns 0.01% as a starting point but the
    field remains fully editable."""
    if frequency_ghz is None:
        return 1.0
    if frequency_ghz < 10.0:
        return 1.0
    return 0.01


def _fetch_es_altitude(lat, lon, db_path):
    """Best-effort earth-station altitude (m) for (lat, lon).

    Fixes the previous bug where Step 5 always displayed 0.0 m regardless
    of actual terrain: it was reading a non-existent 'altitude' key from
    the grid-DB record instead of the 'station_alt' column that
    build_horizon_grid.py / query_horizon.py actually populate.

    Tries, in order:
      1. The nearest precomputed horizon_grid.db grid point's station_alt
         (DEM-derived at build time - see
         horizon_core.calculate_horizon_profile()).
      2. If there's no DB, no coverage there, or the lookup fails: the
         optional `srtm` package (SRTM.py; `pip install srtm.py`) for an
         on-the-fly SRTM lookup - wrapped in try/except ImportError so the
         app still runs if it isn't installed.
      3. Otherwise, 0.0 m with a note the caller should surface as a
         warning. This function never raises.

    Returns (altitude_m, record_or_None, note). `record` is the full
    query_location() result (including the horizon profile) so callers
    that need both altitude AND the profile only pay for one DB round
    trip instead of two.
    """
    if db_path:
        try:
            try:
                from core.query_horizon import query_location
            except ImportError:
                from query_horizon import query_location
            record = query_location(db_path, lat, lon)
            alt = record.get("station_alt")
            if alt is not None:
                return (float(alt), record,
                        f"horizon grid DB, nearest point {record['offset_km']:.2f} km away")
        except ImportError as e:
            return (0.0, None,
                    f"grid lookup unavailable ({e}) — install numpy/scipy/pyproj "
                    "(requirements-geo.txt)")
        except Exception:
            pass  # fall through to the SRTM fallback below

    try:
        import srtm
        data = srtm.get_data()
        elev = data.get_elevation(lat, lon)
        if elev is not None:
            return float(elev), None, "SRTM fallback lookup (`srtm` package)"
    except ImportError:
        pass
    except Exception:
        pass

    return (0.0, None,
            "could not be determined — no grid DB coverage at this location and the "
            "optional `srtm` package is not installed/failed; defaulting to 0.0 m")


@st.dialog("Coordination Distance — Propagation Parameters (AP7 §1.5/§4, Annex 1)", width="large")
def propagation_params_dialog():
    # Check if Step 1 used calculator (has geometry saved)
    used_step1_calculator = st.session_state.get("gt_geometry") is not None

    # --- Earth station location ------------------------------------------
    # Always shown, always editable, always pre-filled from the shared drafts
    # ("es_lat"/"es_lon") - the same values the Gt calculator in Step 1 uses,
    # and what the ITU .mdb import fills in. (Previously this was a read-only
    # display when Gt came from the calculator, and a blank 0.0/0.0 form
    # otherwise - including after an .mdb import, where gt_geometry is None -
    # so the saved coordinates never showed up here.)
    st.markdown("##### Earth station location")
    st.caption("Saved defaults from Step 1 / the ITU import (shared with the Gt calculator) — "
               "edit here if needed.")
    c1, c2 = st.columns(2)
    with c1:
        lat = ds.number("Latitude (deg, +N/-S)", "ppd_lat", st.session_state.get("step1_es_lat") or 0.0,
                        share="es_lat", format="%.6f")
    with c2:
        lon = ds.number("Longitude (deg, +E/-W)", "ppd_lon", st.session_state.get("step1_es_lon") or 0.0,
                        share="es_lon", format="%.6f")

    if used_step1_calculator:
        s1_lat, s1_lon = st.session_state.get("step1_es_lat"), st.session_state.get("step1_es_lon")
        if (s1_lat is not None and s1_lon is not None
                and (abs(lat - s1_lat) > 1e-6 or abs(lon - s1_lon) > 1e-6)):
            st.warning(
                f"This location differs from the one Gt was calculated for in Step 1 "
                f"({s1_lat:.6f}°, {s1_lon:.6f}°). Step 5 recomputes Gt(α) per azimuth from Step 1's "
                "saved geometry, so reopen Step 1's Gt calculator (it now shows these coordinates) "
                "and re-run φ → G(φ) → “Use this value” to keep them consistent."
            )

    # Create a placeholder for status messages
    status_placeholder = st.empty()

    # --- Horizon grid database (mandatory) --------------------------------
    # AP7 §1.5/§4 requires the actual terrain-derived horizon profile, not
    # a manually-typed single azimuth's elevation/distance, so there is no
    # manual entry/fallback here - this is the only path into Step 5.
    st.markdown("##### Horizon grid database")
    db_override = ds.text(
        "Grid DB path override (optional — leave blank to auto-search)",
        "ppd_db_override",
    )
    db_search_paths = [db_override] if db_override.strip() else [
        "data_sources/horizon_grid.db", "horizon_grid.db",
    ]
    db_path = next((p for p in db_search_paths if p and os.path.exists(p)), None)
    if db_path is None:
        status_placeholder.error("horizon_grid.db not found. Checked: " + ", ".join(db_search_paths))
    else:
        st.caption(f"Using grid database: {db_path}")

    # --- Auto-fetch altitude + full horizon profile together --------------
    # Re-fetched automatically whenever lat/lon/db_path change (cheap: one
    # SQLite nearest-neighbor query), so the user isn't required to click a
    # button before the rest of the form makes sense. A manual refetch
    # button is still offered further down for convenience (e.g. after
    # rebuilding the DB on disk, or if the automatic lookup errored).
    fetch_key = (round(lat, 6), round(lon, 6), db_path)
    if st.session_state.get("ppd_fetch_key") != fetch_key:
        auto_alt, auto_record, auto_note = _fetch_es_altitude(lat, lon, db_path)
        st.session_state["ppd_altitude_val"] = auto_alt
        st.session_state["ppd_altitude_note"] = auto_note
        st.session_state["ppd_horizon_profile"] = auto_record
        st.session_state["ppd_fetch_key"] = fetch_key

    dem_altitude = st.session_state.get("ppd_altitude_val", 0.0)   # DEM/grid DB/SRTM (fallback)
    alt_note = st.session_state.get("ppd_altitude_note") or ""
    itu_altitude = _itu_altitude_for(lat, lon)
    if itu_altitude is not None:
        # Official filing altitude (e_stn.ant_alt, m above MSL) takes priority over the DEM.
        altitude = itu_altitude
        altitude_source = "ITU MDB filing"
        st.text_input("Altitude (from ITU MDB filing)", value=f"{altitude:.2f} m", disabled=True,
                      key="ppd_alt_display_itu", help="e_stn.ant_alt as reviewed in the ITU import; metres above mean sea level.")
        st.caption(f"Altitude: {altitude:.2f} m (from ITU MDB filing). DEM/grid lookup at this point: "
                   f"{dem_altitude:.2f} m" + (f" ({alt_note})" if alt_note else "") + " — not used for the altitude.")
    else:
        altitude = dem_altitude
        altitude_source = alt_note or "DEM/grid DB"
        if st.session_state.get("itu_imported_altitude") is not None:
            st.caption("The imported ITU altitude belongs to different coordinates than the ones entered above, "
                       "so the DEM/grid lookup is used instead.")
        st.text_input("Altitude (auto-fetched)", value=f"{altitude:.2f} m", disabled=True,
                      key="ppd_alt_display", help=f"Source: {alt_note}" if alt_note else None)
        if alt_note.startswith("could not be determined") or alt_note.startswith("grid lookup unavailable"):
            st.warning(f"Altitude {alt_note}.")

    step3_freq = st.session_state.get("step3_selected_freq")

    # If Step 3's band picker gathered one or more candidate frequency
    # bands, let the user choose which one to use here instead of typing
    # the frequency again. This only sets the frequency/p (and reruns) when
    # the user explicitly clicks the button - it never overrides a value
    # they've already typed into the field below on its own.
    freq_candidates = st.session_state.get("step3_freq_candidates") or []
    if freq_candidates:
        st.markdown("##### Frequency (from Step 3)")
        candidate_labels = [f"{c['band']}  (~{c['freq']:.4f} GHz)" for c in freq_candidates]
        default_idx = 0
        selected_band = st.session_state.get("step3_selected_band")
        for i, c in enumerate(freq_candidates):
            if c["band"] == selected_band:
                default_idx = i
                break
        fc1, fc2 = st.columns([3, 1])
        chosen_idx = fc1.selectbox(
            "Choose among the frequency band(s) selected in Step 3",
            options=list(range(len(freq_candidates))),
            format_func=lambda i: candidate_labels[i],
            index=default_idx, key="ppd_freq_choice",
        )
        fc2.write("")  # vertical alignment spacer
        if fc2.button("Use this frequency", key="ppd_use_freq_choice"):
            chosen = freq_candidates[chosen_idx]
            ds.put("freq_ghz", chosen["freq"])
            ds.put("ppd_p", _default_p_percent(chosen["freq"]))
            status_ok(f"Frequency set to {chosen['freq']:.4f} GHz from band {chosen['band']}.")
            st.rerun()

    # Frequencies kept from the ITU .mdb import (all groups, Tx/Rx). Only changes the
    # frequency and p on an explicit click. Deliberately does NOT touch Pt (Step 2) or
    # Te (Step 3): changing Pt would silently invalidate Lb(p), and grp.noise_t is the
    # imported station's own receiver noise, not the victim's Te.
    imported_all = _itu_imported_freq_list()
    if imported_all:
        st.markdown("##### Frequency (from ITU import)")
        ic1, ic2 = st.columns([3, 1])
        imp_i = ic1.selectbox("Choose from imported frequencies", options=list(range(len(imported_all))),
                              format_func=lambda i: _itu_freq_label(imported_all[i]),
                              key="ppd_imported_freq_choice")
        ic2.write("")
        if ic2.button("Use this imported frequency", key="ppd_use_imported_freq"):
            chosen = imported_all[imp_i]
            ds.put("freq_ghz", chosen["freq_center_ghz"])
            ds.put("ppd_p", _default_p_percent(chosen["freq_center_ghz"]))
            status_ok(f"Frequency set to {chosen['freq_center_ghz']:.4f} GHz from ITU import.")
            st.rerun()

    # --- Common parameters (identical in both the Step-1-calculator and manual cases) ---
    st.markdown("##### Common parameters")
    c4, c5 = st.columns(2)
    with c4:
        # Draft "freq_ghz": filled by the ITU .mdb import (grp.freq) and kept across
        # dialog opens; falls back to the Step 3 band's midpoint, then 1.0 GHz.
        freq = ds.number("Frequency (GHz)", "ppd_freq", step3_freq if step3_freq is not None else 1.0,
                         share="freq_ghz", follow_default=True, format="%.4f",
                         help=("Saved default (ITU import / your last entry); otherwise pre-filled from "
                               "the band selected in Step 3. Editable."))
    with c5:
        p_percent = ds.number("Time percentage p (%)", "ppd_p", _default_p_percent(freq),
                              follow_default=True, format="%.2f",
                              help="Pre-filled per AP7: 1% below 10 GHz, 0.01% from 10-60 GHz "
                                   "(0.01% starting point at/above 60 GHz, where Eq. 19 applies). Editable.")
    is_transmitter = ds.checkbox("This is the transmitting earth station (unchecked = receiving)",
                                 "ppd_istx", True)

    if used_step1_calculator:
        # Auto-detect zone and fill climatic parameters. Only recomputed when
        # lat/lon/altitude/frequency/p actually change (signature check), so a
        # value the user overrode below survives closing and reopening this
        # dialog instead of being overwritten on every render.
        try:
            from core import radio_climatic_zone as rcz
            import geopandas as gpd

            shp_path = None
            search_paths = [
                "data_sources/costline/ne_10m_coastline.shp",
                "data_sources/coastline/ne_10m_coastline.shp",
                "ne_10m_coastline/ne_10m_coastline.shp"
            ]
            for p in search_paths:
                if os.path.exists(p):
                    shp_path = p
                    break

            if shp_path:
                autofill_sig = (round(lat, 6), round(lon, 6), round(altitude, 2), round(freq, 6),
                                round(p_percent, 6) if freq > 60 else None, shp_path)
                if st.session_state.get("ppd_autofill_sig") != autofill_sig:
                    @st.cache_resource
                    def load_gdf(_path):
                        return gpd.read_file(_path)

                    gdf = load_gdf(shp_path)
                    z, zn, dc_val = rcz.determine_radio_climatic_zone(lon, lat, altitude, gdf)
                    st.session_state["ppd_auto_zone"] = z
                    st.session_state["ppd_auto_zone_name"] = zn
                    st.session_state["ppd_auto_dc"] = dc_val

                    # Auto-fill climatic parameters
                    be = prop.beta_e(lat)
                    n0 = prop.N0(lat)
                    dm = prop.dmin(freq, lat)
                    dx1 = prop.dmax1(freq, z, p_percent if freq > 60 else None)

                    ds.put("ppd_n0", n0)
                    ds.put("ppd_be", be)
                    ds.put("ppd_dm", dm)
                    ds.put("ppd_dx1", dx1)
                    ds.put("ppd_dc", dc_val)
                    st.session_state["ppd_autofill_sig"] = autofill_sig

                z = st.session_state.get("ppd_auto_zone")
                zn = st.session_state.get("ppd_auto_zone_name")
                status_placeholder.success(f"Auto-detected Zone: {z} ({zn}). All parameters filled automatically.")
            else:
                status_placeholder.warning("Coastline shapefile not found. Using default Zone A1.")
                st.session_state["ppd_auto_zone"] = "A1"
                st.session_state["ppd_auto_zone_name"] = "A1"
                st.session_state["ppd_auto_dc"] = 0.0
        except Exception as e:
            status_placeholder.warning(f"Auto-detection issue: {e}. Using defaults.")
            st.session_state["ppd_auto_zone"] = "A1"
    else:
        # Manual zone selection for manual Step 1
        st.markdown("##### Radio-climatic zone (Mode 1)")
        c6, c7 = st.columns([1, 2])
        zone_options = list(prop.ZONES)
        auto_zone = st.session_state.get("ppd_auto_zone")
        default_zone = auto_zone if auto_zone and auto_zone in zone_options else "A1"
        with c6:
            zone_sel = ds.selectbox("Zone", zone_options, "ppd_zone", default_zone)
        # The chosen zone is what Mode 1 must use. (The "Detected Zone" field below
        # and the parameters accepted at the bottom both read ppd_auto_zone - before,
        # this selectbox was never written back to it, so any zone picked here was
        # silently ignored and "A1" was used.)
        st.session_state["ppd_auto_zone"] = zone_sel

        if c7.button("Auto-fill N0 / β_e / dmin / dmax1 / dc", key="ppd_lookup_zone"):
            try:
                be = prop.beta_e(lat)
                n0 = prop.N0(lat)
                dm = prop.dmin(freq, lat)
                dx1 = prop.dmax1(freq, zone_sel, p_percent if freq > 60 else None)
                dc_auto = st.session_state.get("ppd_auto_dc", 0.0)

                ds.put("ppd_n0", n0)
                ds.put("ppd_be", be)
                ds.put("ppd_dm", dm)
                ds.put("ppd_dx1", dx1)
                ds.put("ppd_dc", dc_auto)

                status_placeholder.success(f"Auto-filled: N0={n0:.2f}, β_e={be:.2f}%, dmin={dm:.2f} km, dmax1={dx1:.2f} km, dc={dc_auto:.2f} km")
            except Exception as e:
                status_placeholder.error(f"Error: {e}")

    st.markdown("##### Radio-climatic zone (Mode 1)")
    zone = st.session_state.get("ppd_auto_zone", "A1")
    st.text_input("Detected Zone", value=f"{zone}", disabled=True, key="ppd_zone_display")

    # --- EDITABLE CLIMATIC PARAMETERS ---
    st.markdown("**Climatic Parameters (Auto-filled, Editable)**")
    c_z1, c_z2 = st.columns(2)
    with c_z1:
        n0_field = ds.number("Surface refractivity N0 (N-units)", "ppd_n0", 0.0, format="%.4f")
    with c_z2:
        be_field = ds.number("β_e (percentage)", "ppd_be", 0.0, format="%.4f")

    c_z3, c_z4 = st.columns(2)
    with c_z3:
        dm_field = ds.number("dmin (km)", "ppd_dm", 0.0, format="%.4f")
    with c_z4:
        dx1_field = ds.number("dmax1 (km)", "ppd_dx1", 0.0, format="%.4f")

    st.markdown("##### Horizon profile (Mode 1)")
    st.caption("Full horizon profile from the precomputed grid DB (automatic — required). "
               "There is no manual single-azimuth entry: AP7 §1.5/§4 requires the actual "
               "terrain-derived profile, so this is the only path into Step 5.")

    if st.button("🔄 Refetch horizon profile + altitude", key="ppd_fetch_profile"):
        auto_alt, auto_record, auto_note = _fetch_es_altitude(lat, lon, db_path)
        st.session_state["ppd_altitude_val"] = auto_alt
        st.session_state["ppd_altitude_note"] = auto_note
        st.session_state["ppd_horizon_profile"] = auto_record
        st.session_state["ppd_fetch_key"] = (round(lat, 6), round(lon, 6), db_path)
        if auto_record is not None:
            status_placeholder.success(
                f"Nearest grid point: {auto_record['lat']:.5f}, {auto_record['lon']:.5f} "
                f"({auto_record['offset_km']:.2f} km from entered location) — "
                f"{len(auto_record['profile'])} azimuths, every {auto_record['azimuth_increment']}°. "
                f"Altitude: {auto_alt:.2f} m ({auto_note})."
            )
        else:
            status_placeholder.error(
                f"Failed to fetch profile ({auto_note}). Check the grid DB path/coverage above."
            )
        st.rerun()

    record = st.session_state.get("ppd_horizon_profile")
    if record:
        st.caption(f"Loaded profile: {len(record['profile'])} azimuths, grid point "
                   f"{record['lat']:.5f}, {record['lon']:.5f} "
                   f"({record['offset_km']:.2f} km from entered location)")
        with st.expander("Preview profile"):
            st.dataframe(
                [{"azimuth_deg": az, "elev_ang_deg": round(d["elevation_angle"], 4),
                  "hor_dist_km": round(d["distance_km"], 4)}
                 for az, d in sorted(record["profile"].items())],
                use_container_width=True,
            )
        if st.session_state.get("gt_geometry"):
            st.caption(
                "Note: Gt(alpha) WILL be recomputed per azimuth in Step 5 (Annex 3, Eq. 84-97), "
                "using this profile's own horizon elevation at each azimuth as eps_h and the "
                "geometry saved from Step 1's Gt calculator. Lb(p, alpha) is then recomputed "
                "from that Gt(alpha) for each azimuth before the mode 1 iteration."
            )
        else:
            st.caption(
                "Note: Gt was entered manually in Step 1, so it can't vary by azimuth — the "
                "same fixed Gt/Lb(p) from Step 4 is reused for every azimuth's mode 1 "
                "calculation. Only horizon elevation/distance (and therefore site shielding Ah) "
                "vary across the contour. Use the Eq. 84-97 geometry calculator in Step 1 "
                "instead of a manual value if you want a fully accurate, per-azimuth Gt contour."
            )
        horizon_profile = [(az, d["elevation_angle"], d["distance_km"])
                           for az, d in sorted(record["profile"].items())]
    else:
        horizon_profile = None

    # --- Horizon elevations vs. the filing altitude ---------------------------------------
    # The precomputed profile's eps_h are measured from the DEM station altitude of its grid
    # point. If the antenna altitude comes from the ITU filing and differs, the profile must be
    # shifted accordingly (a higher antenna sees a lower horizon). Offered as a checkbox (default on).
    if horizon_profile and itu_altitude is not None and record and record.get("station_alt") is not None:
        _dz = itu_altitude - float(record["station_alt"])
        if abs(_dz) >= 1.0:
            if ds.checkbox(f"Adjust horizon elevations for the filing altitude ({_dz:+.1f} m vs. the grid DB's "
                           f"{float(record['station_alt']):.1f} m)", "ppd_adjust_horizon_alt", True):
                _orig = horizon_profile
                horizon_profile = _adjust_horizon_for_altitude(horizon_profile, _dz)
                _max_d = max(abs(n[1] - o[1]) for n, o in zip(horizon_profile, _orig))
                st.caption(f"Horizon elevation angles shifted (first-order, same limiting terrain point); "
                           f"largest change {_max_d:.3f}\u00b0.")
            else:
                st.caption("Horizon elevations left as computed for the grid DB altitude.")

    # --- ITU operating azimuth sector (e_stn.azm_fr / azm_to) -----------------------------
    # Filtering happens HERE, once: params["horizon_profile"] feeds the Mode 1 loop, the
    # recompute helper, Step 7/8 (border analysis uses the contour's azimuths) and Step 9.
    sector_applied = None
    _sector = itu_import.normalize_azimuth_sector(st.session_state.get("itu_imported_azm_fr"),
                                                  st.session_state.get("itu_imported_az_to"))
    if _sector["restrict"]:
        st.markdown("##### Operating azimuth sector (from ITU import)")
        st.caption(f"e_stn lists an operating sector of {_sector['azm_fr']:.1f}\u00b0 \u2192 {_sector['az_to']:.1f}\u00b0 "
                   f"({_sector['width_deg']:.1f}\u00b0). These are the azimuths the main beam may point at; the "
                   "coordination contour is normally still assessed around the whole station (off-axis Gt is "
                   "handled per azimuth), so restricting it leaves the other azimuths unassessed.")
        if ds.checkbox("Restrict the contour to this sector (Mode 1, borders, plot)",
                       "ppd_restrict_sector", ITU_RESTRICT_SECTOR_DEFAULT):
            if horizon_profile:
                horizon_profile, _finfo = itu_import.filter_profile_to_sector(horizon_profile, _sector)
                sector_applied = {"azm_fr": _sector["azm_fr"], "az_to": _sector["az_to"]}
                st.caption(f"Filtered horizon profile to {_finfo['n_in']} of {_finfo['n_total']} azimuths within "
                           f"{_sector['azm_fr']:.1f}\u00b0\u2013{_sector['az_to']:.1f}\u00b0.")
                for _w in _finfo["warnings"]:
                    st.warning(_w)
    elif _sector["defined"] or st.session_state.get("itu_imported_azm_fr") is not None \
            or st.session_state.get("itu_imported_az_to") is not None:
        st.caption(_sector["note"])

    st.markdown("##### Path composition (Mode 1)")
    c10, c11, c12 = st.columns(3)
    with c10:
        dtm = ds.number("dtm — longest continuous land distance, Zone A1+A2 (km)", "ppd_dtm", 0.0,
                        format="%.4f")
    with c11:
        dlm = ds.number("dlm — longest continuous inland distance, Zone A2 (km)", "ppd_dlm", 0.0,
                        format="%.4f")
    with c12:
        dc = ds.number("dc — distance to coast, land stations only (km)", "ppd_dc", 0.0, format="%.4f")

    c13, c14 = st.columns(2)
    with c13:
        land_fraction = ds.number("Approximate land fraction of path (0-1)", "ppd_landfrac", 1.0,
                                  min_value=0.0, max_value=1.0, format="%.4f")
    with c14:
        step = ds.number("Iteration step size (km)", "ppd_step", 1.0, format="%.4f")

    st.markdown("##### Rain climatic zone & geometry (Mode 2)")
    st.caption("Annex 2 (hydrometeor/rain scatter, Eq. 62-83). Mode 2 is combined with the Mode 1 "
               "contour per AP7 \u00a71.6: the required distance on each azimuth is the greater of the two.")
    _rain_placeholder = "(select)"
    c15, c16, c17 = st.columns(3)
    with c15:
        rain_zone = ds.selectbox(
            "Rain climatic zone (A\u2013Q)", [_rain_placeholder] + list(prop.RAIN_ZONES),
            "ppd_rain_zone", _rain_placeholder,
            help="ITU-R P.837 rain climatic zone of the earth station site (Table 4). "
                 "No default is assumed - choose the zone for your site.")
    with c16:
        elev_angle = ds.number(
            "Earth station antenna main beam elevation angle \u03b5s (deg)", "ppd_elev", 30.0,
            min_value=0.1, max_value=90.0, format="%.2f",
            help="Main beam elevation of the earth station antenna. Used for the Eq. 83 contour "
                 "offset \u0394d = hR / (2\u00b7tan \u03b5s).")
    with c17:
        gx = ds.number(
            "Terrestrial network antenna gain Gx (dBi)", "ppd_gx", st.session_state.get("gr") or 0.0,
            follow_default=True, format="%.2f",
            help="Gx from ITU Tables 7/8 (pre-filled from Step 3 when available). Enters Eq. 82 directly.")

    _rain_lookup_placeholder = st.empty()
    if st.button("Look up hR / dmax2 / R(0.3%)", key="ppd_lookup_rain"):
        if rain_zone not in prop.RAIN_ZONES:
            _rain_lookup_placeholder.error("Select a rain climatic zone first.")
        else:
            try:
                rz = prop.run_lookup_rain_zone(lat, lon, rain_zone)
                _rain_lookup_placeholder.success(
                    f"Zone {rain_zone}: hR = {rz['hR']:.3f} km, dmax2 = {rz['dmax2']:.2f} km, "
                    f"R(0.3%) = {rz['R_0.3pct']:.2f} mm/h (pc = {rz['pc']:.1f} %). "
                    "Note: hR uses Eq. 75 for all regions; Eq. 74 (North America / Europe west of "
                    "60\u00b0E) is not separately selected - see rain_height_hR()."
                )
            except Exception as e:
                _rain_lookup_placeholder.error(f"Error: {e}")

    st.divider()
    c19, c20 = st.columns(2)
    if c19.button("Use these parameters", type="primary", key="ppd_accept"):
        if not horizon_profile:
            status_placeholder.error(
                "No horizon profile available yet — this is required (there is no manual "
                "single-azimuth fallback). Check the grid DB path/coverage above, or click "
                "\u201C🔄 Refetch horizon profile + altitude\u201D."
            )
        elif rain_zone not in prop.RAIN_ZONES:
            status_placeholder.error(
                "Select a rain climatic zone (Mode 2 section) - there is no default."
            )
        else:
            st.session_state["ppd_params"] = dict(
                latitude=lat, longitude=lon, altitude=altitude, altitude_source=altitude_source,
                frequency_GHz=freq, p_percent=p_percent,
                is_transmitter=is_transmitter, zone=zone, N0=(n0_field if n0_field else None),
                # Placeholders only - the primary (and only) path always
                # supplies a full per-azimuth horizon_profile below; these
                # are never read unless some future single-azimuth fallback
                # is reintroduced.
                eps_h=0.0, dh=0.5, dtm=dtm, dlm=dlm, dc=dc, land_fraction=land_fraction, step=step,
                beta_e=be_field, dmin=dm_field, dmax1=dx1_field,
                horizon_profile=horizon_profile,
                azimuth_sector=sector_applied,   # None = full 360 deg
                # Mode 2 (Annex 2) inputs
                rain_zone=rain_zone, elevation_angle=elev_angle, gx=gx,
            )
            st.session_state["step5_ready"] = True
            st.rerun()
    if c20.button("Cancel", key="ppd_cancel"):
        st.rerun()

# ===========================================================================
# UI — page header
# ===========================================================================
st.title("Link Budget Calculator")
st.caption("Minimum Permissible Basic Transmission Loss Lb(p) — Master Calculator "
           "(RR Appendix 7). Ported from the Java Swing desktop tool.")
st.markdown(
    "This tool determines the minimum permissible basic transmission loss "
    "**Lb(p) = Pt + Gt + Gr − Pr(p)** (RR Appendix 7, main body Eq. 2), by walking "
    "through each term below. **Pr(p)** is the *victim's* tolerance to interference "
    "— not the power transmitted by anyone — so treat it as a budget the interfering "
    "emission must stay under, expressed in dBW in the reference bandwidth B."
)
st.divider()

def manual_or_calculate(step_key, label):
    return st.radio(
        f"How do you want to provide {label}?",
        ["I already have this value", "Help me calculate it"],
        key=f"{step_key}_method", horizontal=True,
    )

# ===========================================================================
# ITU SNS filing database import (.mdb) — optional convenience to
# auto-populate Step 1's earth station coordinates and Step 5's frequency
# from an exported ITU SNS Access database, instead of typing them in by
# hand. See core/itu_import.py for the parsing logic.
# ===========================================================================


# Defaults used in the ITU-import review panel when the .mdb has no value for a field.
ITU_DEFAULT_GMAX_DBI = 50.0    # typical GSO earth station main-beam gain
ITU_DEFAULT_DIAM_M = 3.7       # common size for the 11-12 GHz band
ITU_DEFAULT_FREQ_GHZ = 11.0    # common Ku-band downlink
ITU_DEFAULT_PT_DBW = 35.0      # ~3 kW, typical HPA


def _finite_or_none(v):
    """float(v) if it is a usable finite number, else None (None/NaN/blank/text)."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if (math.isnan(f) or math.isinf(f)) else f


def _itu_label(text, value_from_mdb, required=False):
    """Field label with a visible provenance marker: from MDB vs default vs missing."""
    if value_from_mdb:
        return f"{text} — ✅ from MDB"
    return f"{text} — ❗ missing, enter a value" if required else f"{text} — ⚠️ (default)"


st.header("Import from ITU filing database (.mdb)")
with st.expander(
    "Upload an ITU SNS database (.mdb) to auto-fill earth station coordinates and frequency",
    expanded=False,
):
    # Informational only — load_mdb() itself tries every available backend
    # (access-parser, then mdbtools, then pyodbc) and only raises if none
    # of them can read the file, so this doesn't gate the uploader.
    available = itu_import.list_available_backends()
    if available:
        st.caption(f"✅ Ready to read .mdb files via: {', '.join(available)}.")
    else:
        st.warning(
            "No .mdb-reading backend is currently installed. You can still upload a file below — "
            "it will fail with an explanation — but for it to actually work, install one of:\n\n"
            "- `pip install access-parser` (pure Python — works on Windows/Linux/macOS, recommended)\n"
            "- mdbtools (Linux: `sudo apt install mdbtools`, macOS: `brew install mdbtools` — "
            "no native Windows build)\n"
            "- `pip install pyodbc` + the Microsoft Access Database Engine Redistributable (Windows, "
            "if you already have Access installed)"
        )

    st.caption(
        "Reads the `e_stn`, `geo`, `non_geo`, `grp`, and `e_ant` tables from an exported ITU SNS "
        "Access database. Only Geostationary (GSO) earth stations are supported by this tool — "
        "this is a screening convenience, not an authoritative ITU filing parser."
    )
    mdb_file = st.file_uploader("ITU SNS database file (.mdb)", type=["mdb"], key="itu_mdb_upload")

    if mdb_file is not None:
        # Only re-parse when a genuinely new file has been uploaded (by name
        # + size), not on every rerun triggered by other widgets below.
        file_key = (mdb_file.name, mdb_file.size)
        if st.session_state.get("itu_mdb_file_key") != file_key:
            st.session_state["itu_mdb_file_key"] = file_key
            st.session_state["itu_mdb_tables"] = None
            st.session_state["itu_mdb_stations"] = None
            st.session_state["itu_mdb_error"] = None
            st.session_state["itu_mdb_last_import_msg"] = None
            st.session_state["itu_mdb_pending"] = None
            st.session_state["itu_imported_frequencies"] = None
            st.session_state["itu_imported_azm_fr"] = None
            st.session_state["itu_imported_az_to"] = None
            st.session_state["itu_imported_altitude"] = None
            st.session_state["itu_imported_altitude_pos"] = None
            if str(st.session_state.get("vd_choice", "")).startswith("Use frequencies"):
                st.session_state.pop("vd_choice", None)
            try:
                tables = itu_import.load_mdb(mdb_file.getvalue(), mdb_file.name)
                stations = itu_import.extract_earth_stations(tables["e_stn"])
                st.session_state["itu_mdb_tables"] = tables
                st.session_state["itu_mdb_stations"] = stations
            except itu_import.ItuImportError as e:
                st.session_state["itu_mdb_error"] = str(e)
            except Exception as e:
                st.session_state["itu_mdb_error"] = f"Unexpected error reading file: {e}"

        mdb_error = st.session_state.get("itu_mdb_error")
        stations = st.session_state.get("itu_mdb_stations")
        tables = st.session_state.get("itu_mdb_tables") or {}

        if mdb_error:
            st.error(mdb_error)
        elif not stations:
            st.info("No earth stations could be extracted from this file.")
        else:
            # Only e_stn is required for the import to work at all. geo,
            # non_geo, grp, and e_ant are all optional — a table missing
            # under every alias it's known by (see itu_import._TABLE_ALIASES)
            # is completely normal (e.g. a GSO-only export legitimately has
            # no non_geo/ngeo/non_gso table), so this is informational only,
            # not a warning.
            optional_found = [t for t in ("geo", "non_geo", "grp", "e_ant") if tables.get(t) is not None]
            optional_missing = [t for t in ("geo", "non_geo", "grp", "e_ant") if tables.get(t) is None]
            if optional_found:
                st.caption(f"Optional tables found: {', '.join(optional_found)}.")
            if optional_missing:
                st.caption(
                    f"Not present in this file (fine — related fields just won't auto-fill): "
                    f"{', '.join(optional_missing)}."
                )

            if st.session_state.get("itu_mdb_last_import_msg"):
                status_ok(st.session_state["itu_mdb_last_import_msg"])

            labels = [s["label"] for s in stations]
            sel_idx = st.selectbox(
                "Select an earth station", options=list(range(len(stations))),
                format_func=lambda i: labels[i], key="itu_mdb_station_idx",
            )
            station = stations[sel_idx]

            if station["lat_error"] or station["lon_error"]:
                st.warning(
                    "Coordinate conversion issue for this station — "
                    f"lat: {station['lat_error'] or 'OK'}; lon: {station['lon_error'] or 'OK'}."
                )
            if station.get("sat_name"):
                st.caption(f"Associated satellite (from e_stn.sat_name): {station['sat_name']}")
            else:
                st.caption(
                    "No associated satellite name found on this station's e_stn row — the GSO/"
                    "non-GSO check below won't be able to confirm status for it (will show as "
                    "\u201cunknown\u201d, not blocked)."
                )

            if st.button("Import this earth station", key="itu_mdb_import_btn", type="primary"):
                # This click only EXTRACTS and stages values for review — nothing is
                # written to the calculator/session state until the user clicks
                # "Proceed with these values" below.
                #
                # classify_satellite() joins by satellite NAME (e_stn.sat_name), not by
                # ntc_id: geo/non_geo hold the SATELLITE's own notice, in a different
                # ntc_id space from the earth station's. See its docstring.
                sat_class = itu_import.classify_satellite(
                    station.get("sat_name"), tables.get("geo"), tables.get("non_geo")
                )
                if sat_class == "non_geo":
                    st.session_state["itu_mdb_pending"] = None
                    st.error("This tool only supports Geostationary (GSO) satellites.")
                else:
                    # e_ant rows are keyed by (ntc_id, emi_rcp, beam_name): look up the
                    # transmit ('E') and receive ('R') antennas separately.
                    # e_ant.gain is the MAIN-BEAM AXIS gain Gmax (ITU SNS item B.5.a) —
                    # NOT Gt/Gr. It is only ever staged as a Gmax default.
                    ant_tx = itu_import.extract_antenna_data(station["ntc_id"], tables.get("e_ant"), emi_rcp="E")
                    ant_rx = itu_import.extract_antenna_data(station["ntc_id"], tables.get("e_ant"), emi_rcp="R")
                    grp_info = itu_import.extract_group_data(station["ntc_id"], tables.get("grp"))
                    # ALL frequency groups (a filing often has several beams, Tx and Rx).
                    freqs_all = itu_import.extract_all_frequencies(
                        station["ntc_id"], tables.get("grp"), tables.get("e_ant"))
                    freqs_cls = itu_import.classify_frequencies_by_direction(freqs_all)
                    orbit_info = itu_import.extract_satellite_orbit_data(station.get("sat_name"), tables.get("geo"))

                    diam = _finite_or_none((ant_tx or {}).get("ant_diam_m"))
                    if diam is None:
                        diam = _finite_or_none((ant_rx or {}).get("ant_diam_m"))

                    st.session_state["itu_mdb_pending"] = {
                        "token": st.session_state.get("itu_mdb_pending_counter", 0) + 1,
                        "row_index": station["row_index"],
                        "label": station["label"],
                        "sat_class": sat_class,
                        "lat": _finite_or_none(station.get("lat")),
                        "lon": _finite_or_none(station.get("lon")),
                        "sat_lon": _finite_or_none(station.get("sat_long_nom")),
                        "gmax_tx": _finite_or_none((ant_tx or {}).get("gain_dbi")),
                        "gmax_rx": _finite_or_none((ant_rx or {}).get("gain_dbi")),
                        "diam": diam,
                        "freq": _finite_or_none((grp_info or {}).get("freq_ghz")),
                        "pt": _finite_or_none((grp_info or {}).get("pwr_max_dbw")),
                        "ant_tx_beam": (ant_tx or {}).get("beam_name"),
                        "ant_rx_beam": (ant_rx or {}).get("beam_name"),
                        "grp_matches": (grp_info or {}).get("match_count", 0),
                        "freqs_all": freqs_all,
                        "freqs_cls": freqs_cls,
                        "grp_table_present": tables.get("grp") is not None,
                        "ant_alt": _finite_or_none(station.get("ant_alt")),
                        "azm_fr": station.get("azm_fr"),
                        "az_to": station.get("az_to"),
                        "inclin_exc": _finite_or_none((orbit_info or {}).get("inclin_exc_deg")),
                    }
                    st.session_state["itu_mdb_pending_counter"] = st.session_state["itu_mdb_pending"]["token"]

            # ------------------------------------------------------------------
            # REVIEW PANEL — shown for the staged import of the selected station.
            # ------------------------------------------------------------------
            pending = st.session_state.get("itu_mdb_pending")
            if pending is not None and pending["row_index"] == station["row_index"]:
                tok = pending["token"]
                st.divider()
                st.subheader("Review extracted values")
                st.caption(
                    "Values extracted from the MDB file are marked ✅. Missing values have been "
                    "pre-filled with defaults (marked ⚠️ (default)) — edit any field before proceeding. "
                    "Nothing is saved until you click “Proceed with these values”."
                )
                if pending["sat_class"] == "unknown":
                    st.warning(
                        "This station's associated satellite (by name, from e_stn.sat_name) was not found in "
                        "either the 'geo' or 'non_geo' table — GSO status could not be verified from this "
                        "file. You can still proceed, but please confirm manually."
                    )
                if pending["inclin_exc"] is not None:
                    st.caption(
                        f"FYI: 'geo' table lists an inclination excursion of {pending['inclin_exc']:.4f}° "
                        "(Plans/AP30B field — not necessarily the same as Eq. 91-96's “max orbital "
                        "inclination is”; not auto-filled, review before using)."
                    )
                if pending["grp_matches"] > 1:
                    st.caption(f"{pending['grp_matches']} matching 'grp' rows were found — the first was used "
                               "for frequency and power.")

                _sec = itu_import.normalize_azimuth_sector(pending["azm_fr"], pending["az_to"])
                if _sec["restrict"]:
                    st.info(f"Operating azimuth sector from e_stn: {_sec['azm_fr']:.1f}\u00b0 \u2192 {_sec['az_to']:.1f}\u00b0 "
                            f"({_sec['width_deg']:.1f}\u00b0 wide{', crossing north' if _sec['wraps'] else ''}). "
                            "It will be offered in Step 5 as an optional restriction of the contour "
                            "(the full 360\u00b0 contour remains the default).")
                else:
                    st.caption(_sec["note"])

                # Group 1: coordinates (required — no silent default)
                st.markdown("**Earth station & satellite geometry** (required)")
                c1, c2, c3 = st.columns(3)
                review_lat = c1.number_input(
                    _itu_label("Latitude (deg, +N/-S)", pending["lat"] is not None, required=True),
                    value=pending["lat"], format="%.4f", step=0.01, key=f"itu_rev_lat_{tok}")
                review_lon = c2.number_input(
                    _itu_label("Longitude (deg, +E/-W)", pending["lon"] is not None, required=True),
                    value=pending["lon"], format="%.4f", step=0.01, key=f"itu_rev_lon_{tok}")
                review_sat_lon = c3.number_input(
                    _itu_label("Satellite orbital longitude (deg, +E/-W)", pending["sat_lon"] is not None,
                               required=True),
                    value=pending["sat_lon"], format="%.4f", step=0.01, key=f"itu_rev_satlon_{tok}")
                _missing = [n for n, v in (("Latitude", pending["lat"]), ("Longitude", pending["lon"]),
                                           ("Satellite orbital longitude", pending["sat_lon"])) if v is None]
                if _missing:
                    st.error(f"Not found in the MDB file: {', '.join(_missing)}. These have no default — "
                             "enter them above to proceed.")

                _alt_ok = pending["ant_alt"] is not None
                review_alt = st.number_input(
                    "Antenna altitude above sea level (m) — ✅ from MDB (e_stn.ant_alt)" if _alt_ok else
                    "Antenna altitude above sea level (m) — not in MDB (optional: leave empty to use the DEM/grid lookup in Step 5)",
                    value=pending["ant_alt"], format="%.2f", step=1.0, key=f"itu_rev_alt_{tok}",
                    help="Metres above mean sea level. When set, Step 5 uses it instead of the DEM/horizon-grid altitude.")

                # Group 2: antenna parameters
                st.markdown("**Antenna parameters**")
                gmax_tx_val = pending["gmax_tx"] if pending["gmax_tx"] is not None else (
                    pending["gmax_rx"] if pending["gmax_rx"] is not None else ITU_DEFAULT_GMAX_DBI)
                gmax_rx_val = pending["gmax_rx"] if pending["gmax_rx"] is not None else gmax_tx_val
                diam_val = pending["diam"] if pending["diam"] is not None else ITU_DEFAULT_DIAM_M

                c4, c5, c6 = st.columns(3)
                _gt_help = ("Pre-filled from e_ant.gain, transmit beam (or default 50.0 dBi). This is Gmax, "
                            "NOT Gt — you will calculate Gt = G(φ) from it in Step 1.")
                _gr_help = ("Pre-filled from e_ant.gain, receive beam (falls back to the transmit value, then "
                            "50.0 dBi). This is Gmax, NOT Gr — Gr = G(φ) is calculated from it in Step 3.")
                review_gmax_tx = c4.number_input(
                    _itu_label("Gmax, transmit (dBi)", pending["gmax_tx"] is not None),
                    value=gmax_tx_val, format="%.4f", help=_gt_help, key=f"itu_rev_gmaxtx_{tok}")
                _rx_from_mdb = pending["gmax_rx"] is not None
                review_gmax_rx = c5.number_input(
                    _itu_label("Gmax, receive (dBi)", _rx_from_mdb) if (_rx_from_mdb or pending["gmax_tx"] is None)
                    else "Gmax, receive (dBi) — ⚠️ (default: copied from transmit)",
                    value=gmax_rx_val, format="%.4f", help=_gr_help, key=f"itu_rev_gmaxrx_{tok}")
                review_diam = c6.number_input(
                    _itu_label("Antenna diameter D (m)", pending["diam"] is not None),
                    value=diam_val, format="%.4f",
                    help="Pre-filled from e_ant.ant_diam (or default 3.7 m).", key=f"itu_rev_diam_{tok}")

                # Group 3: frequency & power. A filing can have several frequency groups
                # (beams, transmit AND receive) — pick which one drives this analysis. The
                # transmit ('E') groups come first: the interferer's own emissions are what
                # matter for coordination.
                st.markdown("**Frequency & power**")
                ordered = (pending["freqs_cls"]["transmit"] + pending["freqs_cls"]["receive"]
                           + pending["freqs_cls"]["unknown"])
                sel_g = None
                if ordered:
                    tx_n, rx_n, unk_n = (len(pending["freqs_cls"][k]) for k in ("transmit", "receive", "unknown"))
                    st.caption(f"Found {len(ordered)} frequency group(s) in 'grp': {tx_n} transmit, "
                               f"{rx_n} receive, {unk_n} unknown direction. All of them are kept for Step 3 "
                               "and Step 5; the one chosen here fills the fields below "
                               "(changing it re-seeds them).")
                    sel_i = st.selectbox("Frequency group for this analysis", options=list(range(len(ordered))),
                                         format_func=lambda i: _itu_freq_label(ordered[i]),
                                         key=f"itu_rev_fsel_{tok}")
                    sel_g = ordered[sel_i]
                    with st.expander(f"View all {len(ordered)} imported frequency group(s)"):
                        st.dataframe([{
                            "dir": {"E": "Tx", "R": "Rx"}.get(g["emi_rcp"], "?"),
                            "beam": g["beam_name"], "min MHz": g["freq_min_mhz"], "max MHz": g["freq_max_mhz"],
                            "center GHz": round(g["freq_center_ghz"], 6), "pwr_max dBW": g["pwr_max_dbw"],
                            "noise_t K": g["noise_t"], "e_ant Gmax dBi": g["ant_gain_dbi"],
                            "e_ant D m": g["ant_diam_m"]} for g in ordered], use_container_width=True)
                    fkey = f"{tok}_{sel_i}"
                    freq_default = sel_g["freq_center_ghz"]
                    # Pt: this group's pwr_max unless it is a receive group (pwr_max is not a
                    # transmit power there) -> then the first transmit group that has one.
                    pt_default = None
                    if sel_g["emi_rcp"] != "R" and sel_g["pwr_max_dbw"] is not None:
                        pt_default = sel_g["pwr_max_dbw"]
                    else:
                        pt_default = next((g["pwr_max_dbw"] for g in pending["freqs_cls"]["transmit"]
                                           if g["pwr_max_dbw"] is not None), None)
                else:
                    fkey = f"{tok}_x"
                    freq_default, pt_default = pending["freq"], pending["pt"]
                    if not pending["grp_table_present"]:
                        st.warning("No frequency data found in this MDB file (no 'grp' table). "
                                   "Frequency and Pt are defaults — edit them below.")
                    else:
                        st.warning("No frequency groups found for this station in the 'grp' table. "
                                   "Frequency and Pt are defaults — edit them below.")
                c7, c8 = st.columns(2)
                review_freq = c7.number_input(
                    _itu_label("Frequency (GHz)", freq_default is not None),
                    value=freq_default if freq_default is not None else ITU_DEFAULT_FREQ_GHZ,
                    format="%.4f", key=f"itu_rev_freq_{fkey}")
                review_pt = c8.number_input(
                    _itu_label("Max transmit power Pt (dBW)", pt_default is not None),
                    value=pt_default if pt_default is not None else ITU_DEFAULT_PT_DBW,
                    format="%.4f", key=f"itu_rev_pt_{fkey}")

                pc1, pc2 = st.columns([1, 1])
                proceed = pc1.button("Proceed with these values", type="primary", key=f"itu_rev_proceed_{tok}")
                if pc2.button("Cancel import", key=f"itu_rev_cancel_{tok}"):
                    st.session_state["itu_mdb_pending"] = None
                    st.rerun()

                if proceed:
                    errors = []
                    if review_lat is None or review_lon is None or review_sat_lon is None:
                        errors.append("Latitude, longitude, and satellite orbital longitude are required.")
                    else:
                        if not -90.0 <= review_lat <= 90.0:
                            errors.append("Latitude must be between -90° and +90°.")
                        if not -180.0 <= review_lon <= 180.0:
                            errors.append("Longitude must be between -180° and +180°.")
                        if not -180.0 <= review_sat_lon <= 180.0:
                            errors.append("Satellite orbital longitude must be between -180° and +180°.")
                    if review_alt is not None and not -500.0 <= review_alt <= 9000.0:
                        errors.append("Antenna altitude must be between -500 m and 9000 m above sea level "
                                      "(or left empty to use the DEM/grid lookup).")
                    if review_diam is None or review_diam <= 0:
                        errors.append("Antenna diameter must be greater than 0 m.")
                    if review_freq is None or review_freq <= 0:
                        errors.append("Frequency must be greater than 0 GHz.")
                    if review_gmax_tx is None or review_gmax_rx is None or review_pt is None:
                        errors.append("Gmax and Pt cannot be empty.")

                    if errors:
                        for _e in errors:
                            st.error(_e)
                    else:
                        # A different earth station means any Gt derived earlier (and the
                        # calculators' intermediate phi / G(phi) results) belong to the OLD
                        # station. Gt/Gr are NEVER written by the import (Gmax is not Gt):
                        # clear the stale ones so the user derives them from Gmax.
                        st.session_state["gt"] = None
                        st.session_state["gt_source"] = None
                        st.session_state["gt_geometry"] = None
                        _reset_gain_results("gw_Gt")
                        _reset_gain_results("gw_Gr")
                        # Lb(p) and everything computed from it is now stale too.
                        for _k in ("lb_p", "step5_ready", "mode1_result", "mode1_profile_result",
                                   "mode1_profile_diag", "mode1_profile_errors", "blind_in_result",
                                   "mode2_result", "mode2_error", "final_contour_result",
                                   "border_analysis_rows", "border_analysis_warning"):
                            st.session_state[_k] = DEFAULTS[_k]
                        st.session_state.pop("ppd_params", None)

                        # Coordinates: committed copies (Step 7) + shared drafts (Gt dialog, Step 5).
                        st.session_state["step1_es_lat"] = review_lat
                        st.session_state["step1_es_lon"] = review_lon
                        st.session_state["step1_sat_lon"] = review_sat_lon
                        ds.put("es_lat", review_lat)
                        ds.put("es_lon", review_lon)
                        ds.put("sat_lon", review_sat_lon)          # Gt calculator (shared draft)
                        ds.put("gw_Gr_sat_lon", review_sat_lon)    # Gr calculator

                        # Gmax -> the calculators' Gmax DRAFT fields only. NEVER gt / gr:
                        # Gt/Gr = G(phi) must still be calculated via Eq. 84-97.
                        ds.put("gw_Gt_gmax", review_gmax_tx)
                        ds.put("gw_Gr_gmax", review_gmax_rx)
                        ds.put("gw_Gt_D", review_diam)
                        ds.put("gw_Gr_D", review_diam)

                        # Frequency (Step 5 / "ppd_freq" shares the "freq_ghz" draft) and Pt.
                        ds.put("freq_ghz", review_freq)
                        st.session_state["pt"] = review_pt
                        st.session_state["pt_source"] = "(imported from ITU SNS database, reviewed)"
                        ds.put("s2_manual", review_pt)

                        # Reviewed altitude (None = not provided -> Step 5 keeps its DEM/grid lookup).
                        st.session_state["itu_imported_altitude"] = review_alt
                        st.session_state["itu_imported_altitude_pos"] = (
                            (review_lat, review_lon) if review_alt is not None else None)
                        st.session_state["itu_imported_azm_fr"] = pending["azm_fr"]
                        st.session_state["itu_imported_az_to"] = pending["az_to"]

                        # Keep EVERY imported frequency group for Step 3 / Step 5 pickers.
                        st.session_state["itu_imported_frequencies"] = (
                            pending["freqs_cls"] if pending["freqs_all"] else None)

                        _defaulted = [name for name, v in (
                            ("Gmax (transmit)", pending["gmax_tx"]), ("Gmax (receive)", pending["gmax_rx"]),
                            ("antenna diameter", pending["diam"]),
                            ("frequency", sel_g["freq_center_ghz"] if sel_g else pending["freq"]),
                            ("Pt", pt_default)) if v is None]
                        msg = (f"Imported {pending['label']} with reviewed values: "
                               f"coordinates {review_lat:.4f}°, {review_lon:.4f}°; satellite longitude "
                               f"{review_sat_lon:.4f}°; Gmax {review_gmax_tx:.4f} dBi (Tx) / "
                               f"{review_gmax_rx:.4f} dBi (Rx); D = {review_diam:.4f} m; "
                               f"f = {review_freq:.4f} GHz; Pt = {review_pt:.4f} dBW.")
                        if _defaulted:
                            msg += f" Default values were used/kept for: {', '.join(_defaulted)}."
                        if review_alt is not None:
                            msg += f" Antenna altitude {review_alt:.2f} m MSL ({'from MDB' if pending['ant_alt'] is not None else 'entered'})."
                        else:
                            msg += " No antenna altitude: Step 5 will use the DEM/grid lookup."
                        if _sec["restrict"]:
                            msg += f" Azimuth sector: {_sec['azm_fr']:.1f}\u00b0 to {_sec['az_to']:.1f}\u00b0."
                        else:
                            msg += " " + _sec["note"]
                        if pending["freqs_all"]:
                            msg += (f" {len(pending['freqs_all'])} frequency group(s) kept for the Step 3 and "
                                    "Step 5 frequency pickers.")
                        msg += (" ⚠️ Gt and Gr still need to be calculated from Gmax via the Eq. 84-97 "
                                "calculator in Step 1 (Gt) and Step 3 (Gr) — Gmax is not the horizon gain.")
                        st.session_state["itu_mdb_last_import_msg"] = msg
                        st.session_state["itu_mdb_pending"] = None
                        st.rerun()
st.divider()


# ===========================================================================
# Step 1 — Gt
# ===========================================================================
st.header("Step 1 — Gt: interferer's horizon antenna gain toward the victim")
st.caption("AP7 Annex 3, Eq. 84-97")
method = manual_or_calculate("s1", "Gt")
if method == "I already have this value":
    val = ds.number("Gt (dBi)", "s1_manual", st.session_state["gt"] or 0.0, format="%.4f")
    if st.button("Use this value as Gt", key="s1_use_manual"):
        st.session_state["gt"] = val
        st.session_state["gt_source"] = "(entered manually)"
        # No geometry behind a manually-entered Gt, so Step 5 can't recompute
        # it per azimuth - clear any geometry left over from a previous
        # "Help me calculate it" round so it doesn't get reused by mistake.
        st.session_state["gt_geometry"] = None
        st.rerun()
else:
    if st.button("Open Gt calculator…", key="s1_open"):
        gain_workflow_dialog(
            "Gt", "interfering earth station, toward the victim's horizon",
            lambda value, geometry=None: (
                st.session_state.__setitem__("gt", value),
                st.session_state.__setitem__("gt_source", "(calculated via Eq. 84-97, Step 1)"),
                st.session_state.__setitem__("gt_geometry", geometry),
            ),
        )
_gmax_tx_saved = ds.get("gw_Gt_gmax")
if st.session_state["gt"] is not None:
    status_ok(f"Gt = {st.session_state['gt']:.4f} dBi {st.session_state['gt_source'] or ''}")
else:
    st.warning("Not yet determined.")
    if _gmax_tx_saved:
        st.info(
            f"Main-beam gain **Gmax = {_gmax_tx_saved:.4f} dBi** is already saved as the default in the Gt "
            "calculator (from the ITU import or your last entry). Gt is the *horizon* gain G(φ) and still "
            "has to be calculated from it — choose \u201CHelp me calculate it\u201D and open the calculator "
            "(coordinates, satellite longitude and diameter are pre-filled and editable there)."
        )
st.divider()

# ===========================================================================
# Step 2 — Pt
# ===========================================================================
st.header("Step 2 — Pt: interferer's maximum transmit power (dBW)")
method = manual_or_calculate("s2", "Pt")
if method == "I already have this value":
    val = ds.number("Pt (dBW)", "s2_manual", st.session_state["pt"] or 0.0, format="%.4f")
    if st.button("Use this value as Pt", key="s2_use_manual"):
        st.session_state["pt"] = val
        st.session_state["pt_source"] = "(entered manually)"
        st.rerun()
else:
    if st.button("Open Pt calculator…", key="s2_open"):
        pt_dialog(
            # EIRP is a main-beam quantity: prefer Gmax (Gt-calculator draft / ITU import),
            # fall back to the horizon Gt only if no Gmax is known.
            ds.get("gw_Gt_gmax") or st.session_state["gt"],
            lambda value: (st.session_state.__setitem__("pt", value),
                           st.session_state.__setitem__("pt_source", "(calculated, Step 2)")),
        )
if st.session_state["pt"] is not None:
    status_ok(f"Pt = {st.session_state['pt']:.4f} dBW {st.session_state['pt_source'] or ''}")
else:
    st.warning("Not yet determined.")
st.divider()

# ===========================================================================
# Step 3 — Gr and Pr(p)
# ===========================================================================
st.header("Step 3 — Gr and Pr(p): victim station parameters")
method = manual_or_calculate("s3", "Gr / Pr(p)")
if method == "I already have this value":
    c1, c2 = st.columns(2)
    with c1:
        gr_val = ds.number("Gr (dBi)", "s3_manual_gr", st.session_state["gr"] or 0.0, format="%.4f")
    with c2:
        pr_val = ds.number("Pr(p) (dBW)", "s3_manual_pr", st.session_state["pr_p"] or 0.0, format="%.4f")
    if st.button("Use these values for Gr / Pr(p)", key="s3_use_manual"):
        st.session_state["gr"] = gr_val
        st.session_state["pr_p"] = pr_val
        st.session_state["gr_pr_source"] = "(entered manually)"
        st.rerun()
else:
    if not st.session_state.get("s3_workflow_open", False):
        if st.button("Open victim parameters workflow…", key="s3_open"):
            st.session_state["s3_workflow_open"] = True
            st.rerun()
    else:
        def _accept(gr, pr, note):
            st.session_state["gr"] = gr
            st.session_state["pr_p"] = pr
            st.session_state["gr_pr_source"] = note
            st.session_state["s3_workflow_open"] = False
            
        st.markdown("### Victim Parameters Workflow")
        victim_workflow_ui(_accept)
        
        if st.button("Cancel Workflow", key="vd_cancel_main"):
            st.session_state["s3_workflow_open"] = False
            st.rerun()
if st.session_state["gr"] is not None and st.session_state["pr_p"] is not None:
    status_ok(f"Gr = {st.session_state['gr']:.4f} dBi, Pr(p) = {st.session_state['pr_p']:.4f} dBW. "
              f"{st.session_state['gr_pr_source'] or ''}")
else:
    st.warning("Not yet determined.")
st.divider()

# ===========================================================================
# Step 4 — Result
# ===========================================================================
st.header("Step 4 — Result: Lb(p) = Pt + Gt + Gr − Pr(p)")
c1, c2 = st.columns(2)
if c1.button("Calculate Lb(p)", type="primary", key="s4_calc"):
    missing = [name for name, v in
               [("Pt", st.session_state["pt"]), ("Gt", st.session_state["gt"]),
                ("Gr", st.session_state["gr"]), ("Pr(p)", st.session_state["pr_p"])]
               if v is None]
    if missing:
        status_err("Missing: " + ", ".join(missing))
    else:
        pt, gt, gr, pr = (st.session_state["pt"], st.session_state["gt"],
                          st.session_state["gr"], st.session_state["pr_p"])
        lb = pt + gt + gr - pr
        st.session_state["lb_p"] = lb
        status_ok("Lb(p) calculated successfully.")
if c2.button("Clear all", key="s4_clear"):
    for k, v in DEFAULTS.items():
        st.session_state[k] = v
    # Also forget every saved draft/default (coordinates, Gmax, dialog inputs...) and the
    # calculators' intermediate results, so "Clear all" really starts from a blank wizard.
    ds.clear_all()
    _reset_gain_results("gw_Gt")
    _reset_gain_results("gw_Gr")
    for _k in ("ptd_result", "prd_result"):
        st.session_state.pop(_k, None)
    st.rerun()

if st.session_state["lb_p"] is not None:
    lb = st.session_state["lb_p"]
    st.metric("Lb(p) (dB)", f"{lb:.4f}")
    st.code(
        f"Pt    = {st.session_state['pt']:.4f} dBW\n"
        f"Gt    = {st.session_state['gt']:.4f} dBi\n"
        f"Gr    = {st.session_state['gr']:.4f} dBi\n"
        f"Pr(p) = {st.session_state['pr_p']:.4f} dBW\n"
        f"-----------------------------------------\n"
        f"Lb(p) = Pt + Gt + Gr - Pr(p) = {lb:.4f} dB",
        language="text",
    )
st.divider()



def _recompute_contour():
    """Helper to automatically re-run the Step 5 calculation logic."""
    params = st.session_state.get("ppd_params")
    if not params:
        return False
    try:
        profile = params.get("horizon_profile")
        if profile:
            gt_geometry = st.session_state.get("gt_geometry")
            pt_val = st.session_state["pt"]
            gr_val = st.session_state["gr"]
            pr_val = st.session_state["pr_p"]
            mode1_by_az, mode1_diag_by_az, errors = [], [], []
            for az, eps_h_i, dh_i in profile:
                try:
                    if gt_geometry:
                        gt_alpha, phi_alpha = _gt_at_azimuth(gt_geometry, az, eps_h_i)
                        lb_p_i = pt_val + gt_alpha + gr_val - pr_val
                    else:
                        gt_alpha, phi_alpha = st.session_state["gt"], None
                        lb_p_i = st.session_state["lb_p"]
                    m1_i = _run_mode1(
                        Lb_p=lb_p_i, frequency_GHz=params["frequency_GHz"],
                        p_percent=params["p_percent"], zone=params["zone"],
                        horizon_elevation_deg=eps_h_i, horizon_distance_km=dh_i,
                        latitude_deg=params["latitude"], N0_override=params["N0"],
                        dtm_km=params["dtm"], dlm_km=params["dlm"], dc_km=params["dc"],
                        is_transmitter=params["is_transmitter"], land_fraction=params["land_fraction"],
                        step_km=params["step"],
                    )
                    mode1_by_az.append((az, m1_i["required_distance_km"]))
                    diag = _mode1_diagnostics(m1_i)
                    diag["Gt_alpha_dBi"] = gt_alpha
                    diag["phi_alpha_deg"] = phi_alpha
                    diag["Lb_p_alpha"] = lb_p_i
                    mode1_diag_by_az.append((az, diag))
                except Exception as e:
                    errors.append((az, str(e)))
            st.session_state["mode1_profile_result"] = mode1_by_az
            st.session_state["mode1_profile_diag"] = mode1_diag_by_az
            st.session_state["mode1_profile_errors"] = errors
            st.session_state["mode1_result"] = None
        else:
            m1 = _run_mode1(
                Lb_p=st.session_state["lb_p"], frequency_GHz=params["frequency_GHz"],
                p_percent=params["p_percent"], zone=params["zone"],
                horizon_elevation_deg=params["eps_h"], horizon_distance_km=params["dh"],
                latitude_deg=params["latitude"], N0_override=params["N0"],
                dtm_km=params["dtm"], dlm_km=params["dlm"], dc_km=params["dc"],
                is_transmitter=params["is_transmitter"], land_fraction=params["land_fraction"],
                step_km=params["step"],
            )
            st.session_state["mode1_result"] = m1
            st.session_state["mode1_profile_result"] = None
            st.session_state["mode1_profile_diag"] = None
            st.session_state["mode1_profile_errors"] = []
        # Mode 2 + AP7 S1.6 max() - same rule as the Step 5 button. A mode 2 failure is
        # recorded in mode2_error (shown in Step 5) and does not fail the mode 1 recompute.
        _compute_mode2_and_combine(params, st.session_state.get("mode1_profile_result"))
        return True
    except Exception as e:
        st.session_state["mode1_result"] = None
        st.session_state["mode1_profile_result"] = None
        st.session_state["mode2_result"] = None
        st.session_state["final_contour_result"] = None
        return False


# ===========================================================================
# Step 5 — Coordination distance
# ===========================================================================
st.header("Step 5 — Coordination distance (AP7 §1.5/§1.6/§4, Annex 1 & Annex 2 — propagation modes (1) and (2))")
if st.session_state["lb_p"] is None:
    st.warning("Calculate Lb(p) in Step 4 first.")
elif st.session_state["pt"] is None or st.session_state["pr_p"] is None:
    st.warning("Pt and Pr(p) must be set (Steps 2 and 3) first.")
else:
    st.markdown(
        f"Using **Lb(p) = {st.session_state['lb_p']:.4f} dB** from Step 4, this step determines the "
        "propagation mode (1) required distance (great-circle path, Annex 1) for one azimuth, or for "
        "every azimuth around the earth station if a full horizon profile is supplied. "
        "It also determines the propagation mode (2) (hydrometeor/rain scatter, Annex 2) required "
        "distance, and per AP7 main body §1.6 the required distance used on each azimuth is the "
        "**greater of the mode (1) and mode (2) distances**."
    )
    if st.button("Enter propagation parameters…", key="s5_open_params"):
        propagation_params_dialog()
        
    params = st.session_state.get("ppd_params")
    if params:
        status_ok("Propagation parameters captured. Click \u201CCalculate coordination distance\u201D below.")
    else:
        st.info("Not yet entered.")

    if st.button("Calculate coordination distance", type="primary", key="s5_calc"):
        if not params:
            status_err("Enter propagation parameters first.")
        else:
            try:
                profile = params.get("horizon_profile")
                if profile:
                    gt_geometry = st.session_state.get("gt_geometry")
                    pt_val = st.session_state["pt"]
                    gr_val = st.session_state["gr"]
                    pr_val = st.session_state["pr_p"]

                    mode1_by_az, mode1_diag_by_az, errors = [], [], []
                    for az, eps_h_i, dh_i in profile:
                        try:
                            if gt_geometry:
                                # Recompute Gt(alpha) using this azimuth's own eps_h_i
                                # (already used below for site shielding Ah too), then
                                # Lb(p, alpha) = Pt + Gt(alpha) + Gr - Pr(p) (main body
                                # Eq. 1/2) - Pt, Gr, Pr(p) stay fixed from Steps 2/3.
                                gt_alpha, phi_alpha = _gt_at_azimuth(gt_geometry, az, eps_h_i)
                                lb_p_i = pt_val + gt_alpha + gr_val - pr_val
                            else:
                                # Gt was entered manually - no geometry to recompute from,
                                # so fall back to the single fixed Gt/Lb(p) from Step 4.
                                gt_alpha, phi_alpha = st.session_state["gt"], None
                                lb_p_i = st.session_state["lb_p"]

                            m1_i = _run_mode1(
                                Lb_p=lb_p_i, frequency_GHz=params["frequency_GHz"],
                                p_percent=params["p_percent"], zone=params["zone"],
                                horizon_elevation_deg=eps_h_i, horizon_distance_km=dh_i,
                                latitude_deg=params["latitude"], N0_override=params["N0"],
                                dtm_km=params["dtm"], dlm_km=params["dlm"], dc_km=params["dc"],
                                is_transmitter=params["is_transmitter"], land_fraction=params["land_fraction"],
                                step_km=params["step"],
                            )
                            mode1_by_az.append((az, m1_i["required_distance_km"]))
                            diag = _mode1_diagnostics(m1_i)
                            diag["Gt_alpha_dBi"] = gt_alpha
                            diag["phi_alpha_deg"] = phi_alpha
                            diag["Lb_p_alpha"] = lb_p_i
                            mode1_diag_by_az.append((az, diag))
                        except Exception as e:
                            errors.append((az, str(e)))
                    st.session_state["mode1_profile_result"] = mode1_by_az
                    st.session_state["mode1_profile_diag"] = mode1_diag_by_az
                    st.session_state["mode1_profile_errors"] = errors
                    st.session_state["mode1_result"] = None
                else:
                    m1 = _run_mode1(
                        Lb_p=st.session_state["lb_p"], frequency_GHz=params["frequency_GHz"],
                        p_percent=params["p_percent"], zone=params["zone"],
                        horizon_elevation_deg=params["eps_h"], horizon_distance_km=params["dh"],
                        latitude_deg=params["latitude"], N0_override=params["N0"],
                        dtm_km=params["dtm"], dlm_km=params["dlm"], dc_km=params["dc"],
                        is_transmitter=params["is_transmitter"], land_fraction=params["land_fraction"],
                        step_km=params["step"],
                    )
                    st.session_state["mode1_result"] = m1
                    st.session_state["mode1_profile_result"] = None
                    st.session_state["mode1_profile_diag"] = None
                    st.session_state["mode1_profile_errors"] = []

                # Propagation mode (2) (Annex 2, Eq. 62-83), then AP7 S1.6: the required
                # distance on each azimuth is max(mode1, mode2). Implemented in
                # _compute_mode2_and_combine(), which calls
                #   prop.run_mode2(Pt=st.session_state["pt"], Pr_p=st.session_state["pr_p"],
                #                  frequency_GHz=..., p_percent=..., rain_zone=..., latitude_deg=...,
                #                  longitude_deg=..., elevation_angle_deg=..., Gx=..., step_km=...)
                # and stores it in st.session_state["mode2_result"].
                if _compute_mode2_and_combine(params, st.session_state.get("mode1_profile_result")):
                    status_ok("Coordination distance calculated: propagation mode (1), mode (2), "
                              "and their per-azimuth maximum (AP7 \u00a71.6).")
                else:
                    status_err("Propagation mode (1) calculated, but propagation mode (2) failed: "
                               f"{st.session_state['mode2_error']}")
            except Exception as e:
                st.session_state["mode1_result"] = None
                st.session_state["mode1_profile_result"] = None
                st.session_state["mode2_result"] = None
                st.session_state["mode2_error"] = None
                st.session_state["final_contour_result"] = None
                status_err(f"Failed to calculate: {e}")

    m1 = st.session_state.get("mode1_result")
    m1_profile = st.session_state.get("mode1_profile_result")
    m1_diag = st.session_state.get("mode1_profile_diag") or []
    m1_errors = st.session_state.get("mode1_profile_errors") or []
    m2 = st.session_state.get("mode2_result")
    m2_error = st.session_state.get("mode2_error")
    d2_km = m2["dr_km"] if m2 else None   # mode (2) required distance dr (km), Eq. 62

    if m1_errors:
        with st.expander(f"{len(m1_errors)} azimuth(s) errored during mode 1 (click to see)"):
            for az, msg in m1_errors:
                st.text(f"azimuth {az}°: {msg}")

    if m2_error and (m1_profile or m1):
        st.error(
            f"Propagation mode (2) could not be computed: {m2_error}. The results below are "
            "propagation mode (1) ONLY - they do not satisfy AP7 §1.6 (greater of mode 1 and mode 2) "
            "until this is fixed."
        )
    if m2 is not None and params and not (1.0 <= params["frequency_GHz"] <= 40.5):
        st.warning(
            f"Propagation mode (2): the Table 5 rain coefficients (k, α) only span 1–40.5 GHz and "
            f"{params['frequency_GHz']:.4g} GHz is outside that range, so k and α were clamped to the "
            "nearest table endpoint rather than extrapolated. Treat the mode (2) distance with caution."
        )

    if m1_profile:
        n_az = len(m1_profile)
        _sec_p = (params or {}).get("azimuth_sector")
        if _sec_p:
            st.caption(f"Calculated for {n_az} azimuths (sector {_sec_p['azm_fr']:.1f}\u00b0\u2013{_sec_p['az_to']:.1f}\u00b0). "
                       "Azimuths outside the sector are not assessed.")
        else:
            st.caption(f"Calculated for {n_az} azimuths (full 360\u00b0).")
        if d2_km is not None:
            n_m2 = sum(1 for _, d1 in m1_profile if d2_km > d1)
            st.success(
                f"Coordination contour computed across {n_az} azimuths: final = max(mode 1, mode 2) "
                f"per AP7 §1.6. Mode (2) (rain scatter, dr = {d2_km:.2f} km) governs on {n_m2} of "
                f"{n_az} azimuths."
            )
        else:
            st.success(f"Propagation mode (1) contour computed across {n_az} azimuths.")

        try:
            import matplotlib.pyplot as plt
            _sec_plot = (params or {}).get("azimuth_sector")
            dists1 = [d for _, d in m1_profile]
            if _sec_plot:
                # Draw a wedge: unwrap azimuths so a 350 -> 10 sector is monotonic (no sweep
                # through the whole circle), and close it through the station (r = 0).
                _fr = _sec_plot["azm_fr"]
                azs_rad = [math.radians(_fr + ((az - _fr) % 360.0)) for az, _ in m1_profile]
                closed = [azs_rad[0]] + azs_rad + [azs_rad[-1], azs_rad[0]]

                def _ring(vals):
                    return [0.0] + list(vals) + [0.0, 0.0]
            else:
                azs_rad = [math.radians(az) for az, _ in m1_profile]
                closed = azs_rad + [azs_rad[0]]

                def _ring(vals):
                    return list(vals) + [vals[0]]
            fig = plt.figure(figsize=(6, 6))
            ax = fig.add_subplot(111, projection="polar")
            ax.plot(closed, _ring(dists1), "b-", linewidth=1.2, label="Mode (1)")
            if d2_km is not None:
                finals = [max(d, d2_km) for d in dists1]
                ax.plot(closed, _ring([d2_km] * len(dists1)), "g--", linewidth=1.2,
                        label=f"Mode (2), dr = {d2_km:.1f} km")
                ax.plot(closed, _ring(finals), "r-", linewidth=2.0,
                        label="Final = max(mode 1, mode 2)")
                ax.fill(closed, _ring(finals), alpha=0.12, color="r")
                ax.set_title("Coordination contour: max(Mode 1, Mode 2) per AP7 §1.6"
                             + _sector_title_suffix(params), fontsize=10, pad=20)
            else:
                ax.fill(closed, _ring(dists1), alpha=0.15)
                ax.set_title("Propagation mode (1) required-distance contour"
                             + _sector_title_suffix(params), fontsize=10, pad=20)
            ax.set_theta_zero_location("N")
            ax.set_theta_direction(-1)
            ax.legend(loc="upper right", bbox_to_anchor=(1.3, 1.1), fontsize=8)
            st.pyplot(fig)
        except ImportError:
            st.info("Install matplotlib to see the polar contour plot: pip install matplotlib")

        # Diagnostic columns (Ah, band-appropriate required-loss value(s), and
        # terminated_by) come straight out of the run_mode1_* result dicts via
        # _mode1_diagnostics() above - same values used to compute
        # required_distance_km, just also surfaced here per azimuth so a flat
        # required_km column can be told apart from a genuinely flat terrain
        # profile. diag_map keys on azimuth since m1_diag was built in the
        # same loop, over the same successful azimuths, as m1_profile.
        diag_map = dict(m1_diag)
        loss_cols = [k for k in _MODE1_LOSS_KEYS if any(k in d for d in diag_map.values())]
        # Gt_alpha_dBi is present whether Gt was recomputed per azimuth (varies)
        # or Gt was manual and reused as-is (constant) - either way it's the
        # actual Gt value that went into that azimuth's Lb(p), so it's always
        # shown, not just when geometry was saved.
        has_gt_alpha = any("Gt_alpha_dBi" in d for d in diag_map.values())

        def _governing(d1):
            return "mode 2" if d2_km > d1 else "mode 1"

        import csv, io
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["azimuth_deg", "Ah_dB"] + loss_cols +
                         (["Gt_alpha_dBi"] if has_gt_alpha else []) +
                         ["terminated_by", "mode1_required_km"] +
                         (["mode2_required_km", "final_required_km", "governing_mode"]
                          if d2_km is not None else []))
        for az, d1 in m1_profile:
            diag = diag_map.get(az, {})
            row = [az, f"{diag.get('Ah'):.4f}" if diag.get("Ah") is not None else ""]
            row += [f"{diag[k]:.4f}" if k in diag else "" for k in loss_cols]
            if has_gt_alpha:
                gt_a = diag.get("Gt_alpha_dBi")
                row += [f"{gt_a:.4f}" if gt_a is not None else ""]
            row += [diag.get("terminated_by", ""), f"{d1:.4f}"]
            if d2_km is not None:
                row += [f"{d2_km:.4f}", f"{max(d1, d2_km):.4f}", _governing(d1)]
            writer.writerow(row)
        st.download_button("Download contour as CSV", buf.getvalue(),
                           file_name="coordination_contour.csv", mime="text/csv")

        st.dataframe(
            [
                {
                    "azimuth_deg": az,
                    "Ah_dB": round(diag_map.get(az, {}).get("Ah"), 3) if diag_map.get(az, {}).get("Ah") is not None else None,
                    **{k: round(diag_map.get(az, {})[k], 3) for k in loss_cols if k in diag_map.get(az, {})},
                    **({"Gt_alpha_dBi": round(diag_map.get(az, {})["Gt_alpha_dBi"], 3)}
                       if has_gt_alpha and diag_map.get(az, {}).get("Gt_alpha_dBi") is not None else {}),
                    "terminated_by": diag_map.get(az, {}).get("terminated_by"),
                    "mode1_required_km": round(d1, 3),
                    **({"mode2_required_km": round(d2_km, 3),
                        "final_required_km": round(max(d1, d2_km), 3),
                        "governing_mode": _governing(d1)} if d2_km is not None else {}),
                }
                for az, d1 in m1_profile
            ],
            use_container_width=True,
        )
        if has_gt_alpha and st.session_state.get("gt_geometry"):
            st.caption("Gt(alpha) recomputed per azimuth (Annex 3, Eq. 84-97) - see Gt_alpha_dBi above; "
                       "it should vary across azimuths unless the horizon-elevation profile and "
                       "off-axis geometry happen to coincide.")
        elif has_gt_alpha:
            st.caption("Gt was entered manually in Step 1, so Gt_alpha_dBi above is constant across "
                       "azimuths (the same fixed Gt/Lb(p) from Step 4 was reused for every azimuth).")
        if d2_km is not None:
            st.caption(
                "final_required_km = max(mode1_required_km, mode2_required_km) on each azimuth "
                "(AP7 main body §1.6). Steps 7 and 9 use this final contour."
            )

    elif m1:
        d1 = m1["required_distance_km"]
        if d2_km is not None:
            mc1, mc2, mc3 = st.columns(3)
            mc1.metric("Propagation mode (1) required distance (km)", f"{d1:.3f}")
            mc2.metric("Propagation mode (2) required distance (km)", f"{d2_km:.3f}")
            mc3.metric("Final required distance (km)", f"{max(d1, d2_km):.3f}",
                       help="Greater of the mode (1) and mode (2) distances (AP7 main body §1.6).")
        else:
            st.metric("Propagation mode (1) required distance (km)", f"{d1:.3f}")

        with st.expander("Full breakdown"):
            st.markdown("**Propagation Mode 1 (Annex 1)**")
            st.json({k: v for k, v in m1.items()}, expanded=False)
        st.caption(
            "This is the required distance for this single azimuth only."
            + (" Final = greater of mode (1) and mode (2) (AP7 main body §1.6)." if d2_km is not None
               else " Mode (2) is not available - see the error above.")
        )

    if m2 is not None and (m1_profile or m1):
        with st.expander("Propagation mode (2) breakdown (Annex 2, Eq. 62-83)"):
            st.json({k: v for k, v in m2.items()}, expanded=False)
        st.caption(
            "Mode (2) uses L(p) = Pt − Pr(p) (not Lb(p)): Gx is folded into Eq. 82 itself. The mode (2) "
            f"distance used above is dr = {m2['dr_km']:.2f} km, applied as a circle centred on the earth "
            f"station (the same value on every azimuth). The Eq. 83 contour offset (Δd = "
            f"{m2['delta_d_km']:.2f} km) is reported in the breakdown but is not applied to the contour, "
            "because that would need the earth station beam azimuth, which is not an input here."
        )

    if m1_profile or m1:
        st.caption(
            "NOTE: several of the underlying Annex 1 iterative-loss equations were reconstructed "
            "from a heavily OCR-corrupted source document and are flagged as TIER B in "
            "propagation_calculator.py's module docstring. Verify against an official copy of "
            "RR Appendix 7 before using these numbers in an actual coordination filing."
        )

# ===========================================================================
# Step 6 — Blind I/N Compliance Check (test-distance based, per azimuth)
# ===========================================================================
# This combines what used to be two separate sections: the old "I/N
# Compliance Check" (Te, B, and I/N limit inputs, defaulted from Step 3's
# Tables 7-9 lookup) and the "blind" test-distance check itself. Rather than
# checking I/N at the coordination distance (where I = Pr(p) by definition),
# this uses a user-defined test distance (e.g. the distance from the
# coordinating earth station to a neighbouring administration's border),
# computed using the conservative worst-case victim parameters (Gx, Te, B)
# from ITU Tables 7/8/9 (Step 3) — i.e. proving non-interference *before*
# the victim's exact station coordinates are known.
st.divider()
st.header("Step 6 — Blind I/N Compliance Check (test distance)")
st.caption(
    "Worst-case I/N at a user-defined test distance (e.g. the distance from the "
    "coordinating earth station to a neighbouring administration's border), computed "
    "using the conservative worst-case victim parameters (Gx, Te, B) from ITU Tables "
    "7/8/9 (Step 3) — i.e. proving non-interference *before* the victim's exact "
    "station coordinates are known."
)

missing_blind = []
if st.session_state.get("pt") is None:
    missing_blind.append("Pt (Step 2)")
if st.session_state.get("gt") is None:
    missing_blind.append("Gt (Step 1)")
if st.session_state.get("gr") is None:
    missing_blind.append("Gx / victim antenna gain (Step 3)")
params_blind = st.session_state.get("ppd_params")
if not params_blind:
    missing_blind.append("propagation parameters (Step 5 — frequency, zone, dtm, etc.)")

if missing_blind:
    st.warning("Complete the following first: " + ", ".join(missing_blind))
else:
    te_default = st.session_state.get("final_te", 150.0)
    b_default = st.session_state.get("final_b", 12500.0)
    gx_default = st.session_state.get("gr", 0.0)

    c1, c2, c3 = st.columns(3)
    gx = c1.number_input("Victim antenna gain Gx (dBi)", value=float(gx_default), format="%.4f",
                          key="blind_gx", help="Auto-populated from Step 3 (Gr); editable")
    te = c2.number_input("Victim noise temperature Te (K)", value=float(te_default), format="%.4f",
                          key="blind_te", help="Auto-populated from Step 3 / Tables 7-9; editable")
    b = c3.number_input("Victim reference bandwidth B (Hz)", value=float(b_default), format="%.2f",
                         key="blind_b", help="Auto-populated from Step 3 / Tables 7-9; editable")

    c4, c5 = st.columns(2)
    test_distance_km = c4.number_input("Test distance (km)", value=100.0, min_value=0.0001,
                                        format="%.4f", key="blind_test_distance",
                                        help="e.g. distance from the earth station to the neighbouring administration's border")
    in_limit_blind = c5.number_input("I/N Limit (dB)", value=-10.0, format="%.2f", key="blind_in_limit",
                                      help="Regulatory limit for the victim service (e.g. -10 dB for Fixed Service, ITU-R S.1327)")

    with st.expander("Propagation parameters used (from Step 5 — editable here for this check only)"):
        cc1, cc2, cc3 = st.columns(3)
        freq_blind = cc1.number_input("Frequency (GHz)", value=float(params_blind.get("frequency_GHz", 0.0) or 0.0),
                                       format="%.4f", key="blind_freq")
        zone_blind = cc2.selectbox("Radio-climatic zone", list(prop.ZONES),
                                    index=(list(prop.ZONES).index(params_blind.get("zone"))
                                           if params_blind.get("zone") in prop.ZONES else 0),
                                    key="blind_zone")
        p_percent_blind = cc3.number_input("Time percentage p (%)", value=float(params_blind.get("p_percent", 20.0) or 20.0),
                                            format="%.4f", key="blind_p_percent",
                                            help="Must be >= 1% - the underlying Eq. 29-31 land/sea blend "
                                                 "model goes complex-valued below 1% (see caption below).")

        cc4, cc5, cc6 = st.columns(3)
        dtm_blind = cc4.number_input("dtm — longest continuous land distance (km)",
                                      value=float(params_blind.get("dtm", 0.0) or 0.0),
                                      format="%.4f", key="blind_dtm")
        latitude_blind = cc5.number_input("Earth station latitude (deg)",
                                           value=float(params_blind.get("latitude", 0.0) or 0.0),
                                           format="%.4f", key="blind_latitude")
        is_transmitter_blind = cc6.checkbox("Earth station is a transmitter",
                                             value=bool(params_blind.get("is_transmitter", True)),
                                             key="blind_is_transmitter")

        cc7, cc8 = st.columns(2)
        dlm_blind = cc7.number_input("dlm — longest continuous inland distance, Zone A2 (km)",
                                      value=float(params_blind.get("dlm", 0.0) or 0.0),
                                      format="%.4f", key="blind_dlm",
                                      help="Only used by the 790 MHz-60 GHz model (Eq. 45-46)")
        dc_blind = cc8.number_input("dc — distance to coast, land stations only (km)",
                                     value=float(params_blind.get("dc", 0.0) or 0.0),
                                     format="%.4f", key="blind_dc")

    if st.button("Calculate Blind I/N", type="primary", key="blind_in_calc"):
        try:
            res = prop.calculate_blind_IN_ratio(
                Pt=st.session_state["pt"], Gt=st.session_state["gt"], Gx=gx,
                Te=te, B=b, test_distance_km=test_distance_km,
                frequency_GHz=freq_blind, p_percent=p_percent_blind, zone=zone_blind,
                dtm_km=dtm_blind, dlm_km=dlm_blind, is_transmitter=is_transmitter_blind,
                latitude_deg=latitude_blind, dc_km=dc_blind,
            )
            res["limit"] = in_limit_blind
            st.session_state["blind_in_result"] = res
            status_ok("Blind I/N calculated.")
        except Exception as e:
            st.session_state["blind_in_result"] = None
            status_err(f"Error: {e}")

    res_blind = st.session_state.get("blind_in_result")
    if res_blind:
        d1, d2, d3, d4 = st.columns(4)
        d1.metric("Path Loss L (dB)", f"{res_blind['L_path_dB']:.2f}")
        d2.metric("Noise Floor N (dBW)", f"{res_blind['N_dBW']:.2f}")
        d3.metric("Interference I (dBW)", f"{res_blind['I_dBW']:.2f}")
        d4.metric("I/N Ratio (dB)", f"{res_blind['IN_ratio_dB']:.2f}")

        if res_blind["IN_ratio_dB"] <= res_blind["limit"]:
            st.success(f"✅ PASS: Blind I/N ({res_blind['IN_ratio_dB']:.2f} dB) is at or below the "
                       f"limit ({res_blind['limit']:.2f} dB) at {test_distance_km:.2f} km.")
        else:
            st.error(f"❌ FAIL: Blind I/N ({res_blind['IN_ratio_dB']:.2f} dB) exceeds the "
                     f"limit ({res_blind['limit']:.2f} dB) at {test_distance_km:.2f} km.")

    st.caption(
        "I = Pt + Gt + Gx − L_path(test_distance), where L_path is the Eq. 29-31 predicted path "
        "loss re-evaluated at the test distance (not the mode 1 required/coordination distance from "
        "Step 5). N = 10·log10(k·Te·B) per Eq. 127. This is a conservative, worst-case screening "
        "check — verify against an official copy of RR Appendix 7 before using it in a real filing."
    )


# ===========================================================================
# Step 7 — Border Analysis & Mitigation
# ===========================================================================
st.divider()
st.header("Step 7 — Border Analysis & Mitigation")
st.caption(
    "For each azimuth in the Step 5 coordination contour, finds the distance to the nearest "
    "foreign country's border (auto-downloaded from Natural Earth), compares it to the required "
    "coordination distance, and — where the contour crosses the border — computes the I/N ratio "
    "there. If I/N fails, sliders let you try mitigations (Pt, satellite longitude, antenna "
    "diameter) and see the effect on the worst azimuth before recomputing the full contour."
)

m1_profile_s8 = _active_contour()   # AP7 S1.6 max(mode 1, mode 2) when mode 2 was computed
m1_diag_s8 = dict(st.session_state.get("mode1_profile_diag") or [])
params_s8 = st.session_state.get("ppd_params")

if not m1_profile_s8:
    st.info(
        "Step 7 needs a full azimuth contour first: go to Step 5, supply a horizon profile "
        "(not just a single azimuth), and click \u201CCalculate coordination distance\u201D."
    )
else:
    _sec_s8 = (params_s8 or {}).get("azimuth_sector")
    if _sec_s8:
        st.caption(f"Analyzing border crossings only for azimuths {_sec_s8['azm_fr']:.1f}\u00b0\u2013"
                   f"{_sec_s8['az_to']:.1f}\u00b0 (the contour's own azimuths).")
    missing_s8 = []
    if st.session_state.get("pt") is None:
        missing_s8.append("Pt (Step 2)")
    if st.session_state.get("gr") is None:
        missing_s8.append("Gx / victim antenna gain (Step 3)")
    if st.session_state.get("final_te") is None or st.session_state.get("final_b") is None:
        missing_s8.append("Te and B (Step 3)")
    if not params_s8:
        missing_s8.append("propagation parameters (Step 5)")
    elif params_s8.get("latitude") is None or params_s8.get("longitude") is None:
        missing_s8.append("earth station latitude/longitude (Step 5)")

    if missing_s8:
        st.warning("Complete the following first: " + ", ".join(missing_s8))
    else:
        es_lat = params_s8["latitude"]
        es_lon = params_s8["longitude"]

        # I/N limit derivation (Pr(p) - N), per ITU-R S.1327 / main-body Eq. 1-2:
        # -10 dB is only a generic Fixed-Service default. The correct,
        # scenario-specific limit is the victim's own permissible-interference
        # threshold Pr(p) (Step 3) minus its noise floor N = 10*log10(k*Te*B)
        # (same Te/B used everywhere else in this app for I/N checks). This
        # reuses calculate_IN_ratio(), the function that used to live in the
        # old standalone "Step 6" before it was folded into Step 6 above.
        if st.session_state.get("pr_p") is None:
            missing_s8.append("Pr(p) (Step 3)")

        if missing_s8:
            st.warning("Complete the following first: " + ", ".join(missing_s8))
            st.stop()

        derived = prop.calculate_IN_ratio(
            st.session_state["pr_p"], st.session_state["final_te"], st.session_state["final_b"]
        )
        derived_limit_s8 = derived["IN_ratio_dB"]

        limit_mode_s8 = st.radio(
            "I/N limit",
            ["Derived from Pr(p) (recommended)", "Manual override"],
            key="s8_limit_mode", horizontal=True,
            help="Derived limit = Pr(p) − N, where N = 10·log10(k·Te·B) uses the Step 3 Te/B "
                 "(the same noise floor used in the I/N calc below). This is the scenario-correct "
                 "limit for the sharing case you picked in Step 3 — not the generic -10 dB "
                 "ITU-R S.1327 Fixed-Service default.",
        )
        if limit_mode_s8.startswith("Derived"):
            limit_s8 = derived_limit_s8
            st.info(
                f"Derived I/N limit = Pr(p) − N = {st.session_state['pr_p']:.2f} dBW − "
                f"({derived['N_dBW']:.2f} dBW) = **{limit_s8:.2f} dB**"
            )
        else:
            limit_s8 = st.number_input("I/N Limit (dB)", value=float(derived_limit_s8), format="%.2f",
                                        key="s8_in_limit_manual",
                                        help="Manually overriding the Pr(p)-derived limit above.")

        home_country = st.text_input("Home country name", value=st.session_state.get("border_home_country", "Azerbaijan"),
                                      key="border_home_country",
                                      help="Excluded from the border search (matched case-insensitively "
                                           "against Natural Earth's NAME/NAME_LONG/ADMIN fields).")

        with st.expander("Terrain diffraction (ITU-R P.526) — recheck FAIL azimuths against a DEM", expanded=False):
            st.caption(
                "For any azimuth that FAILS its I/N check under plain FSPL + gaseous absorption, "
                "optionally re-evaluate the path loss with an added ITU-R P.526 single knife-edge "
                "diffraction term computed from a DEM. A real mountain/terrain obstruction between "
                "the earth station and the border can add enough extra loss to turn a FAIL into a "
                "PASS — this does not change the original I/N number, it adds a separate, clearly "
                "labeled 'with diffraction' result alongside it."
            )
            diffraction_enabled_s8 = st.checkbox(
                "Check terrain diffraction on FAIL azimuths", value=True, key="s8_diffraction_enabled",
                disabled=_GEOD is None,
                help=None if _GEOD is not None else
                     "pyproj is not installed — needed to locate the border point for the DEM lookup.",
            )
            dem_path_s8 = st.text_input(
                "DEM GeoTIFF path", value=st.session_state.get("s8_dem_path", DEM_TIFF_PATH),
                key="s8_dem_path", disabled=not diffraction_enabled_s8,
                help="Same DEM used by Step 9's hillshade overlay. If it's missing or unreadable, "
                     "the diffraction recheck is silently skipped for that azimuth (falls back to "
                     "FSPL + gases only) rather than erroring out the whole analysis.",
            )
            dc1, dc2 = st.columns(2)
            es_height_s8 = dc1.number_input(
                "Earth station antenna height above ground (m)", value=10.0, min_value=0.0,
                step=1.0, format="%.1f", key="s8_es_height_m", disabled=not diffraction_enabled_s8,
            )
            target_height_s8 = dc2.number_input(
                "Border point reference height above ground (m)", value=0.0, min_value=0.0,
                step=1.0, format="%.1f", key="s8_target_height_m", disabled=not diffraction_enabled_s8,
                help="Height of the point being checked at the border. 0 m is the conservative "
                     "default for a screening check with no specific victim station known.",
            )

        if st.button("Analyze borders", type="primary", key="s8_analyze"):
            azimuths_s8 = [az for az, _ in m1_profile_s8]
            try:
                border_km_by_az = borders.find_border_distances(
                    lat=es_lat, lon=es_lon, azimuths_deg=azimuths_s8,
                    home_country_name=home_country, max_distance_km=2000.0,
                )
                st.session_state["border_analysis_warning"] = None
            except (ImportError, RuntimeError) as e:
                border_km_by_az = {az: st.session_state.get("border_manual_km", {}).get(az) for az in azimuths_s8}
                st.session_state["border_analysis_warning"] = str(e)

            rows = []
            for az, required_km in m1_profile_s8:
                border_km = border_km_by_az.get(az)
                diag = m1_diag_s8.get(az, {})
                gt_alpha = diag.get("Gt_alpha_dBi", st.session_state.get("gt"))
                overlap = border_km is not None and required_km > border_km
                in_row = None
                diffraction_result = None
                if overlap:
                    try:
                        # Get the horizon profile for this azimuth
                        profile_s8 = params_s8.get("horizon_profile") or []
                        eps_h_border = next((e for a, e, _ in profile_s8 if a == az), 0.0)
                        dh_border = next((d for a, _, d in profile_s8 if a == az), 0.5)
                        
                        in_row = prop.calculate_IN_at_distance(
                            Pt=st.session_state["pt"], 
                            Gt_dBi=gt_alpha, 
                            Gx_dBi=st.session_state["gr"],
                            Te_K=st.session_state["final_te"], 
                            B_Hz=st.session_state["final_b"],
                            distance_km=border_km, 
                            frequency_GHz=params_s8["frequency_GHz"],
                            p_percent=params_s8["p_percent"], 
                            zone=params_s8["zone"],
                            dtm_km=params_s8["dtm"], 
                            dlm_km=params_s8.get("dlm", 0.0),  # ADD THIS
                            is_transmitter=params_s8["is_transmitter"],
                            latitude_deg=params_s8["latitude"],
                            dc_km=params_s8.get("dc", 0.0),  # ADD THIS
                            horizon_elevation_deg=eps_h_border,  # ADD THIS
                            horizon_distance_km=dh_border,  # ADD THIS
                        )

                        debug_msg = f"""
                        **Debug Info for Azimuth {az}°:**
                        - Distance: {border_km:.2f} km
                        - Frequency: {params_s8['frequency_GHz']} GHz
                        - Pt: {st.session_state['pt']:.2f} dBW
                        - Gt: {gt_alpha:.2f} dBi
                        - Gx: {st.session_state['gr']:.2f} dBi
                        - Path Loss: {in_row['L_path_dB']:.2f} dB
                        - Noise Floor: {in_row['N_dBW']:.2f} dBW
                        - Interference: {in_row['I_dBW']:.2f} dBW
                        - I/N: {in_row['IN_ratio_dB']:.2f} dB
                        """

                        # --- Terrain diffraction recheck (ITU-R P.526) -----
                        # Only bother for azimuths that actually FAIL the plain
                        # FSPL+gases I/N check — a PASS needs no help from
                        # terrain, and running the DEM lookup on every azimuth
                        # would be needless work.
                        is_failing = in_row["IN_ratio_dB"] > limit_s8
                        if is_failing and diffraction_enabled_s8 and _GEOD is not None:
                            border_lon, border_lat, _ = _GEOD.fwd(es_lon, es_lat, az, border_km * 1000.0)
                            diff_calc = prop.calculate_path_loss_with_diffraction(
                                frequency_GHz=params_s8["frequency_GHz"],
                                distance_km=border_km,
                                latitude_deg=params_s8["latitude"],
                                es_lat=es_lat, es_lon=es_lon, es_height_m=es_height_s8,
                                target_lat=border_lat, target_lon=border_lon, target_height_m=target_height_s8,
                                dem_path=dem_path_s8,
                            )
                            adjusted_I_dBW = st.session_state["pt"] + gt_alpha + st.session_state["gr"] - diff_calc["L_path_dB"]
                            adjusted_IN_dB = adjusted_I_dBW - in_row["N_dBW"]
                            diffraction_result = {
                                "diffraction_available": diff_calc["diffraction_available"],
                                "diffraction_loss_dB": diff_calc["diffraction_loss_dB"],
                                "diffraction_warning": diff_calc["diffraction_warning"],
                                "base_loss_dB": diff_calc["base_loss_dB"],
                                "adjusted_L_path_dB": diff_calc["L_path_dB"],
                                "adjusted_I_dBW": adjusted_I_dBW,
                                "adjusted_IN_ratio_dB": adjusted_IN_dB,
                                "adjusted_status": "PASS" if adjusted_IN_dB <= limit_s8 else "FAIL",
                                "border_lat": border_lat, "border_lon": border_lon,
                            }
                            if diff_calc["diffraction_available"]:
                                turned_pass = diffraction_result["adjusted_status"] == "PASS"
                                debug_msg += f"""
                        **Terrain diffraction recheck (ITU-R P.526):**
                        - Diffraction loss: {diff_calc['diffraction_loss_dB']:.2f} dB
                        - Path loss with diffraction: {diff_calc['L_path_dB']:.2f} dB
                        - I/N with diffraction: {adjusted_IN_dB:.2f} dB
                        - Result: {"✅ PASS (diffraction helped)" if turned_pass else "❌ still FAIL"}
                        """
                            else:
                                debug_msg += f"""
                        **Terrain diffraction recheck:** not applied — {diff_calc['diffraction_warning']}
                        """

                        st.warning(debug_msg)
                    except Exception as e:
                        in_row = {"error": str(e)}
                rows.append({
                    "azimuth_deg": az, "required_km": required_km, "border_km": border_km,
                    "overlap": overlap, "Gt_alpha_dBi": gt_alpha, "in_result": in_row,
                    # Needed by Step 9's map (streamlit_map_component.py) to color
                    # PASS/FAIL sectors — the dataframe below already computes this
                    # limit inline via _status(), but never previously persisted it.
                    "limit_db": limit_s8,
                    # ITU-R P.526 knife-edge diffraction recheck (see above) —
                    # None unless this azimuth failed and the recheck was
                    # enabled/attempted. Never overwrites in_result; it's a
                    # separate, clearly-labeled "what if a mountain helps"
                    # result shown alongside the original I/N.
                    "diffraction_result": diffraction_result,
                })
            st.session_state["border_analysis_rows"] = rows

        warn_s8 = st.session_state.get("border_analysis_warning")
        if warn_s8:
            st.warning(
                f"Automatic border download unavailable: {warn_s8}\n\n"
                "You can still analyze borders manually: enter a border distance (km) per azimuth "
                "below, then click \u201CAnalyze borders\u201D again to use them."
            )
            azimuths_s8 = [az for az, _ in m1_profile_s8]
            with st.expander("Manual border distance entry (km) — used when auto-download fails"):
                manual = dict(st.session_state.get("border_manual_km", {}))
                cols = st.columns(4)
                for i, az in enumerate(azimuths_s8):
                    val = cols[i % 4].number_input(
                        f"{az}\u00b0", value=float(manual.get(az) or 0.0), min_value=0.0,
                        format="%.2f", key=f"s8_manual_{az}",
                    )
                    manual[az] = val if val > 0 else None
                st.session_state["border_manual_km"] = manual

        rows_s8 = st.session_state.get("border_analysis_rows")
        if rows_s8:
            def _status(r):
                if not r["overlap"]:
                    return "— (no overlap)" if r["border_km"] is not None else "— (no neighbor found)"
                if r["in_result"] is None or "error" in r["in_result"]:
                    return "⚠️ error"
                return "❌ FAIL" if r["in_result"]["IN_ratio_dB"] > limit_s8 else "✅ PASS"

            def _effective_status(r):
                """Same as _status(), but if a terrain-diffraction recheck
                turned this azimuth's FAIL into a PASS, reflect that here —
                this is what fail_rows/the mitigation panel below act on."""
                base = _status(r)
                diff = r.get("diffraction_result")
                if base == "❌ FAIL" and diff and diff.get("diffraction_available"):
                    return "✅ PASS (diffraction)" if diff["adjusted_status"] == "PASS" else "❌ FAIL"
                return base

            st.dataframe(
                [
                    {
                        "Azimuth (°)": r["azimuth_deg"],
                        "Required dist (km)": round(r["required_km"], 3),
                        "Border dist (km)": round(r["border_km"], 3) if r["border_km"] is not None else None,
                        "Overlap?": "Yes" if r["overlap"] else "No",
                        "I/N at border (dB)": (round(r["in_result"]["IN_ratio_dB"], 2)
                                                if r["in_result"] and "IN_ratio_dB" in r["in_result"] else None),
                        "Limit (dB)": limit_s8,
                        "Status": _status(r),
                        "Diffraction loss (dB)": (
                            round(r["diffraction_result"]["diffraction_loss_dB"], 2)
                            if r.get("diffraction_result") and r["diffraction_result"]["diffraction_available"]
                            else None
                        ),
                        "I/N with diffraction (dB)": (
                            round(r["diffraction_result"]["adjusted_IN_ratio_dB"], 2)
                            if r.get("diffraction_result") and r["diffraction_result"]["diffraction_available"]
                            else None
                        ),
                        "Effective status": _effective_status(r),
                    }
                    for r in rows_s8
                ],
                use_container_width=True,
            )
            if any(r.get("diffraction_result") for r in rows_s8):
                st.caption(
                    "\"Effective status\" reflects the ITU-R P.526 terrain diffraction recheck where it "
                    "was run: an azimuth that FAILs on plain FSPL + gases but PASSes once the extra "
                    "diffraction loss from a DEM-detected obstruction is added shows as \"✅ PASS "
                    "(diffraction)\". The mitigation panel below reacts to this effective status."
                )

            fail_rows = [r for r in rows_s8 if _effective_status(r) == "❌ FAIL"]

            if not fail_rows:
                st.success("No azimuth both crosses the border and fails the I/N limit.")
            else:
                worst = max(fail_rows, key=lambda r: r["in_result"]["IN_ratio_dB"])
                st.session_state["border_mitigation_az"] = worst["azimuth_deg"]

                st.divider()
                st.subheader("Mitigation panel")
                margin = worst["in_result"]["IN_ratio_dB"] - limit_s8
                st.error(
                    f"Worst azimuth: **{worst['azimuth_deg']}\u00b0** — Current I/N: "
                    f"**{worst['in_result']['IN_ratio_dB']:.2f} dB** (Limit: {limit_s8:.2f} dB) "
                    f"❌ FAIL by {margin:.2f} dB"
                )

                gt_geometry_s8 = st.session_state.get("gt_geometry")
                if not gt_geometry_s8:
                    st.info(
                        "Satellite-longitude and antenna-diameter mitigation require Gt to have been "
                        "produced via Step 1's Eq. 84-97 geometry calculator (so φ/G(φ) can be "
                        "recomputed). Only the Pt slider is available with a manually-entered Gt."
                    )

                pt_current = st.session_state["pt"]
                sat_lon_current = st.session_state.get("step1_sat_lon", 0.0) or 0.0
                dl_current = (gt_geometry_s8 or {}).get("d_over_lambda") or 1.0
                wavelength_s8 = (gt_geometry_s8 or {}).get("wavelength")
                diameter_current = (gt_geometry_s8 or {}).get("diameter")
                # If the original Gt geometry didn't record a real
                # wavelength/diameter (e.g. D/lambda was entered directly, or
                # estimated from Gmax), fall back to a documented nominal
                # baseline diameter so the D slider still has something
                # physical to scale d_over_lambda from. This is an
                # approximation - see the caption below.
                diameter_baseline = diameter_current if diameter_current else 3.0
                if wavelength_s8 is None and dl_current:
                    wavelength_s8 = diameter_baseline / dl_current  # back out an implied lambda

                mc1, mc2, mc3 = st.columns(3)
                pt_slider = mc1.slider("Transmit power Pt (dBW)", min_value=pt_current - 20.0,
                                        max_value=pt_current + 5.0, value=pt_current, step=0.1,
                                        key="s8_mit_pt")
                sat_lon_slider = mc2.slider("Satellite orbital longitude δ (deg)", min_value=-180.0,
                                             max_value=180.0, value=float(sat_lon_current), step=0.1,
                                             key="s8_mit_satlon", disabled=not gt_geometry_s8)
                d_slider = mc3.slider("Antenna diameter D (m)", min_value=1.0, max_value=15.0,
                                       value=float(min(max(diameter_baseline, 1.0), 15.0)), step=0.1,
                                       key="s8_mit_d", disabled=not gt_geometry_s8)

                # --- live recompute for the worst azimuth only ---
                gt_new = worst["Gt_alpha_dBi"]
                phi_new = None
                if gt_geometry_s8:
                    try:
                        es_lon_saved = st.session_state.get("step1_es_lon", 0.0) or 0.0
                        new_delta0 = sat_lon_slider - es_lon_saved
                        new_dl = d_slider / wavelength_s8 if wavelength_s8 else dl_current
                        geometry_trial = dict(gt_geometry_s8)
                        geometry_trial["delta0"] = new_delta0
                        # eps_h at the worst azimuth comes from the same horizon
                        # profile Step 5 used (profile is [(az, eps_h, dh), ...]).
                        profile_s8 = params_s8.get("horizon_profile") or []
                        eps_h_worst = next((e for a, e, _ in profile_s8 if a == worst["azimuth_deg"]), 0.0)
                        gt_new, phi_new = _gt_at_azimuth(geometry_trial, worst["azimuth_deg"], eps_h_worst)
                    except Exception as e:
                        st.warning(f"Could not recompute Gt(\u03c6) for the slider values: {e}")

                # Path loss/N don't depend on Pt/Gt/D, so reuse the already-computed
                # L_path_dB/N_dBW for this azimuth's border distance instead of
                # recomputing the whole propagation model on every slider move.
                L_path_worst = worst["in_result"]["L_path_dB"]
                N_worst = worst["in_result"]["N_dBW"]
                I_new = pt_slider + gt_new + st.session_state["gr"] - L_path_worst
                IN_new = I_new - N_worst

                lc1, lc2, lc3 = st.columns(3)
                lc1.metric("Gt(\u03c6) at worst azimuth (dBi)", f"{gt_new:.2f}",
                           delta=f"{gt_new - worst['Gt_alpha_dBi']:.2f}" if gt_geometry_s8 else None)
                lc2.metric("I/N at worst azimuth (dB)", f"{IN_new:.2f}",
                           delta=f"{IN_new - worst['in_result']['IN_ratio_dB']:.2f}")
                lc3.metric("Margin vs limit (dB)", f"{limit_s8 - IN_new:.2f}")

                if IN_new <= limit_s8:
                    st.success(f"✅ With these settings, the worst azimuth ({worst['azimuth_deg']}\u00b0) would PASS.")
                else:
                    st.error(f"❌ Still FAILs by {IN_new - limit_s8:.2f} dB at the worst azimuth ({worst['azimuth_deg']}\u00b0).")

                    if st.button("Apply mitigation and recompute contour", type="primary", key="s8_apply"):
                        # 1. Save the slider values to session state so they persist
                        st.session_state["pt"] = pt_slider
                        st.session_state["s8_applied_pt"] = pt_slider
                        st.session_state["s8_applied_sat_lon"] = sat_lon_slider
                        st.session_state["s8_applied_diameter"] = d_slider
                        
                        if gt_geometry_s8:
                            st.session_state["step1_sat_lon"] = sat_lon_slider
                            new_geometry = dict(gt_geometry_s8)
                            new_geometry["delta0"] = sat_lon_slider - (st.session_state.get("step1_es_lon", 0.0) or 0.0)
                            new_geometry["d_over_lambda"] = d_slider / wavelength_s8 if wavelength_s8 else dl_current
                            new_geometry["diameter"] = d_slider
                            if wavelength_s8:
                                new_geometry["wavelength"] = wavelength_s8
                            st.session_state["gt_geometry"] = new_geometry

                        # 2. Automatically recompute the contour (Step 5 logic)
                        success = _recompute_contour()
                        
                        # 3. Clear downstream results so they don't show stale data
                        st.session_state["border_analysis_rows"] = None
                        st.session_state["blind_in_result"] = None
                        
                        if success:
                            st.success(f"✅ **Contour automatically recomputed with mitigated parameters:** Pt={pt_slider:.2f} dBW, Satellite Longitude={sat_lon_slider:.2f}°, Antenna Diameter={d_slider:.2f} m. Please click 'Analyze borders' below to verify the new I/N.")
                        else:
                            st.error("Failed to recompute contour. Check Step 5 parameters.")
                            
                        st.rerun()

                st.caption(
                    "If the saved Gt geometry didn't record a real wavelength/diameter (D/\u03bb was entered "
                    "directly, or estimated from Gmax in Step 1), the antenna-diameter slider falls back to "
                    "an assumed baseline diameter of 3 m to back out an implied \u03bb — re-enter Gt in Step 1 "
                    "using explicit λ and D if you need this slider to reflect a real antenna."
                )

st.caption(
    "TIER B / screening-only: Natural Earth's Admin 0 - Countries dataset is a small/medium-scale "
    "cartographic product, not a legal definition of national boundaries, and does not resolve "
    "disputed borders. This border-crossing check, and the I/N figures computed at the resulting "
    "distances, are a planning aid only — actual coordination requires official ITU procedures and "
    "each administration's own recognized border geometry. Verify against an official copy of RR "
    "Appendix 7 and authoritative boundary data before using these results in a real filing."
)



st.header("Step 8 — Border Analysis & Mitigation")
st.caption(
    "For each azimuth in the Step 5 coordination contour, finds the distance to the nearest "
    "foreign country's border..."
)

# --- NEW: Show currently active parameters ---

# NOTE: st.session_state.get(key, default) only falls back to `default`
# when `key` is *missing* — if the stored value is explicitly None (e.g.
# no mitigation has been applied yet, or gt_geometry has no diameter on
# record), .get() still returns None, and the f"{...:.2f}" formatting
# below then raises TypeError. Guard each one with `or 0.0` so a stored
# None is also treated as "not set yet".
active_pt = st.session_state.get("s8_applied_pt") or st.session_state.get("pt") or 0.0
active_sat = st.session_state.get("s8_applied_sat_lon") or st.session_state.get("step1_sat_lon") or 0.0
active_diam = (
    st.session_state.get("s8_applied_diameter")
    or (st.session_state.get("gt_geometry") or {}).get("diameter")
    or 0.0
)

st.info(f"📡 **Currently Active Parameters for Contour Calculation:** "
        f"Transmit Power (Pt) = **{active_pt:.2f} dBW** | "
        f"Satellite Longitude = **{active_sat:.2f}°** | "
        f"Antenna Diameter = **{active_diam:.2f} m**")
# --- END NEW ---

m1_profile_s8 = _active_contour()   # AP7 S1.6 max(mode 1, mode 2) when mode 2 was computed
# ... rest of the code ...

# ===========================================================================
# Step 9 — Map Visualization
# ===========================================================================
st.header("Step 9 — Map Visualization")
st.caption(
    "Interactive map of the earth station, the Step 5 coordination contour, and — once "
    "Step 8's border analysis has run — the per-azimuth PASS/FAIL interference sectors. "
    "An optional DEM hillshade layer can be toggled on if a local GeoTIFF is available."
)

_ppd_params_s9 = st.session_state.get("ppd_params")
_mode1_profile_s9 = _active_contour()   # AP7 S1.6 max(mode 1, mode 2) when mode 2 was computed
_border_rows_s9 = st.session_state.get("border_analysis_rows")

if not _ppd_params_s9 or not _mode1_profile_s9:
    st.info(
        "Step 9 needs Step 5's coordination distance results first (a full azimuth "
        "contour, not just a single azimuth)."
    )
else:
    map_ui.render_coordination_map(
        st,
        ppd_params=_ppd_params_s9,
        mode1_profile_result=_mode1_profile_s9,
        border_analysis_rows=_border_rows_s9,
        dem_path=DEM_TIFF_PATH,
    )