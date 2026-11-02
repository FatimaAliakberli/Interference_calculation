#!/usr/bin/env python3
"""
core/streamlit_map_component.py — Streamlit glue between the Link Budget
Calculator wizard (app.py) and map_visualization.py.

This module knows nothing about ITU-R math itself — it only reads the
already-computed results sitting in app.py's session state (Step 5's
coordination-distance profile, Step 8's border/I-N analysis rows) and
turns them into a folium map via map_visualization.py, then renders that
map inline with st.components.v1.html and offers a download button for
the standalone HTML.

Expected shapes of the arguments (as produced by app.py):

    ppd_params (dict, from st.session_state["ppd_params"]):
        Must contain at least "latitude" and "longitude" (the earth
        station's coordinates, decimal degrees).

    mode1_profile_result (list[tuple], from
    st.session_state["mode1_profile_result"]):
        [(azimuth_deg, required_distance_km), ...] — one entry per
        azimuth in the Step 5 horizon profile. Used to draw the
        coordination contour.

    border_analysis_rows (list[dict], from
    st.session_state["border_analysis_rows"]):
        [{"azimuth_deg", "required_km", "border_km", "overlap",
          "Gt_alpha_dBi", "in_result", "limit_db"(optional)}, ...]
        as built in Step 8. "in_result" is either None, {"error": ...},
        or a dict containing "IN_ratio_dB" (see
        propagation_calculator.calculate_IN_at_distance). If a row does
        not carry a "limit_db" key (the stock Step 8 code currently
        doesn't store one — see the app.py integration notes), that
        row is skipped for PASS/FAIL coloring since there is nothing to
        compare the I/N ratio against.

Dependencies: folium (via map_visualization.py), and everything
map_visualization.py itself needs (pyproj, numpy, rasterio, matplotlib
for the optional DEM overlay). All are imported lazily inside the
functions below so simply importing this module never fails even if a
dependency is missing — the failure is surfaced as a Streamlit warning
at render time instead of a crash at import time.
"""

from typing import Any, Dict, List, Optional, Tuple


def _import_map_visualization():
    """Import map_visualization.py, supporting both `core.map_visualization`
    (package import, matching the rest of core/) and a bare top-level
    import (e.g. if this file is ever run/imported standalone)."""
    try:
        from core import map_visualization as mv
    except ImportError:
        import map_visualization as mv
    return mv


def _build_zone_results(border_analysis_rows: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], int]:
    """
    Convert Step 8's border_analysis_rows into the list-of-dicts shape
    map_visualization.add_interference_zones() expects:
        {azimuth, border_distance_km, required_distance_km,
         in_ratio_db, limit_db, status,
         diffraction_loss_db, diffraction_status (optional)}

    Rows without a usable border distance, a computed I/N result, or a
    known limit_db are skipped (skip_count is returned so the caller can
    tell the user why some azimuths didn't get a colored sector).

    If a row carries a "diffraction_result" (Step 7's ITU-R P.526
    terrain-diffraction recheck, run for azimuths that FAILed the plain
    FSPL+gases I/N check — see app.py), and that recheck actually
    produced a usable value, the extra "diffraction_loss_db" and
    "diffraction_status" keys are attached so map_visualization can note
    it in the popup. The sector's own color/status ("status" below)
    intentionally still reflects the ORIGINAL (pre-diffraction) I/N — the
    diffraction outcome is shown as an annotation alongside it, not a
    silent recoloring, so the map and the Step 7 table stay legible
    together.
    """
    zone_results = []
    skipped = 0
    for row in border_analysis_rows or []:
        in_res = row.get("in_result")
        limit_db = row.get("limit_db")
        border_km = row.get("border_km")

        if border_km is None or limit_db is None or not in_res or "IN_ratio_dB" not in in_res:
            skipped += 1
            continue

        in_ratio = in_res["IN_ratio_dB"]
        status = "FAIL" if in_ratio > limit_db else "PASS"
        zone = {
            "azimuth": row["azimuth_deg"],
            "border_distance_km": border_km,
            "required_distance_km": row.get("required_km", border_km),
            "in_ratio_db": in_ratio,
            "limit_db": limit_db,
            "status": status,
        }

        diff_res = row.get("diffraction_result")
        if diff_res and diff_res.get("diffraction_available"):
            zone["diffraction_loss_db"] = diff_res["diffraction_loss_dB"]
            zone["diffraction_in_ratio_db"] = diff_res["adjusted_IN_ratio_dB"]
            zone["diffraction_status"] = diff_res["adjusted_status"]

        zone_results.append(zone)
    return zone_results, skipped


def render_coordination_map(
    st,
    ppd_params: Optional[Dict[str, Any]],
    mode1_profile_result: Optional[List[Tuple[float, float]]],
    border_analysis_rows: Optional[List[Dict[str, Any]]],
    dem_path: Optional[str] = None,
) -> None:
    """
    Render an interactive coordination map inline in a Streamlit app.

    Draws, on a folium basemap centered on the earth station:
      - the earth station marker
      - country borders (optional layer, toggleable)
      - the Step 5 coordination contour (per-azimuth required distance)
      - Step 8 interference sectors (green=PASS / red=FAIL), with a
        dashed reference arc at the required distance and a popup with
        full I/N detail per azimuth, plus a legend
      - an optional DEM hillshade overlay (only if dem_path is given and
        readable)

    This function renders directly (via st.components.v1.html and other
    st.* calls) and returns nothing. Safe to call with partial/None
    results — it will show an st.info/st.warning explaining what's
    missing instead of raising.

    Args:
        st: The Streamlit module (passed in, as this file avoids a
            hard `import streamlit as st` so it stays importable/testable
            without Streamlit installed).
        ppd_params: st.session_state["ppd_params"] from Step 5 (must have
            "latitude"/"longitude").
        mode1_profile_result: st.session_state["mode1_profile_result"]
            from Step 5 — list of (azimuth_deg, required_distance_km).
        border_analysis_rows: st.session_state["border_analysis_rows"]
            from Step 8 — list of per-azimuth border/I-N result dicts.
        dem_path: Optional path to a DEM GeoTIFF (e.g.
            "data_sources/azerbaijan_dem.tif") for the hillshade overlay
            toggle. If None or unreadable, the DEM toggle is disabled.
    """
    if not ppd_params:
        st.info("Run Step 5 (coordination distance) first — no earth station location yet.")
        return
    if not mode1_profile_result:
        st.info(
            "Run Step 5 (coordination distance) with a full horizon profile first — "
            "no per-azimuth contour to plot yet."
        )
        return

    es_lat = ppd_params.get("latitude")
    es_lon = ppd_params.get("longitude")
    if es_lat is None or es_lon is None:
        st.warning("Earth station latitude/longitude missing from Step 5's parameters.")
        return

    try:
        mv = _import_map_visualization()
    except ImportError as e:
        st.error(
            f"Map visualization is unavailable — missing dependency: {e}\n\n"
            "Install with `pip install folium pyproj numpy rasterio matplotlib`."
        )
        return

    import os
    dem_available = bool(dem_path) and os.path.isfile(dem_path)

    # --- controls -----------------------------------------------------
    c1, c2, c3 = st.columns(3)
    zoom = c1.slider("Zoom level", min_value=3, max_value=14, value=7, key="s9_zoom")
    show_borders = c2.checkbox("Show country borders layer", value=True, key="s9_show_borders")
    show_dem = c3.checkbox(
        "Show DEM hillshade overlay", value=False,
        disabled=not dem_available, key="s9_show_dem",
        help=None if dem_available else f"DEM not found at {dem_path!r}.",
    )
    dem_opacity = 0.5
    if show_dem and dem_available:
        dem_opacity = st.slider("DEM overlay opacity", 0.0, 1.0, 0.5, 0.05, key="s9_dem_opacity")

    with st.spinner("Building interactive coordination map..."):
        try:
            m = mv.create_base_map(es_lat, es_lon, zoom=zoom)
        except Exception as e:
            st.error(f"Could not build the base map: {e}")
            return

        if show_borders:
            try:
                m = mv.add_country_borders(m)
            except Exception as e:
                st.caption(f"(Country borders layer unavailable: {e})")

        # --- Step 5 coordination contour ---
        distances_by_az = {
            az: dist for az, dist in mode1_profile_result
            if dist is not None
        }
        if distances_by_az:
            try:
                m = mv.add_coordination_contour(
                    m, es_lat, es_lon, dict(sorted(distances_by_az.items())),
                    color="red", line_style="dashed",
                )
            except Exception as e:
                st.caption(f"(Coordination contour could not be drawn: {e})")
        else:
            st.caption("No valid per-azimuth required distances to plot yet.")

        # --- Step 8 interference sectors ---
        if border_analysis_rows:
            zone_results, skipped = _build_zone_results(border_analysis_rows)
            if zone_results:
                try:
                    m = mv.add_interference_zones(m, zone_results, es_lat, es_lon)
                    m.get_root().html.add_child(
                        mv.folium.Element(mv.create_interference_legend())
                    )
                except Exception as e:
                    st.caption(f"(Interference zones could not be drawn: {e})")
                if skipped:
                    st.caption(
                        f"{skipped} azimuth(s) skipped in the interference overlay "
                        "(no border crossing, no computed I/N, or no limit recorded for that row)."
                    )
            else:
                st.caption(
                    "Border analysis has run, but no azimuth yet has both a border crossing "
                    "and a limit_db value to compare against — see the app.py Step 8 note about "
                    "adding `\"limit_db\": limit_s8` to each row."
                )
        else:
            st.caption("Run \u201CAnalyze borders\u201D in Step 8 to overlay PASS/FAIL interference sectors.")

        # --- optional DEM hillshade ---
        if show_dem and dem_available:
            try:
                m = mv.add_dem_overlay(m, dem_path, es_lat, es_lon, opacity=dem_opacity)
            except Exception as e:
                st.warning(f"DEM overlay could not be added: {e}")

    try:
        map_html = m.get_root().render()
    except Exception as e:
        st.error(f"Could not render the map: {e}")
        return

    st.components.v1.html(map_html, height=600, scrolling=False)

    download_map_button(st, m)


def download_map_button(st, map_obj, filename: str = "coordination_map.html") -> None:
    """
    Render an st.download_button that lets the user save the current
    folium map as a standalone, self-contained interactive HTML file
    (openable in any browser, no Streamlit required).

    Args:
        st: The Streamlit module.
        map_obj: A folium.Map instance (e.g. as returned/built inside
            render_coordination_map).
        filename: Download filename offered to the user. Defaults to
            "coordination_map.html".

    Returns:
        None. Renders the button directly; does nothing if map_obj can't
        be serialized (shows an st.error instead of raising).
    """
    try:
        html_bytes = map_obj.get_root().render().encode("utf-8")
    except Exception as e:
        st.error(f"Could not prepare the map for download: {e}")
        return

    st.download_button(
        label="\u2b07\ufe0f Download interactive map (HTML)",
        data=html_bytes,
        file_name=filename,
        mime="text/html",
        key=f"s9_download_{filename}",
    )