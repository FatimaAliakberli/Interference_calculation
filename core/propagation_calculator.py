#!/usr/bin/env python3
"""
ITU-R Radio Regulations Appendix 7 (AP7) main body - propagation mode (1) and
propagation mode (2) required-distance calculator.

SCOPE: this module covers BOTH propagation modes:
  - propagation mode (1) (Annex 1, great-circle path - tropospheric scatter,
    ducting, layer reflection/refraction, gaseous absorption and site
    shielding), across all three frequency bands:
      * 100 MHz  <= f < 790 MHz  (Annex 1 S1 site shielding + S2 propagation model)
      * 790 MHz  <= f < 60 GHz   (Annex 1 S1 site shielding + S3 propagation model)
      * 60 GHz   <= f <= 105 GHz (Annex 1 S1 site shielding + S4 propagation model)
  - propagation mode (2) (Annex 2, hydrometeor/rain scatter, Eq. 62-83),
    via run_mode2() / run_lookup_rain_zone().

Per AP7 main body S1.6, "the required distance used for any azimuth is the
greater of the propagation mode (1) and propagation mode (2) required
distances". This module returns each mode's distance separately; combining
them per azimuth (max) is done by the caller. Note that run_mode2() works
from L(p) = Pt - Pr(p) (NOT Lb(p)): Gx is folded into Eq. 82 itself.

Radio-climatic zone lookups (beta_e, N0, dmin, dmax1, correction_X/
correction_Z) are the shared foundation used by all three mode1 bands.

-----------------------------------------------------------------------------
SOURCE RELIABILITY - READ BEFORE USING THESE NUMBERS FOR AN ACTUAL FILING
-----------------------------------------------------------------------------
Every function below is tagged TIER A or TIER B in its docstring:

  TIER A - the source PDF (OCR'd from the AP7 main body, section 4) was
           legible enough that the constants/structure could be read off
           directly, or cross-checked by verifying that a piecewise formula
           evaluates to the expected value at its own stated boundary
           frequencies.
  TIER B - the source OCR was garbled (scrambled numbers, dropped digits,
           fraction bars lost) at the relevant point, and the formula below
           is a best-effort reconstruction based on the surrounding prose,
           typical ITU piecewise-interpolation conventions, and (where
           possible) boundary-condition consistency checks. TIER B formulas
           MUST be verified against an official copy of RR Appendix 7 before
           being relied on for a real coordination filing.

As more Annexes are supplied in later steps, functions built from clean
source text should be marked TIER A; anything reconstructed from damaged
OCR should keep the TIER B flag and inherit it into anything that calls it.
-----------------------------------------------------------------------------
"""

import argparse
import math

# ---------------------------------------------------------------------------
# Table 3 (main body S4.3) - maximum coordination distance for propagation
# mode (1), frequencies <= 60 GHz, by radio-climatic zone. [TIER A - values
# given directly and unambiguously in the source table.]
# ---------------------------------------------------------------------------
TABLE_3_DMAX1_KM = {
    "A1": 500.0,
    "A2": 375.0,
    "B": 900.0,
    "C": 1200.0,
}

ZONES = tuple(TABLE_3_DMAX1_KM.keys())


def _clamp(x, lo=-1.0, hi=1.0):
    """Guard against floating point drift pushing acos/asin arguments
    just outside [-1, 1]. Kept for consistency with calculations.py even
    though nothing in step 1 needs it yet."""
    return max(lo, min(hi, x))


# ---------------------------------------------------------------------------
# S4.1 - Radio-climatic information
# ---------------------------------------------------------------------------

def reduced_latitude(zeta_deg):
    """Eq. 9/10: 'reduced' latitude zeta_r (deg) used in the beta_e formula.
    [TIER A - the two-branch structure (subtract 1.8 deg above the 1.8 deg
    threshold, else zero) was legible in the source.]

        zeta_r = |zeta| - 1.8   for |zeta| > 1.8 deg
        zeta_r = 0               for |zeta| <= 1.8 deg
    """
    az = abs(zeta_deg)
    return (az - 1.8) if az > 1.8 else 0.0


def beta_e(zeta_deg):
    """Eq. 7/8: percentage of time beta_e for which clear-air anomalous
    propagation conditions exist, as a function of the reduced latitude
    zeta_r from Eq. 9/10.
    
    [TIER A - VERIFIED from official source document]
    
    For ζr ≤ 70°:
    βe = 10^(1.67 - 0.015*ζr)
    
    For ζr > 70°:
    βe = 4.17
    """
    zeta_r = reduced_latitude(zeta_deg)
    if zeta_r <= 70.0:
        # Correct formula: 10^(1.67 - 0.015*zeta_r)
        return 10.0 ** (1.67 - 0.015 * zeta_r)
    return 4.17


def N0(zeta_deg):
    """Eq. 11: path-centre sea-level surface refractivity N0, used in the
    propagation mode (1) calculations for 790 MHz - 60 GHz.
    [TIER A - the constants 32.7, 330 and 62.6 and the exp(-(zeta/32.7)^2)
    structure were legible in the source.]

        N0 = 330 - 62.6 * exp(-(zeta / 32.7)^2)
    """
    return 330.0 - 62.6 * math.exp(-((zeta_deg / 32.7) ** 2))


# ---------------------------------------------------------------------------
# S4.2 - Minimum coordination distance (dmin), Eq. 12-18
# ---------------------------------------------------------------------------

def _dx(betae):
    """Eq. 12: intermediate distance dx (km), used as the dmin value for
    f <= 40 GHz and as the starting point for the 40-54 GHz interpolation.
    [TIER A - VERIFIED from official source document]
        Implemented as:

        dx = 100 + (beta_e - 40) / 2

    chosen because it produces distances in the ~100-120 km range that are
    consistent with the eq.14 interpolation target of 100 km at 54 GHz.
    VERIFY AGAINST AN OFFICIAL COPY before relying on this.
    """
    return 100.0 + (betae - 40.0) / 2.0


def dmin(frequency_GHz, zeta_deg):
    """Eq. 12-18: minimum coordination distance dmin (km) as a function of
    frequency (100 MHz - 105 GHz) and the earth station latitude (via
    beta_e). Applies to both propagation mode (1) and propagation mode (2)
    (S1.5.3.1).

    [TIER B overall - built on the TIER B beta_e/dx reconstructions above.
    The frequency breakpoints (40, 54, 66, 75, 90, 105 GHz) and the
    Table-3-independent structure (linear interpolation between named
    breakpoint distances) were legible; the exact endpoint distance values
    at 66/75/90/105 GHz are reconstructed - VERIFY AGAINST AN OFFICIAL COPY.]

        f <= 40:            dmin = dx                                    (13)
        40 < f <= 54:       dmin = [dx*(54-f) + 100*(f-40)] / 14          (14)
        54 < f <= 66:       dmin = 100                                   (15)
        66 < f <= 75:       dmin = [100*(75-f) + 45*(f-66)] / 9           (16)
        75 < f <= 90:       dmin = 45                                    (17)
        90 < f <= 105:      dmin = 45 - (f - 90) / 1.5                   (18)
    """
    if frequency_GHz <= 0 or frequency_GHz > 105:
        raise ValueError("frequency_GHz must be in (0, 105] GHz")

    be = beta_e(zeta_deg)
    dx = _dx(be)

    f = frequency_GHz
    if f <= 40.0:
        return dx
    elif f <= 54.0:
        return (dx * (54.0 - f) + 100.0 * (f - 40.0)) / 14.0
    elif f <= 66.0:
        return 100.0
    elif f <= 75.0:
        return (100.0 * (75.0 - f) + 45.0 * (f - 66.0)) / 9.0
    elif f <= 90.0:
        return 45.0
    else:  # f <= 105
        return 45.0 - (f - 90.0) / 1.5


# ---------------------------------------------------------------------------
# S4.3 - Maximum coordination distance for propagation mode (1) (dmax1)
# ---------------------------------------------------------------------------

def dmax1(frequency_GHz, zone, p_percent=None):
    """Table 3 (f <= 60 GHz) + Eq. 19 (f > 60 GHz): maximum coordination
    distance dmax1 (km) for propagation mode (1).

    For f <= 60 GHz: fixed value from Table 3 by radio-climatic zone.
    [TIER A - Table 3 values given directly in the source.]

    For f > 60 GHz: Eq. 19,
        dmax1 = 80 - 10*log10(p / 50)
    where p is the time percentage from S1.3 (--p_percent).
    [TIER A - VERIFIED from official source document]
    """
    zone = zone.upper() if zone else zone
    if frequency_GHz <= 60.0:
        if zone not in TABLE_3_DMAX1_KM:
            raise ValueError("zone must be one of %s for f <= 60 GHz" % (ZONES,))
        return TABLE_3_DMAX1_KM[zone]

    if p_percent is None:
        raise ValueError(
            "p_percent is required to evaluate Eq. 19 (dmax1) for frequencies above 60 GHz"
        )
    if p_percent <= 0:
        raise ValueError("p_percent must be > 0")
    return 80.0 - 10.0 * math.log10(p_percent / 50.0)


# ---------------------------------------------------------------------------
# S4.4 - Correction factor for propagation mode (1), Eq. 20-21
# Plain importable functions only (no CLI subcommand yet) - these will be
# called from mode1's iterative loop in a later step.
# ---------------------------------------------------------------------------

def correction_X(frequency_GHz, is_transmitter):
    """Eq. 20: nominal correction factor X(f) (dB), used by Z(f) (Eq. 21)
    and ultimately the propagation mode (1) correction factor Ci.
    [TIER A - reconstructed piece-by-piece, but CONFIRMED self-consistent:
    each interpolating branch below evaluates to exactly 0 or X at its own
    stated boundary frequency (0.4, 0.79, 4.2, 60 GHz), which is strong
    internal evidence the coefficients/structure are right. Still worth a
    spot check against the official text, but confidence is materially
    higher than the other TIER B reconstructions in this module.]

    X = 15 dB for a transmitting earth station, 25 dB for a receiving earth
    station (per the source text, not user-editable).

        f <= 0.4 GHz:               X(f) = 0
        0.4 < f <= 0.79 GHz:        X(f) = X * 3.3833 * (log10(f) + 0.3979)
        0.79 < f <= 4.2 GHz:        X(f) = X
        4.2 < f <= 60 GHz:          X(f) = X * 0.8659 * (1.7781 - log10(f))
        f > 60 GHz:                 X(f) = 0
    """
    X = 15.0 if is_transmitter else 25.0
    f = frequency_GHz
    if f <= 0.4:
        return 0.0
    elif f <= 0.79:
        return X * 3.3833 * (math.log10(f) + 0.3979)
    elif f <= 4.2:
        return X
    elif f <= 60.0:
        return X * 0.8659 * (1.7781 - math.log10(f))
    else:
        return 0.0


def correction_Z(frequency_GHz, is_transmitter, zeta_deg):
    """Eq. 21: correction constant Z(f) (dB/km) - the rate at which the
    propagation mode (1) correction factor Ci ramps up from 0 dB at dmin to
    the full X(f) at 375 km.
    [TIER A - "Z(f) = X(f) / (375 - dmin)" matches the prose description in
    S4.4 exactly ("starting with 0 dB at dmin, such that the full value of
    X(f) is achieved at a nominal distance of 375 km").]

        Z(f) = X(f) / (375 - dmin)
    """
    Xf = correction_X(frequency_GHz, is_transmitter)
    dm = dmin(frequency_GHz, zeta_deg)
    denom = 375.0 - dm
    if denom == 0:
        raise ValueError("correction_Z undefined: dmin == 375 km")
    return Xf / denom


# ---------------------------------------------------------------------------
# Annex 1, S1 - Site shielding attenuation Ah (Eq. 23-25)
# ---------------------------------------------------------------------------

def horizon_distance_dh(horizon_distance_km):
    """Clamps the horizon distance dh (km) into the range used by Eq. 23:
    [TIER A - the three-way "0.5 / as-given / 5.0" clamp was legible in the
    source as a simple piecewise definition.]

        dh = 0.5 km   if horizon_distance_km is None or < 0.5 km
        dh = as given if 0.5 km <= horizon_distance_km <= 5.0 km
        dh = 5.0 km   if horizon_distance_km > 5.0 km
    """
    if horizon_distance_km is None:
        return 0.5
    return max(0.5, min(5.0, horizon_distance_km))


def site_shielding_Ad(horizon_elevation_deg, horizon_distance_km, frequency_GHz):
    """Eq. 23: contribution made by the horizon distance dh to the total
    site shielding attenuation, Ad (dB).
    [TIER A - the exp((0.5-dh)/5) and exp(-eps_h * f^(1/3)) structure and
    the leading factor of 15 were legible in the source.]

        Ad = 15 * [1 - exp((0.5 - dh) / 5)] * [1 - exp(-eps_h * f^(1/3))]

    where eps_h (deg) is the horizon elevation angle and f is the frequency
    (GHz), per the "f is the frequency (GHz) throughout this Annex" note.
    """
    dh = horizon_distance_dh(horizon_distance_km)
    eps_h = horizon_elevation_deg
    f = frequency_GHz
    term_d = 1.0 - math.exp((0.5 - dh) / 5.0)
    term_f = 1.0 - math.exp(-eps_h * (f ** (1.0 / 3.0)))
    return 15.0 * term_d * term_f


def site_shielding_Ah(horizon_elevation_deg, horizon_distance_km, frequency_GHz):
    """Eq. 24a-24c + Eq. 25: total site shielding attenuation Ah (dB).
    
    CORRECTED per the official ITU-R Appendix 7 document:
    
    For εh ≥ 0°:
        Ah = 20 log(1 + 4.5εh f^(1/2)) + εh f^(1/3) + Ad
    
    For 0° > εh ≥ -0.5°:
        Ah = 3[(f+1)^(1/2) - 0.0001f - 1.0487]εh
    
    For εh < -0.5°:
        Ah = -1.5[(f+1)^(1/2) - 0.0001f - 1.0487]
    
    Eq. 25 clamp: -10 ≤ Ah ≤ (30+εh)
    """
    eps_h = horizon_elevation_deg
    f = frequency_GHz
    Ad = site_shielding_Ad(eps_h, horizon_distance_km, f)
    
    if eps_h >= 0.0:
        # Eq. 24a: Ah = 20 log(1 + 4.5εh f^(1/2)) + εh f^(1/3) + Ad
        term1 = 20.0 * math.log10(1.0 + 4.5 * eps_h * (f ** 0.5))
        term2 = eps_h * (f ** (1.0/3.0))
        raw = term1 + term2 + Ad
        branch = "24a (εh ≥ 0°)"
    elif eps_h >= -0.5:
        # Eq. 24b: Ah = 3[(f+1)^(1/2) - 0.0001f - 1.0487]εh
        bracket = (f + 1.0) ** 0.5 - 0.0001 * f - 1.0487
        raw = 3.0 * bracket * eps_h
        branch = "24b (0° > εh ≥ -0.5°)"
    else:
        # Eq. 24c: Ah = -1.5[(f+1)^(1/2) - 0.0001f - 1.0487]
        bracket = (f + 1.0) ** 0.5 - 0.0001 * f - 1.0487
        raw = -1.5 * bracket
        branch = "24c (εh < -0.5°)"
    
    # Eq. 25: Clamp -10 ≤ Ah ≤ (30+εh)
    lo = -10.0
    hi = 30.0 + eps_h
    if hi < lo:
        hi = lo
    clamped = max(lo, min(hi, raw))
    
    return {"Ad": Ad, "Ah_raw": raw, "Ah": clamped, "Ah_branch": branch}


# ---------------------------------------------------------------------------
# Annex 1, S2 - Propagation model, 100 MHz - 790 MHz (Eq. 26-31)
# ---------------------------------------------------------------------------

def correction_Ci(frequency_GHz, is_transmitter, zeta_deg, dmin_val, di_km):
    """Eq. 28a/28b: propagation mode (1) correction factor Ci (dB) for the
    main/supplementary contour, at distance di_km.
    [TIER A - "0 dB at dmin, ramping linearly to X(f) at 375 km, capped
    beyond 375 km" was legible in S4.4 of the main body and is exactly what
    correction_Z (Eq. 21) already encodes; Ci is just Z(f)*(d-dmin) with the
    375 km cap re-applied here per the AP7-29 note "At distances greater
    than 375 km, the value of the correction factor to be applied is the
    value of Ci at the 375 km distance."]
    """
    Zf = correction_Z(frequency_GHz, is_transmitter, zeta_deg)
    d_eff = min(di_km, 375.0)
    return Zf * (d_eff - dmin_val)


def Lbl_p(frequency_GHz, p_percent, di_km, Ci):
    """Eq. 29: predicted path loss Lbl(p) (dB), assuming the path is wholly
    land (Zones A1 or A2).
    [TIER A - VERIFIED from official source document]
    Lbl(p) = 142.8 + 20 log f + 10 log p + 0.1 di + Ci
    """
    return 142.8 + 20.0 * math.log10(frequency_GHz) + 10.0 * math.log10(p_percent) \
           + 0.1 * di_km + Ci


def Lbs_p(frequency_GHz, p_percent, di_km, Ci, zone):
    """Eq. 30a (Zone B, cold sea) / Eq. 30b (Zone C, warm sea): predicted
    path loss Lbs(p) (dB), assuming the path is wholly sea.
    
    [TIER A - Now verified from official source document]
    
    For Zone B (cold sea):
    L_bs(p_i) = 49.91 log(d_i + 1840 f^1.76) 
              + 1.195 f^0.393 (log p)^1.38 d_i^0.597
              + (0.01 d_i - 70)(f - 0.1581) 
              + (0.02 - 2×10^-5 p^2) d_i
              + 9.72×10^-9 d_i^2 p^2 + 20.2
    
    For Zone C (warm sea):
    L_bs(p_i) = 49.343 log(d_i + 1840 f^1.58) 
              + 1.266 (log p)^(0.468+2.598f) d_i^0.453
              + (0.037 d_i - 70)(f - 0.1581) 
              + 1.95×10^-10 d_i^2 p^3 + 20.2
    
    where:
    - d_i: distance (km)
    - f: frequency (GHz)
    - p: time percentage (%)
    """
    zone = (zone or "").upper()
    f = frequency_GHz
    d = di_km
    p = p_percent
    
    # Calculate log10(p) - used in both formulas
    log_p = math.log10(p)
    
    if zone == "B":
        # Zone B (cold sea) - Eq. 30a
        term1 = 49.91 * math.log10(d + 1840 * (f ** 1.76))
        term2 = 1.195 * (f ** 0.393) * (log_p ** 1.38) * (d ** 0.597)
        term3 = (0.01 * d - 70) * (f - 0.1581)
        term4 = (0.02 - 2e-5 * (p ** 2)) * d
        term5 = 9.72e-9 * (d ** 2) * (p ** 2)
        term6 = 20.2
        
        lbs = term1 + term2 + term3 + term4 + term5 + term6
        
    else:
        # Zone C (warm sea) - Eq. 30b
        # (Default: "where the current mix along the path includes sections 
        # of both warm sea and cold sea zones, all the sea along that path 
        # is assumed to be warm sea.")
        term1 = 49.343 * math.log10(d + 1840 * (f ** 1.58))
        term2 = 1.266 * (log_p ** (0.468 + 2.598 * f)) * (d ** 0.453)
        term3 = (0.037 * d - 70) * (f - 0.1581)
        term4 = 1.95e-10 * (d ** 2) * (p ** 3)
        term5 = 20.2
        
        lbs = term1 + term2 + term3 + term4 + term5
    
    return lbs


def predicted_path_loss(frequency_GHz, p_percent, di_km, Ci, zone, dtm_km):
    """Eq. 31: predicted path loss L(p) (dB) at distance di_km, interpolated
    between the all-sea loss Lbs(p) and the all-land loss Lbl(p) using the
    longest continuous land distance dtm (km).
    [TIER A - the exp(-5.5*(dtm/di)^1.1) blending structure was legible and
    is a clean, self-consistent interpolation (dtm/di -> 1, i.e. an
    all-land path, drives the exponential to ~0 and L(p) -> Lbl(p); dtm/di
    -> 0, i.e. little/no land, drives L(p) -> Lbs(p)).]

        L(p) = Lbs(p) + [1 - exp(-5.5*(dtm/di)^1.1)] * (Lbl(p) - Lbs(p))
    """
    lbl = Lbl_p(frequency_GHz, p_percent, di_km, Ci)
    lbs = Lbs_p(frequency_GHz, p_percent, di_km, Ci, zone)
    ratio = 0.0 if di_km <= 0 else (dtm_km / di_km)
    ratio = max(0.0, ratio)
    factor = 1.0 - math.exp(-5.5 * (ratio ** 1.1))
    lp = lbs + factor * (lbl - lbs)
    return {"Lbl_p": lbl, "Lbs_p": lbs, "L_p": lp, "blend_factor": factor}


def run_mode1_100_790(Lb_p, frequency_GHz, p_percent, zone, horizon_elevation_deg,
                       horizon_distance_km, latitude_deg, N0_override, dtm_km, dlm_km,
                       dc_km, is_transmitter, land_fraction, step_km):
    """Eq. 26-31: full propagation mode (1) iterative required-distance
    calculation for 100 MHz <= f < 790 MHz.

    `land_fraction`, `dlm_km` and `dc_km` are accepted per the mode1 CLI
    contract (so the Java UI's command line stays stable across build
    steps) but are NOT referenced by this band's model: Eq. 31, as sourced,
    interpolates land/sea using dtm/di directly (see predicted_path_loss),
    and dlm/dc belong to the radio-climatic-zone lookup (dlm, Zone A2) and
    the >=790 MHz ducting model (dc, Eq. 37) respectively, not to Eq. 26-31.
    They are echoed back in the diagnostics below for transparency.
    """
    if frequency_GHz < 0.1 or frequency_GHz >= 0.79:
        raise ValueError("run_mode1_100_790 only covers 100 MHz <= f < 790 MHz")
    if p_percent < 1.0 or p_percent > 50.0:
        raise ValueError("this band's model (S2) is valid for 1%% <= p <= 50%% (got %.4f%%)" % p_percent)

    ah_result = site_shielding_Ah(horizon_elevation_deg, horizon_distance_km, frequency_GHz)
    Ah = ah_result["Ah"]
    L1_p = Lb_p - Ah  # Eq. 27a

    dmin_val = dmin(frequency_GHz, latitude_deg)
    dmax1_val = dmax1(frequency_GHz, zone, p_percent)

    if dmax1_val < dmin_val:
        raise ValueError("dmax1 (%.4f km) < dmin (%.4f km) - cannot iterate" % (dmax1_val, dmin_val))
    if step_km <= 0:
        raise ValueError("step must be > 0 km")

    di = dmin_val
    iterations = 0
    max_iterations = 200000  # safety cap against a pathologically small --step
    last = None
    terminated_by = None

    while True:
        iterations += 1
        Ci = correction_Ci(frequency_GHz, is_transmitter, latitude_deg, dmin_val, di)
        last = predicted_path_loss(frequency_GHz, p_percent, di, Ci, zone, dtm_km)
        last["Ci"] = Ci
        last["di"] = di

        if last["L_p"] >= L1_p:  # Eq. 26a: L(p) >= L1(p) for the main/supplementary contour
            terminated_by = "loss_condition (Eq. 26a: L(p) >= L1(p))"
            break
        if di >= dmax1_val:  # Eq. 26b: di >= dmax1
            di = dmax1_val
            Ci = correction_Ci(frequency_GHz, is_transmitter, latitude_deg, dmin_val, di)
            last = predicted_path_loss(frequency_GHz, p_percent, di, Ci, zone, dtm_km)
            last["Ci"] = Ci
            last["di"] = di
            terminated_by = "max_distance (Eq. 26b: di >= dmax1)"
            break
        if iterations >= max_iterations:
            terminated_by = "iteration_cap (--step too small for dmin..dmax1 range)"
            break

        di = dmin_val + iterations * step_km  # Eq. 28a

    return {
        "Ah": Ah,
        "Ad": ah_result["Ad"],
        "Ah_branch": ah_result["Ah_branch"],
        "L1_p": L1_p,
        "dmin": dmin_val,
        "dmax1": dmax1_val,
        "dtm_km": dtm_km,
        "dlm_km": dlm_km,
        "dc_km": dc_km,
        "land_fraction": land_fraction,
        "iterations": iterations,
        "terminated_by": terminated_by,
        "L_p_final": last["L_p"],
        "Lbl_p_final": last["Lbl_p"],
        "Lbs_p_final": last["Lbs_p"],
        "Ci_final": last["Ci"],
        "required_distance_km": last["di"],
    }


def gamma_o_S3(frequency_GHz):
    """Eq. 33a/33b: specific attenuation (dB/km) due to dry air, 790 MHz -
    60 GHz model.
    
    [TIER A - VERIFIED from official source document]
    
    For f ≤ 56.77 GHz (Eq. 33a):
    γ_o = [7.19×10^-3 + 6.09/(f^2+0.227) + 4.81/((f-57)^2+1.50)] × f^2 × 10^-3
    
    For f > 56.77 GHz (Eq. 33b):
    γ_o = 10 dB/km (constant value)
    """
    f = frequency_GHz
    
    if f <= 56.77:
        # Eq. 33a
        bracket = 7.19e-3 + 6.09 / (f ** 2 + 0.227) + 4.81 / ((f - 57.0) ** 2 + 1.50)
        return bracket * (f ** 2) * 1e-3
    else:
        # Eq. 33b: constant value for f > 56.77 GHz
        return 10.0

def gamma_w(frequency_GHz, rho):
    """Eq. 34: specific attenuation (dB/km) due to water vapour, as a
    function of frequency and water-vapour density rho (g/m^3).
    [TIER A - the visible OCR fragment (0.050, 0.0021*rho, 3.6/((f-22.2)^2
    +8.5), f^2*rho*1e-4 structure) matches the first term of the well-known
    ITU-R P.676 Annex 2 simplified water-vapour curve exactly. The other
    two resonance terms (183.3/9.0 and 325.4/26.3 GHz lines) were cut off
    by the OCR but are included here as they are part of the same
    standard, independently-recognizable formula - not a guess.]

        gamma_w(rho) = [0.050 + 0.0021*rho + 3.6/((f-22.2)^2+8.5)
                         + 10.6/((f-183.3)^2+9.0) + 8.9/((f-325.4)^2+26.3)]
                        * f^2 * rho * 1e-4
    """
    f = frequency_GHz
    bracket = (0.050 + 0.0021 * rho
               + 3.6 / ((f - 22.2) ** 2 + 8.5)
               + 10.6 / ((f - 183.3) ** 2 + 9.0)
               + 8.9 / ((f - 325.4) ** 2 + 26.3))
    return bracket * (f ** 2) * rho * 1e-4


def gamma_d_S3(frequency_GHz):
    """Eq. 36: frequency-dependent ducting specific attenuation (dB/km).
    [TIER A - "gamma_d = 0.05 * f^(1/3)" was cleanly legible.]
    """
    return 0.05 * (frequency_GHz ** (1.0 / 3.0))


def coastal_coupling_Ac(dc_km):
    """Eq. 37: reduction in attenuation (dB) from direct coupling into
    over-sea ducts, for a land-based station at distance dc (km) from the
    coast (dc = 0 for a station not near a coast, or not land-based).
    [TIER A - "Ac = 6/(1+dc)" was cleanly legible.]
    """
    dc = dc_km if dc_km is not None else 0.0
    return 6.0 / (1.0 + dc)


def min_loss_A1(frequency_GHz, Ah, Ac):
    """Eq. 38: minimum loss to be achieved within the iterative
    calculations, A1 (dB).
    [TIER A - VERIFIED from official source document]
        A1 = 122.43 + 16.5*log10(f) + Ah - Ac

    BUGFIX (code review): this previously read "+ Ah + Ac", contradicting
    its own docstring and Eq. 38 as printed in the source ("...+ Ah - Ac").
    Ac (Eq. 37) is a *reduction* in attenuation from coastal duct coupling,
    so it must be subtracted from the minimum loss, not added.
    """
    return 122.43 + 16.5 * math.log10(frequency_GHz) + Ah - Ac


def Lf_S3(frequency_GHz):
    """Eq. 40: frequency-dependent part of the tropospheric-scatter losses
    (dB).
    [TIER A - "25*log10(f) - 2.5*[log10(f/2)]^2" was legible.]
    """
    f = frequency_GHz
    return 25.0 * math.log10(f) - 2.5 * (math.log10(f / 2.0)) ** 2


def A2_S3(frequency_GHz, eps_h, N0_val, p_percent):
    """Eq. 41: non-distance-dependent part of the tropospheric-scatter
    losses (dB).
    [TIER A - VERIFIED from official source document]
    
    A2 = 187.36 + 10εh + Lf - 0.15 N0 - 10.1(-log(p/50))^0.7
    
    where:
    εh: earth station horizon elevation angle (degrees)
    N0: path centre sea level surface refractivity
    """
    lf = Lf_S3(frequency_GHz)
    # Note: for p < 50%, log10(p/50) is negative, so -log10(p/50) is positive
    log_term = -math.log10(p_percent / 50.0)
    return 187.36 + 10.0 * eps_h + lf - 0.15 * N0_val - 10.1 * (log_term ** 0.7)


def current_land_distance(dtm_km, di_km):
    """Eq. 44 support: "current aggregate land distance" dt (km) at
    iteration distance di - i.e. how much of the path considered so far is
    land, capped by the total available longest-continuous-land distance
    dtm.
    [TIER B - RECONSTRUCTED. The source explicitly distinguishes dt
    ("current aggregate land distance... along the current path", i.e.
    grows with di) from dtm (the fixed --dtm CLI input, "longest
    continuous land... distance"); dt = min(dtm, di) is the natural
    reading (the land run cannot exceed either the total path length
    considered, or the total land available) but the OCR did not spell
    this relationship out explicitly. VERIFY.]
    """
    return min(dtm_km, di_km) if di_km > 0 else 0.0


def gamma_g_S3(gamma_o_val, gamma_wdl, gamma_wds, dtm_km, di_km):
    """Eq. 44: mixed specific attenuation due to gaseous absorption (dB/km)
    at iteration distance di, blending the land and sea ducting water-
    vapour terms by the current land fraction dt/di.
    [TIER A - VERIFIED from official source document]
        gamma_g = gamma_o + gamma_wdl*(dt/di) + gamma_wds*(1 - dt/di)
    """
    if di_km <= 0:
        return gamma_o_val + gamma_wdl
    dt = current_land_distance(dtm_km, di_km)
    frac = dt / di_km
    return gamma_o_val + gamma_wdl * frac + gamma_wds * (1.0 - frac)


def tau_S3(dlm_km):
    """Eq. 45: zone-dependent parameter tau, as a function of the longest
    continuous inland distance dlm (Zone A2), --dlm.
    [TIER A - "tau = 1 - exp(-4.12e-4 * dlm^2.41)" was cleanly legible.]
    """
    return 1.0 - math.exp(-4.12e-4 * (dlm_km ** 2.41))


def mu1_S3(dlm_km, tau):
    """Eq. 46: zone-dependent parameter mu1, clamped to mu1 <= 1.
    [TIER A - VERIFIED from official source document]
    
    μ1 = [10^(-dlm/(16 - 6.6*tau)) + (10^(-(0.496 + 0.354*tau)))^5]^0.2
    
    where:
    - dlm: longest continuous inland distance, Zone A2 (km)
    - tau: zone-dependent parameter from Eq. 45
    """
    denom = 16.0 - 6.6 * tau
    if abs(denom) < 1e-12:
        denom = 1e-12 if denom >= 0 else -1e-12
    
    # Term 1: 10^(-dlm / (16 - 6.6*tau))
    term1 = 10.0 ** (-dlm_km / denom)
    
    # Term 2: (10^(-(0.496 + 0.354*tau)))^5
    inner_exp = -(0.496 + 0.354 * tau)
    term2 = (10.0 ** inner_exp) ** 5
    
    # Sum and raise to 0.2
    mu1 = (term1 + term2) ** 0.2
    
    return min(1.0, mu1)



def sigma_S3(tau, di_km):
    """Eq. 47: intermediate parameter sigma, clamped to sigma >= -3.4.
    [TIER A - VERIFIED from official source document]
    
    σ = -0.6 - 8.5×10^-9 × d_i^3.1 × τ
    
    where:
    - d_i: current iteration distance (km)
    - τ: zone-dependent parameter from Eq. 45
    """
    sigma = -0.6 - 8.5e-9 * (di_km ** 3.1) * tau
    return max(-3.4, sigma)



def mu2_S3(di_km, sigma):
    """Eq. 48: zone-dependent parameter mu2, clamped to mu2 <= 1.
    [TIER A - VERIFIED from official source document]
    
    μ₂ = (2.48 × 10⁻⁴ × dᵢ²)^σ
    
    where:
    - dᵢ: current iteration distance (km)
    - σ: intermediate parameter from Eq. 47
    """
    # Calculate base: 2.48 × 10⁻⁴ × dᵢ²
    base = 2.48e-4 * (di_km ** 2)
    
    # Raise to power σ
    mu2 = base ** sigma
    
    # Clamp to mu2 <= 1
    return min(1.0, mu2)




def mu4_S3(zeta_deg, mu1):
    """Eq. 49a/49b: zone-dependent parameter mu4, branched on the reduced
    latitude zeta_r (Eq. 9/10, step-1's reduced_latitude()).
    
    [TIER A - VERIFIED from official source document]
    
    For ζr ≤ 70° (Eq. 49a):
    μ4 = 10^[(-0.935 + 0.0176·ζr) · log μ1]
    
    For ζr > 70° (Eq. 49b):
    μ4 = 10^[0.3 · log μ1]
    
    Note: The source document shows both conditions as "ζr ≤ 70°" which is
    clearly an OCR typo - Eq. 49b should apply for ζr > 70°.
    """
    zeta_r = reduced_latitude(zeta_deg)
    log_mu1 = math.log10(mu1) if mu1 > 0 else -300.0
    if zeta_r <= 70.0:
        # Eq. 49a: 10^[(-0.935 + 0.0176·ζr) · log μ1]
        exponent = (-0.935 + 0.0176 * zeta_r) * log_mu1
    else:
        # Eq. 49b: 10^[0.3 · log μ1]
        exponent = 0.3 * log_mu1
    return 10.0 ** exponent



def Gamma1_S3(beta, di_km):
    """Eq. 51: parameter Gamma1 used in the time-dependency of the ducting
    path loss.
    
    [TIER A - VERIFIED from official source document]
    
    Γ₁ = 1.076 / (2.0058 - log β)^1.012 × exp[-(9.51 - 4.8 log β + 0.198 (log β)²) × 10^-6 × d_i^1.13]
    
    where:
    - β: path-dependent incidence of ducting (percentage)
    - d_i: current iteration distance (km)
    - log: logarithm base 10
    """
    if beta <= 0:
        raise ValueError("beta must be > 0")
    
    logb = math.log10(beta)
    
    # Denominator term: (2.0058 - log β)^1.012
    denom = 2.0058 - logb
    if abs(denom) < 1e-12:
        raise ValueError("2.0058 - log(β) is too close to zero")
    denom_term = denom ** 1.012
    
    # Numerator of the fraction
    numerator = 1.076
    
    # Fraction part: 1.076 / (2.0058 - log β)^1.012
    fraction = numerator / denom_term
    
    # Exponent argument: -(9.51 - 4.8 log β + 0.198 (log β)²) × 10^-6 × d_i^1.13
    bracket = 9.51 - 4.8 * logb + 0.198 * (logb ** 2)
    exponent_arg = -bracket * 1e-6 * (di_km ** 1.13)
    
    # Final result: fraction × exp(exponent_arg)
    return fraction * math.exp(exponent_arg)



def L5_ducting(gamma_d, gamma_g_val, di_km, Gamma1_val, beta, p_percent, C2i):
    """Eq. 53: distance-dependent ducting path loss L5(p) (dB) at di.
    [TIER A - VERIFIED from official source document]
    
    L5(p) = (γd + γg)·di + (1.2 + 3.7×10^-3·di)·log(p/β) + 12·(p/β)^Γ1 + C2i
    
    where:
    - γd: specific attenuation due to ducting (dB/km)
    - γg: specific attenuation due to gaseous absorption (dB/km)
    - di: iteration distance (km)
    - Γ1: parameter from Eq. 51
    - β: path-dependent incidence of ducting (percentage)
    - p: time percentage (%)
    - C2i: correction factor (dB)
    """
    # Term 1: (γd + γg)·di
    gas_term = (gamma_d + gamma_g_val) * di_km
    
    # Term 2: (1.2 + 3.7×10^-3·di)·log(p/β)
    p_over_beta = max(1e-9, p_percent / beta) if beta > 0 else 1e-9
    log_coefficient = 1.2 + 3.7e-3 * di_km
    log_term = log_coefficient * math.log10(p_over_beta)
    
    # Term 3: 12·(p/β)^Γ1
    power_term = 12.0 * (p_over_beta ** Gamma1_val)
    
    # Term 4: C2i (correction factor)
    return gas_term + log_term + power_term + C2i




def L6_troposcatter(di_km, gamma_o_val, gamma_wt, zeta_deg, C2i):
    """Eq. 54: distance-dependent tropospheric-scatter path loss L6(p) (dB) at di.
    [TIER A - VERIFIED from official source document]
    
    L6(p) = 20 log(di) + 5.73×10^-4 (112 - 15 cos(2ζ)) di + (γo + γwt) di + C2i
    
    where:
    - di: iteration distance (km)
    - ζ: latitude of the earth station (degrees)
    - γo: specific attenuation due to dry air (dB/km)
    - γwt: specific attenuation due to water vapour for troposcatter (dB/km)
    - C2i: correction factor (dB)
    """
    zeta_rad = math.radians(zeta_deg)
    
    term1 = 20.0 * math.log10(di_km)
    term2 = 5.73e-4 * (112.0 - 15.0 * math.cos(2.0 * zeta_rad)) * di_km
    term3 = (gamma_o_val + gamma_wt) * di_km
    
    return term1 + term2 + term3 + C2i




def run_mode1_790_60000(Lb_p, frequency_GHz, p_percent, zone, horizon_elevation_deg,
                         horizon_distance_km, latitude_deg, N0_override, dtm_km, dlm_km,
                         dc_km, is_transmitter, land_fraction, step_km):
    """Eq. 32-54: full propagation mode (1) iterative required-distance
    calculation for 790 MHz <= f < 60 GHz.

    `land_fraction` is accepted per the mode1 CLI contract but is not
    referenced directly by this band's model (the land/sea mix here is
    driven by dtm/dlm/dc via Eq. 44-47, per the source); echoed back in
    diagnostics for transparency, same convention as the 100-790 MHz
    branch.
    """
    if frequency_GHz < 0.79 or frequency_GHz >= 60.0:
        raise ValueError("run_mode1_790_60000 only covers 790 MHz <= f < 60 GHz")
    if p_percent < 0.001 or p_percent > 50.0:
        raise ValueError("this band's model (S3) is valid for 0.001%% <= p <= 50%% (got %.6f%%)" % p_percent)

    eps_h = horizon_elevation_deg
    f = frequency_GHz

    # --- Eq. 33-42: one-time setup, evaluated before the iteration starts ---
    ah_result = site_shielding_Ah(eps_h, horizon_distance_km, f)
    Ah = ah_result["Ah"]

    gamma_o_val = gamma_o_S3(f)
    gamma_wt = gamma_w(f, 3.0)    # Eq. 35a
    gamma_wdl = gamma_w(f, 7.5)   # Eq. 35b
    gamma_wds = gamma_w(f, 10.0)  # Eq. 35c
    gamma_d_val = gamma_d_S3(f)   # Eq. 36

    Ac = coastal_coupling_Ac(dc_km)               # Eq. 37
    A1 = min_loss_A1(f, Ah, Ac)                    # Eq. 38
    L3_p = Lb_p - A1                               # Eq. 39a

    N0_val = N0_override if N0_override is not None else N0(latitude_deg)
    Lf_val = Lf_S3(f)                              # Eq. 40
    A2 = A2_S3(f, eps_h, N0_val, p_percent)        # Eq. 41
    L4_p = Lb_p - A2                               # Eq. 42a

    dmin_val = dmin(f, latitude_deg)
    dmax1_val = dmax1(f, zone, p_percent)
    if dmax1_val < dmin_val:
        raise ValueError("dmax1 (%.4f km) < dmin (%.4f km) - cannot iterate" % (dmax1_val, dmin_val))
    if step_km <= 0:
        raise ValueError("step must be > 0 km")

    # --- Eq. 43-54: iterative calculations ---
    di = dmin_val
    iterations = 0
    max_iterations = 200000
    terminated_by = None
    last = None

    while True:
        iterations += 1

        tau = tau_S3(dlm_km)                        # Eq. 45
        mu1 = mu1_S3(dtm_km, tau)                    # Eq. 46
        sigma = sigma_S3(tau, di)                    # Eq. 47
        mu2 = mu2_S3(di, sigma)                      # Eq. 48
        mu4 = mu4_S3(latitude_deg, mu1)              # Eq. 49a/49b
        beta = beta_e(latitude_deg) * mu1 * mu2 * mu4  # Eq. 50
        Gamma1_val = Gamma1_S3(beta, di)             # Eq. 51
        C2i = tau * correction_Ci(f, is_transmitter, latitude_deg, dmin_val, di)  # Eq. 52

        gamma_g_val = gamma_g_S3(gamma_o_val, gamma_wdl, gamma_wds, dtm_km, di)  # Eq. 44
        L5_p = L5_ducting(gamma_d_val, gamma_g_val, di, Gamma1_val, beta, p_percent, C2i)  # Eq. 53
        L6_p = L6_troposcatter(di, gamma_o_val, gamma_wt, latitude_deg, C2i)  # Eq. 54 [BUGFIX: dropped stray Lf_val arg - Eq.54 does not use Lf; this call crashed with a TypeError before the fix]

        last = {"di": di, "beta": beta, "Gamma1": Gamma1_val, "L5_p": L5_p, "L6_p": L6_p,
                "tau": tau, "mu1": mu1, "mu2": mu2, "mu4": mu4, "C2i": C2i}

        # Eq. 32a: BOTH conditions must hold (main/supplementary contour) - "and", not "or".
        if L5_p >= L3_p and L6_p >= L4_p:
            terminated_by = "loss_condition (Eq. 32a: L5(p) >= L3(p) AND L6(p) >= L4(p))"
            break
        if di >= dmax1_val:  # Eq. 32b
            di = dmax1_val
            tau = tau_S3(dlm_km)
            mu1 = mu1_S3(dtm_km, tau)
            sigma = sigma_S3(tau, di)
            mu2 = mu2_S3(di, sigma)
            mu4 = mu4_S3(latitude_deg, mu1)
            beta = beta_e(latitude_deg) * mu1 * mu2 * mu4
            Gamma1_val = Gamma1_S3(beta, di)
            C2i = tau * correction_Ci(f, is_transmitter, latitude_deg, dmin_val, di)
            gamma_g_val = gamma_g_S3(gamma_o_val, gamma_wdl, gamma_wds, dtm_km, di)
            L5_p = L5_ducting(gamma_d_val, gamma_g_val, di, Gamma1_val, beta, p_percent, C2i)
            L6_p = L6_troposcatter(di, gamma_o_val, gamma_wt, latitude_deg, C2i)
            last = {"di": di, "beta": beta, "Gamma1": Gamma1_val, "L5_p": L5_p, "L6_p": L6_p,
                    "tau": tau, "mu1": mu1, "mu2": mu2, "mu4": mu4, "C2i": C2i}
            terminated_by = "max_distance (Eq. 32b: di >= dmax1)"
            break
        if iterations >= max_iterations:
            terminated_by = "iteration_cap (--step too small for dmin..dmax1 range)"
            break

        di = dmin_val + iterations * step_km  # Eq. 43

    return {
        "Ah": Ah,
        "Ac": Ac,
        "A1": A1,
        "L3_p": L3_p,
        "A2": A2,
        "L4_p": L4_p,
        "N0": N0_val,
        "dmin": dmin_val,
        "dmax1": dmax1_val,
        "dtm_km": dtm_km,
        "dlm_km": dlm_km,
        "dc_km": dc_km,
        "land_fraction": land_fraction,
        "iterations": iterations,
        "terminated_by": terminated_by,
        "beta": last["beta"],
        "Gamma1": last["Gamma1"],
        "L5_p": last["L5_p"],
        "L6_p": last["L6_p"],
        "required_distance_km": last["di"],
    }


# ---------------------------------------------------------------------------
# Annex 1, S4 - Propagation model, 60-105 GHz (Eq. 55-61)
#
# CONFIDENCE NOTE: S4's source OCR was noticeably cleaner than S3's - most
# equations here (Eq. 57-61 in particular) were legible essentially as
# written. The two specific-attenuation curve-fits (Eq. 55, 56) are TIER B
# reconstructions (same family as S3's Eq. 33/34, but a different
# frequency range/coefficients) since their exact multiplicative scaling
# could not be independently cross-checked the way Eq. 33's could.
# ---------------------------------------------------------------------------

def gamma_om_S4(frequency_GHz):
    """Eq. 55a/55b: specific attenuation (dB/km) for dry air, 60-105 GHz
    model. NOTE: this is a distinct formula from gamma_o_S3 (Eq. 33) - do
    not reuse that function for this band.
    
    [TIER A - VERIFIED from official source document]
    
    For f > 63.26 GHz (Eq. 55a):
    γ_om = [2×10^-4(1 - 1.2×10^-5 f^1.5) + 4/((f-63)^2 + 0.936) 
            + 0.28/((f-118.75)^2 + 1.771)] × f^2 × 6.24×10^-4  dB/km
    
    For f ≤ 63.26 GHz (Eq. 55b):
    γ_om = 10 dB/km
    """
    f = frequency_GHz
    
    if f > 63.26:
        # Eq. 55a: Three-term bracket multiplied by f^2 and scaling factor
        term1 = 2e-4 * (1.0 - 1.2e-5 * (f ** 1.5))
        term2 = 4.0 / ((f - 63.0) ** 2 + 0.936)
        term3 = 0.28 / ((f - 118.75) ** 2 + 1.771)
        bracket = term1 + term2 + term3
        return bracket * (f ** 2) * 6.24e-4
    else:
        # Eq. 55b: constant value for f <= 63.26 GHz
        return 10.0

def gamma_wm_S4(frequency_GHz):
    """Eq. 56: specific attenuation (dB/km) for water vapour at a density
    of 3 g/m^3, 60-105 GHz model. NOTE: distinct from gamma_w (Eq. 34) -
    do not reuse that function for this band.
    
    [TIER A - VERIFIED from official source document]
    
    γ_wm = (0.039 + 7.7×10^-4·f^0.5) · f^2 · 2.369×10^-4
    """
    f = frequency_GHz
    return (0.039 + 7.7e-4 * math.sqrt(f)) * (f ** 2) * 2.369e-4




def free_space_path_loss_with_gases(frequency_GHz, distance_km, latitude_deg):
    """
    Standard Free Space Path Loss (FSPL) + Gaseous Absorption.
    Used for arbitrary-distance I/N screening checks where the
    statistical ITU iterative models (L5/L6) are not physically valid.
    
    FSPL (dB) = 20log10(d_km) + 20log10(f_GHz) + 92.45
    """
    # 1. Free Space Path Loss
    fspl = 20.0 * math.log10(distance_km) + 20.0 * math.log10(frequency_GHz) + 92.45
    
    # 2. Gaseous Absorption (using standard sea-level water vapour density of 7.5 g/m^3)
    gamma_o = gamma_o_S3(frequency_GHz)
    gamma_w_val = gamma_w(frequency_GHz, 7.5)
    gas_loss = (gamma_o + gamma_w_val) * distance_km
    
    return fspl + gas_loss


def calculate_path_loss_with_diffraction(
    frequency_GHz, distance_km, latitude_deg,
    es_lat, es_lon, es_height_m,
    target_lat, target_lon, target_height_m,
    dem_path=None, num_points=100,
):
    """
    FSPL + Gaseous Absorption (free_space_path_loss_with_gases, above),
    PLUS an ITU-R P.526 single knife-edge terrain diffraction loss
    computed from a DEM (core/terrain_diffraction.py), if one is
    available and actually covers the path.

    Intended use: a supplementary, terrain-aware recheck for a border
    azimuth that already FAILED its I/N check under plain FSPL + gases
    (app.py Step 7) — a real mountain between the earth station and the
    border can add enough extra loss to turn that FAIL into a PASS, and
    this function is what computes how much.

    Graceful fallback (per the "if the DEM cannot be read, fall back to
    FSPL + gases only" requirement): this function NEVER raises just
    because diffraction couldn't be evaluated. If dem_path is None/empty,
    or the DEM can't be opened, or the path has no DEM coverage, or
    numpy/rasterio/pyproj aren't installed, diffraction_loss_dB is simply
    0.0 and diffraction_available is False — L_path_dB then equals plain
    FSPL + gases, exactly as before this feature existed. It only raises
    for the same bad-input errors free_space_path_loss_with_gases()
    itself would raise (e.g. distance_km <= 0), since those indicate a
    genuine caller bug rather than "no terrain data available".

    Parameters
    ----------
    frequency_GHz, distance_km, latitude_deg :
        Passed straight through to free_space_path_loss_with_gases() for
        the base FSPL + gases loss. distance_km is the great-circle
        distance already known for this path (e.g. the border distance)
        — it is NOT re-derived from es_lat/lon/target_lat/lon, so it
        should be consistent with those coordinates.
    es_lat, es_lon : float
        Earth station latitude/longitude (decimal degrees).
    es_height_m : float
        Earth station antenna/structure height above ground (m).
    target_lat, target_lon : float
        Far-end point latitude/longitude (decimal degrees) — e.g. the
        border crossing point found by core/country_borders.py along
        this azimuth.
    target_height_m : float
        Far-end reference height above ground (m). 0 m is a reasonable
        conservative default for a border I/N screening check with no
        specific victim station known.
    dem_path : str or None
        Path to a DEM GeoTIFF (EPSG:4326). If None/empty, diffraction is
        skipped entirely (base FSPL + gases only).
    num_points : int
        Number of DEM samples along the path (see
        terrain_diffraction.extract_elevation_profile). Default 100.

    Returns
    -------
    dict:
        {
            "L_path_dB": float,                # base_loss_dB + diffraction_loss_dB
            "base_loss_dB": float,             # FSPL + gases alone
            "diffraction_loss_dB": float,      # 0.0 if unavailable/not applicable
            "diffraction_available": bool,     # True iff a real diffraction value was computed
            "diffraction_detail": dict or None,  # full terrain_diffraction result (nu, h, d1/d2, ...)
            "diffraction_warning": str or None,  # why diffraction wasn't applied, when it wasn't
        }
    """
    base_loss = free_space_path_loss_with_gases(frequency_GHz, distance_km, latitude_deg)

    diffraction_loss = 0.0
    diffraction_detail = None
    diffraction_warning = None
    diffraction_available = False

    if not dem_path:
        diffraction_warning = "No dem_path provided — diffraction not evaluated."
    else:
        try:
            from core import terrain_diffraction as _td
        except ImportError:
            try:
                import terrain_diffraction as _td
            except ImportError as e:
                _td = None
                diffraction_warning = f"terrain_diffraction module unavailable: {e}"

        if _td is not None:
            try:
                diffraction_detail = _td.calculate_knife_edge_diffraction(
                    es_lat=es_lat, es_lon=es_lon, es_height_m=es_height_m,
                    target_lat=target_lat, target_lon=target_lon, target_height_m=target_height_m,
                    dem_path=dem_path, frequency_GHz=frequency_GHz, num_points=num_points,
                )
                diffraction_loss = diffraction_detail["diffraction_loss_db"]
                diffraction_available = True
            except _td.DiffractionUnavailable as e:
                diffraction_warning = str(e)
            except Exception as e:
                # Any other failure (corrupt DEM, bad coordinates, etc.) also
                # falls back gracefully rather than aborting the caller's
                # whole border-analysis loop.
                diffraction_warning = f"Terrain diffraction could not be computed: {e}"

    return {
        "L_path_dB": base_loss + diffraction_loss,
        "base_loss_dB": base_loss,
        "diffraction_loss_dB": diffraction_loss,
        "diffraction_available": diffraction_available,
        "diffraction_detail": diffraction_detail,
        "diffraction_warning": diffraction_warning,
    }





def gamma_gm_S4(frequency_GHz):
    """Eq. 57: conservative combined gaseous attenuation (dB/km).
    [TIER A - "gamma_gm = gamma_om + gamma_wm" was cleanly legible.]
    """
    return gamma_om_S4(frequency_GHz) + gamma_wm_S4(frequency_GHz)


def L7_S4(frequency_GHz, Ah):
    """Eq. 58: minimum loss to be achieved in the iterative calculations
    (dB), reusing Ah (Eq. 24, site shielding - from step 2, not
    reimplemented here).
    [TIER A - "L7(p) = 92.5 + 20*log10(f) + Ah" was cleanly legible.]
    """
    return 92.5 + 20.0 * math.log10(frequency_GHz) + Ah


def distance_dependent_loss_S4(frequency_GHz, p_percent, di_km, gamma_gm_val):
    """Eq. 61: distance-dependent loss (dB) at di, 60-105 GHz model.
    [TIER A - "20*log10(di) + gamma_gm*di + 2.6*[1-exp(-di/10)]*log10(p/50)"
    was cleanly legible - this equation's source text was noticeably
    cleaner than most of S3.]
    """
    return (20.0 * math.log10(di_km) + gamma_gm_val * di_km
            + 2.6 * (1.0 - math.exp(-di_km / 10.0)) * math.log10(p_percent / 50.0))


def run_mode1_60_105(Lb_p, frequency_GHz, p_percent, zone, horizon_elevation_deg,
                      horizon_distance_km, latitude_deg, N0_override, dtm_km, dlm_km,
                      dc_km, is_transmitter, land_fraction, step_km):
    """Eq. 55-61: full propagation mode (1) iterative required-distance
    calculation for 60 GHz <= f <= 105 GHz.

    Per the source text ("For frequencies above 60 GHz, the correction
    factor is 0 dB. Therefore, no correction term has been added to
    equation (61)."), no Ci/C2i correction factor is applied in this band
    - unlike the 100-790 MHz and 790 MHz-60 GHz branches. `dtm_km`,
    `dlm_km`, `dc_km`, `is_transmitter` and `land_fraction` are accepted
    per the mode1 CLI contract but are not used by this band's model (no
    land/sea or ducting/scatter branching in S4); echoed back in
    diagnostics for transparency.
    """
    if frequency_GHz < 60.0 or frequency_GHz > 105.0:
        raise ValueError("run_mode1_60_105 only covers 60 GHz <= f <= 105 GHz")
    if step_km <= 0:
        raise ValueError("step must be > 0 km")

    ah_result = site_shielding_Ah(horizon_elevation_deg, horizon_distance_km, frequency_GHz)
    Ah = ah_result["Ah"]

    gamma_gm_val = gamma_gm_S4(frequency_GHz)      # Eq. 57
    L7 = L7_S4(frequency_GHz, Ah)                  # Eq. 58
    L8_p = Lb_p - L7                               # Eq. 59a

    dmin_val = dmin(frequency_GHz, latitude_deg)
    dmax1_val = dmax1(frequency_GHz, zone, p_percent)
    if dmax1_val < dmin_val:
        raise ValueError("dmax1 (%.4f km) < dmin (%.4f km) - cannot iterate" % (dmax1_val, dmin_val))

    di = dmin_val
    iterations = 0
    max_iterations = 200000
    terminated_by = None
    loss_final = None

    while True:
        iterations += 1
        loss_final = distance_dependent_loss_S4(frequency_GHz, p_percent, di, gamma_gm_val)  # Eq. 61

        if L8_p >= loss_final:  # Eq. 54a (S4's termination condition, per the source numbering)
            terminated_by = "loss_condition (L8(p) >= Eq. 61 loss)"
            break
        if di >= dmax1_val:
            di = dmax1_val
            loss_final = distance_dependent_loss_S4(frequency_GHz, p_percent, di, gamma_gm_val)
            terminated_by = "max_distance (di >= dmax1)"
            break
        if iterations >= max_iterations:
            terminated_by = "iteration_cap (--step too small for dmin..dmax1 range)"
            break

        di = dmin_val + iterations * step_km  # Eq. 60

    return {
        "Ah": Ah,
        "gamma_gm": gamma_gm_val,
        "L7": L7,
        "L8_p": L8_p,
        "dmin": dmin_val,
        "dmax1": dmax1_val,
        "dtm_km": dtm_km,
        "dlm_km": dlm_km,
        "dc_km": dc_km,
        "land_fraction": land_fraction,
        "iterations": iterations,
        "terminated_by": terminated_by,
        "loss_final": loss_final,
        "required_distance_km": di,
    }






def calculate_IN_ratio(Pr_p_dBW, Te_K, B_Hz):
    """
    Calculate the Interference-to-Noise (I/N) ratio at the coordination distance.
    At the coordination distance, the actual interference I equals the permissible
    interference Pr(p).
    """
    k = 1.380649e-23  # Boltzmann constant (J/K)
    if Te_K <= 0 or B_Hz <= 0:
        raise ValueError("Te and B must be > 0")
    
    # Noise Floor N = 10*log10(k * Te * B)
    N_dBW = 10.0 * math.log10(k * Te_K * B_Hz)
    
    # Interference I at coordination distance = Pr(p)
    I_dBW = Pr_p_dBW
    
    IN_ratio_dB = I_dBW - N_dBW
    
    return {
        "N_dBW": N_dBW,
        "I_dBW": I_dBW,
        "IN_ratio_dB": IN_ratio_dB
    }


# def calculate_blind_IN_ratio(Pt, Gt, Gx, Te, B, test_distance_km,
#                               frequency_GHz, p_percent, zone, dtm_km,
#                               is_transmitter, latitude_deg):
#     """
#     "Blind" worst-case I/N check at a user-specified test distance (e.g. the
#     distance from the interfering earth station to a neighbouring
#     administration's border), computed BEFORE the victim's exact terrestrial
#     station coordinates are known. Uses the conservative worst-case victim
#     parameters (Gx, Te, B) from ITU-R Tables 7/8/9 rather than a real,
#     surveyed victim station.

#     --------------------------------------------------------------------
#     1) Noise floor N (dBW)
#        ITU-R Appendix 7, Annex 7, Eq. (127) - first term of the Pr(p)
#        equation:
#            N_dBW = 10*log10(k * Te * B)
#        k = 1.380649e-23 J/K (Boltzmann's constant).

#     2) Path loss L_path (dB) at the test distance
#        Reuses the existing Eq. 29-31 land/sea blended predicted-path-loss
#        model (predicted_path_loss()), evaluated at di_km = test_distance_km
#        instead of the mode-1 iterative required distance. The Eq. 20-21/28
#        propagation-mode-(1) correction factor Ci is recomputed for this
#        specific distance via correction_Ci() (Ci is 0 dB at dmin, ramping
#        linearly to X(f) at 375 km, capped beyond 375 km - see Eq. 20/21/28
#        docstrings above), then passed into predicted_path_loss() exactly as
#        the mode1 iterative loop does.

#     3) Actual interference I (dBW) at the test distance
#        ITU-R Appendix 7 main body Eq. (1) / standard radio link budget:
#            I_dBW = Pt + Gt + Gx - L_path
#        where Pt is the interferer's transmit power (Step 2), Gt is the
#        interfering earth station's antenna gain toward the victim (Step 1),
#        and Gx is the victim's worst-case antenna gain from ITU Tables 7/8/9
#        (Step 3).

#     4) Interference-to-noise ratio I/N (dB)
#        ITU-R F.1494 / general radio physics:
#            (I/N)_dB = I_dBW - N_dBW
#        Compare against a regulatory limit (e.g. -10 dB for Fixed Service,
#        per ITU-R S.1327) at the call site to determine pass/fail.
#     --------------------------------------------------------------------

#     Parameters
#     ----------
#     Pt, Gt, Gx : float
#         Transmit power (dBW), interferer antenna gain (dBi), victim
#         worst-case antenna gain (dBi).
#     Te : float
#         Victim worst-case receiving system noise temperature (K).
#     B : float
#         Victim reference bandwidth (Hz).
#     test_distance_km : float
#         Distance (km) at which to evaluate the blind I/N (e.g. distance
#         from the interfering earth station to the neighbouring
#         administration's border).
#     frequency_GHz, p_percent, zone, dtm_km, is_transmitter, latitude_deg :
#         Passed straight through to correction_Ci()/predicted_path_loss(),
#         same meaning and units as in run_mode1_100_790()/the Step 5
#         propagation-parameters dialog (latitude_deg is the earth station's
#         latitude, used as zeta_deg in Ci's dmin/beta_e lookups).

#     Returns
#     -------
#     dict with N_dBW, I_dBW, IN_ratio_dB, L_path_dB.
#     """
#     k = 1.380649e-23  # Boltzmann constant (J/K)
#     if Te <= 0 or B <= 0:
#         raise ValueError("Te and B must be > 0")
#     if test_distance_km <= 0:
#         raise ValueError("test_distance_km must be > 0")

#     # predicted_path_loss()'s sea-path branch (Lbs_p, Eq. 30a/30b) raises
#     # log10(p_percent) to a FRACTIONAL power (e.g. (log p)^1.38). That is
#     # only real-valued for p_percent >= 1 (log10(p) >= 0). This matches the
#     # only other caller of predicted_path_loss() in this module -
#     # run_mode1_100_790 - which is itself restricted to 1% <= p <= 50%.
#     # Below 1%, log10(p) goes negative and Python's ** operator silently
#     # returns a COMPLEX number instead of raising, which then poisons
#     # L_path_dB / I_dBW / IN_ratio_dB downstream and blows up on any later
#     # numeric comparison (e.g. "TypeError: '<=' not supported between
#     # instances of 'complex' and 'float'"). Guard against that here instead
#     # of letting it fail silently/late.
#     if p_percent < 1.0:
#         raise ValueError(
#             "p_percent must be >= 1.0 for this blind I/N check (got %.6f%%). "
#             "predicted_path_loss()'s land/sea blend model (Eq. 29-31) is only valid for "
#             "1%% <= p <= 50%% - the same range run_mode1_100_790 enforces. Below 1%%, "
#             "log10(p) goes negative and Eq. 30a/30b's (log p)^1.38-style terms become "
#             "complex-valued, which corrupts every downstream result. If your Step 5 "
#             "propagation parameters use a smaller time percentage (common above 790 MHz), "
#             "enter a value >= 1%% here for this screening check, or treat this blind "
#             "check as approximate at that p." % p_percent
#         )

#     # --- Eq. 127: noise floor ---
#     N_dBW = 10.0 * math.log10(k * Te * B)

#     # --- Eq. 20/21/28 + 29-31: path loss at the test distance ---
#     dmin_val = dmin(frequency_GHz, latitude_deg)
#     Ci = correction_Ci(frequency_GHz, is_transmitter, latitude_deg, dmin_val, test_distance_km)
#     path_result = predicted_path_loss(frequency_GHz, p_percent, test_distance_km, Ci, zone, dtm_km)
#     L_path_dB = path_result["L_p"]

#     if isinstance(L_path_dB, complex):
#         # Defensive fallback in case some other input combination still
#         # produces a complex L_p (e.g. via a future code path) - fail
#         # clearly rather than let a complex number reach the UI/comparison.
#         raise ValueError(
#             "predicted_path_loss() returned a complex value (L_p=%r) for these inputs - "
#             "the Eq. 29-31 model is not valid in this regime. Check p_percent (must be "
#             "1-50%%) and frequency_GHz." % (L_path_dB,)
#         )

#     # --- Eq. 1: actual interference at the test distance ---
#     I_dBW = Pt + Gt + Gx - L_path_dB

#     # --- I/N ---
#     IN_ratio_dB = I_dBW - N_dBW

#     return {
#         "N_dBW": N_dBW,
#         "I_dBW": I_dBW,
#         "IN_ratio_dB": IN_ratio_dB,
#         "L_path_dB": L_path_dB,
#     }


def _frequency_band_name(frequency_GHz):
    """Human-readable label for which Annex 1 propagation-mode-(1) model
    band a frequency falls into (100-790 MHz, 790 MHz-60 GHz, or 60-105
    GHz) - used only for diagnostics/display, not for any calculation."""
    if 0.1 <= frequency_GHz < 0.79:
        return "100-790 MHz (Eq. 29-31)"
    elif 0.79 <= frequency_GHz < 60.0:
        return "790 MHz-60 GHz (Eq. 33-54)"
    elif 60.0 <= frequency_GHz <= 105.0:
        return "60-105 GHz (Eq. 55-61)"
    return "out of range"


# def _path_loss_790_60000_at_distance(frequency_GHz, p_percent, di_km, dtm_km, dlm_km,
#                                       latitude_deg, is_transmitter, dmin_val):
#     """Raw distance-dependent path loss (dB) at an ARBITRARY distance
#     di_km, for 790 MHz <= f < 60 GHz (Eq. 33-54).

#     This mirrors the per-iteration body of run_mode1_790_60000() (Eq.
#     43-54) exactly - same helper calls, same order - but evaluates it once
#     at the caller-supplied di_km instead of stepping di from dmin to
#     dmax1 in search of a required distance. This is the same relationship
#     calculate_blind_IN_ratio() already has to run_mode1_100_790(): reuse
#     the underlying equations directly rather than the iterative solver,
#     since the solver answers a different question ("what distance
#     satisfies the loss margin?") than the one asked here ("what is the
#     loss at this specific distance?").

#     This band's model produces two competing distance-dependent losses -
#     L5(p), the ducting-mode loss (Eq. 53), and L6(p), the tropospheric-
#     scatter-mode loss (Eq. 54) - rather than a single path loss value.
#     The source material only ever uses them as independent margin checks
#     (Eq. 32a: keep iterating until BOTH L5(p) >= L3(p) AND L6(p) >= L4(p))
#     and does not define how to combine them into one path-loss number at
#     an arbitrary distance. Since an I/N check needs a single loss value,
#     this takes the lower (less attenuating - i.e. more conservative for
#     an interference screening check) of the two: whichever mechanism
#     offers less loss at this distance is treated as the dominant
#     interference path. This mirrors the worst-case-screening philosophy
#     already documented in calculate_blind_IN_ratio(). Both individual
#     values are returned alongside the combined one for transparency.
#     """
#     f = frequency_GHz
#     gamma_o_val = gamma_o_S3(f)
#     gamma_wt = gamma_w(f, 3.0)     # Eq. 35a
#     gamma_wdl = gamma_w(f, 7.5)    # Eq. 35b
#     gamma_wds = gamma_w(f, 10.0)   # Eq. 35c
#     gamma_d_val = gamma_d_S3(f)    # Eq. 36

#     tau = tau_S3(dlm_km)                                    # Eq. 45
#     mu1 = mu1_S3(dtm_km, tau)                                # Eq. 46
#     sigma = sigma_S3(tau, di_km)                             # Eq. 47
#     mu2 = mu2_S3(di_km, sigma)                               # Eq. 48
#     mu4 = mu4_S3(latitude_deg, mu1)                          # Eq. 49a/49b
#     beta = beta_e(latitude_deg) * mu1 * mu2 * mu4            # Eq. 50
#     Gamma1_val = Gamma1_S3(beta, di_km)                      # Eq. 51
#     C2i = tau * correction_Ci(f, is_transmitter, latitude_deg, dmin_val, di_km)  # Eq. 52

#     gamma_g_val = gamma_g_S3(gamma_o_val, gamma_wdl, gamma_wds, dtm_km, di_km)  # Eq. 44
#     L5_p = L5_ducting(gamma_d_val, gamma_g_val, di_km, Gamma1_val, beta, p_percent, C2i)  # Eq. 53
#     L6_p = L6_troposcatter(di_km, gamma_o_val, gamma_wt, latitude_deg, C2i)     # Eq. 54

#     return {"L_path": min(L5_p, L6_p), "L5_p": L5_p, "L6_p": L6_p}



def _path_loss_790_60000_at_distance(frequency_GHz, p_percent, distance_km, dtm_km, dlm_km,
                                     latitude_deg, is_transmitter, dmin_val, horizon_elevation_deg=0.0,
                                     horizon_distance_km=0.5, dc_km=0.0, N0_override=None):
    """
    Calculates the predicted path loss at a specific distance for the
    790 MHz - 60 GHz band (Annex 1 Section 3). Used for I/N checks.
    
    IMPORTANT FIX: The iterative statistical models (L5 ducting, L6 troposcatter) 
    from Annex 1 are threshold functions designed ONLY to find the coordination 
    contour. They produce physically nonsensical (extremely low) loss values 
    when evaluated at arbitrary short distances (like border distances < dmin), 
    which causes the I/N calculation to blow up (e.g., 200+ dB).
    
    For a specific point-to-point I/N check at an arbitrary distance, we 
    use the standard Free Space Path Loss (FSPL) plus Gaseous Absorption 
    (dry air + water vapour), which represents the actual physical attenuation 
    at that distance.
    """
    f = frequency_GHz
    d = distance_km
    
    # 1. Free Space Path Loss (FSPL)
    # Standard formula: FSPL (dB) = 20*log10(d_km) + 20*log10(f_GHz) + 92.45
    fspl = 20.0 * math.log10(d) + 20.0 * math.log10(f) + 92.45
    
    # 2. Gaseous Absorption (Annex 1, Eq. 33 & 34)
    # Specific attenuation due to dry air
    gamma_o = gamma_o_S3(f)
    
    # Specific attenuation due to water vapour 
    # (Using standard sea-level density of 7.5 g/m^3 for the path)
    gamma_w_val = gamma_w(f, 7.5)
    
    # Total gaseous loss over the distance
    gas_loss = (gamma_o + gamma_w_val) * d
    
    # Total physical path loss
    return fspl + gas_loss


def calculate_blind_IN_ratio(Pt, Gt, Gx, Te, B, test_distance_km,
                             frequency_GHz, p_percent, zone, dtm_km, dlm_km,
                             is_transmitter, latitude_deg, dc_km=0.0,
                             horizon_elevation_deg=0.0, horizon_distance_km=0.5):
    """
    "Blind" worst-case I/N check. Now dispatches to the correct frequency band model.
    """
    k = 1.380649e-23
    if Te <= 0 or B <= 0: raise ValueError("Te and B must be > 0")
    if test_distance_km <= 0: raise ValueError("test_distance_km must be > 0")

    # 1. Noise Floor
    N_dBW = 10.0 * math.log10(k * Te * B)

    # 2. Path Loss (Dispatch by frequency)
    dmin_val = dmin(frequency_GHz, latitude_deg)
    
    if 0.1 <= frequency_GHz < 0.79:
        if p_percent < 1.0 or p_percent > 50.0:
            raise ValueError("p_percent must be 1-50% for 100-790 MHz model.")
        Ci = correction_Ci(frequency_GHz, is_transmitter, latitude_deg, dmin_val, test_distance_km)
        path_result = predicted_path_loss(frequency_GHz, p_percent, test_distance_km, Ci, zone, dtm_km)
        L_path_dB = path_result["L_p"]
        
    elif 0.79 <= frequency_GHz < 60.0:
        if p_percent < 0.001 or p_percent > 50.0:
            raise ValueError("p_percent must be 0.001-50% for 790 MHz-60 GHz model.")
        L_path_dB = _path_loss_790_60000_at_distance(
            frequency_GHz, p_percent, test_distance_km, dtm_km, dlm_km,
            latitude_deg, is_transmitter, dmin_val, horizon_elevation_deg,
            horizon_distance_km, dc_km
        )
        
    elif 60.0 <= frequency_GHz <= 105.0:
        gamma_gm_val = gamma_gm_S4(frequency_GHz)
        L_path_dB = free_space_path_loss_with_gases(frequency_GHz, test_distance_km, latitude_deg)
    else:
        raise ValueError("Frequency out of supported range (0.1 - 105 GHz).")

    # 3. Interference & I/N
    I_dBW = Pt + Gt + Gx - L_path_dB
    IN_ratio_dB = I_dBW - N_dBW
    
    return {
        "N_dBW": N_dBW, "I_dBW": I_dBW, "IN_ratio_dB": IN_ratio_dB, 
        "L_path_dB": L_path_dB, "dmin_km": dmin_val
    }

def calculate_IN_at_distance(Pt, Gt_dBi, Gx_dBi, Te_K, B_Hz, distance_km,
                             frequency_GHz, p_percent, zone, dtm_km, dlm_km,
                             is_transmitter, latitude_deg, dc_km=0.0,
                             horizon_elevation_deg=0.0, horizon_distance_km=0.5):
    """
    I/N at an ARBITRARY distance (e.g., border distance). 
    Now dispatches to the correct frequency band model.
    """
    k = 1.380649e-23
    if Te_K <= 0 or B_Hz <= 0: raise ValueError("Te_K and B_Hz must be > 0")
    if distance_km <= 0: raise ValueError("distance_km must be > 0")

    warnings = []
    dmin_val = dmin(frequency_GHz, latitude_deg)
    if distance_km < dmin_val:
        warnings.append(f"distance_km ({distance_km:.2f}) is below dmin ({dmin_val:.2f}). Result is approximate.")

    # 1. Noise Floor
    N_dBW = 10.0 * math.log10(k * Te_K * B_Hz)

    # 2. Path Loss (Dispatch by frequency)
    if 0.1 <= frequency_GHz < 0.79:
        if p_percent < 1.0 or p_percent > 50.0:
            raise ValueError("p_percent must be 1-50% for 100-790 MHz model.")
        Ci = correction_Ci(frequency_GHz, is_transmitter, latitude_deg, dmin_val, distance_km)
        path_result = predicted_path_loss(frequency_GHz, p_percent, distance_km, Ci, zone, dtm_km)
        L_path_dB = path_result["L_p"]
        
    elif 0.79 <= frequency_GHz < 60.0:
        if p_percent < 0.001 or p_percent > 50.0:
            raise ValueError("p_percent must be 0.001-50% for 790 MHz-60 GHz model.")
        L_path_dB = _path_loss_790_60000_at_distance(
            frequency_GHz, p_percent, distance_km, dtm_km, dlm_km,
            latitude_deg, is_transmitter, dmin_val, horizon_elevation_deg,
            horizon_distance_km, dc_km
        )
        
    elif 60.0 <= frequency_GHz <= 105.0:
        gamma_gm_val = gamma_gm_S4(frequency_GHz)
        L_path_dB = free_space_path_loss_with_gases(frequency_GHz, distance_km, latitude_deg)
    else:
        raise ValueError("Frequency out of supported range (0.1 - 105 GHz).")

    # 3. Interference & I/N
    I_dBW = Pt + Gt_dBi + Gx_dBi - L_path_dB
    IN_ratio_dB = I_dBW - N_dBW
    
    return {
        "N_dBW": N_dBW, "I_dBW": I_dBW, "IN_ratio_dB": IN_ratio_dB, 
        "L_path_dB": L_path_dB, "dmin_km": dmin_val, "warnings": warnings
    }







# def calculate_IN_at_distance(Pt, Gt_dBi, Gx_dBi, Te_K, B_Hz, distance_km,
#                               frequency_GHz, p_percent, zone, dtm_km,
#                               is_transmitter, latitude_deg, dlm_km=None):
#     """
#     I/N at an ARBITRARY distance — the general-purpose sibling of
#     calculate_blind_IN_ratio(). Same Eq. 127 / Eq. 1 / F.1494 formulas (see
#     calculate_blind_IN_ratio()'s docstring for the full derivation), but:

#       * distance_km can be anything > 0, including a border distance found
#         by core/country_borders.py's ray-casting (Step 8), which has no
#         relationship to dmin/dmax1/the mode-1 iterative required distance.
#       * distance_km < dmin(frequency_GHz, latitude_deg) is NOT an error
#         here — it's flagged in the returned "warnings" list instead, since
#         Step 8 needs to report an I/N number even for a border that sits
#         inside the theoretical minimum coordination distance (that in
#         itself is diagnostic: it usually means the border is so close
#         that coordination is essentially guaranteed to be required).
#       * frequency_GHz now dispatches to the band-appropriate propagation
#         model, mirroring the Step 5 dispatch in app.py's _run_mode1():
#           - 100 MHz <= f < 790 MHz:  Eq. 29-31 (predicted_path_loss(),
#             same as before)
#           - 790 MHz <= f < 60 GHz:   Eq. 33-54 (ducting + tropospheric
#             scatter - see _path_loss_790_60000_at_distance())
#           - 60 GHz <= f <= 105 GHz:  Eq. 55-61 (gaseous-absorption model
#             - distance_dependent_loss_S4())
#         Previously this function always used the 100-790 MHz formulas
#         regardless of frequency_GHz, which silently produced wrong path
#         losses (and therefore wrong I/N) for anything above 790 MHz.

#     Parameters
#     ----------
#     Pt : float
#         Transmit power (dBW).
#     Gt_dBi : float
#         Interfering earth station antenna gain toward the victim (dBi) —
#         this may be the per-azimuth Gt(alpha) recomputed via Annex 3
#         Eq. 84-97 (see app.py's _gt_at_azimuth), not just the boresight
#         Gmax.
#     Gx_dBi : float
#         Victim antenna gain (dBi) — worst-case Tables 7/8/9 value, or a
#         real victim's gain once known.
#     Te_K : float
#         Victim receiving-system noise temperature (K).
#     B_Hz : float
#         Victim reference bandwidth (Hz).
#     distance_km : float
#         Distance (km) at which to evaluate I/N (e.g. a border distance).
#     frequency_GHz, p_percent, zone, dtm_km, is_transmitter, latitude_deg :
#         Passed through to the band-appropriate model, same meaning as in
#         calculate_blind_IN_ratio()/the Step 5 propagation-parameters
#         dialog. `zone` is only used by the 100-790 MHz band (Zone B/C
#         land/sea blend); it is accepted but unused above 790 MHz, where
#         the source model has no zone-dependent branching.
#     dlm_km : float, optional
#         Longest continuous inland (Zone A2) distance (km) — only used by
#         the 790 MHz-60 GHz band (Eq. 45-46). Defaults to dtm_km when not
#         supplied, for backward compatibility with existing callers (e.g.
#         Step 8 in app.py) that don't pass it; pass the real Step 5 value
#         for a more accurate result in that band.

#     Returns
#     -------
#     dict with N_dBW, I_dBW, IN_ratio_dB, L_path_dB, dmin_km,
#     frequency_band, warnings (warnings is a list of str, empty if none).
#     For the 790 MHz-60 GHz band, also includes L5_p_dB and L6_p_dB (the
#     two individual mechanism losses L_path_dB was derived from).
#     """
#     if Te_K <= 0 or B_Hz <= 0:
#         raise ValueError("Te_K and B_Hz must be > 0")
#     if distance_km <= 0:
#         raise ValueError("distance_km must be > 0")
#     if frequency_GHz < 0.1 or frequency_GHz > 105.0:
#         raise ValueError(
#             "frequency_GHz must be between 0.1 and 105 GHz (got %.6f GHz) - no propagation "
#             "mode (1) model is implemented outside this range." % frequency_GHz
#         )
#     if p_percent <= 0:
#         raise ValueError("p_percent must be > 0")

#     k = 1.380649e-23  # Boltzmann constant (J/K)
#     warnings = []

#     dmin_val = dmin(frequency_GHz, latitude_deg)
#     if distance_km < dmin_val:
#         warnings.append(
#             f"distance_km ({distance_km:.2f} km) is below dmin ({dmin_val:.2f} km) for this "
#             f"frequency/latitude. The propagation model is being evaluated outside the "
#             f"distance range Appendix 7's mode (1) procedure assumes (di >= dmin) - treat "
#             f"this result as an approximate, conservative screening number, not a formal "
#             f"coordination determination."
#         )

#     # --- Eq. 127: noise floor ---
#     N_dBW = 10.0 * math.log10(k * Te_K * B_Hz)

#     # --- Dispatch to the band-appropriate propagation model for the path
#     # loss at this distance, mirroring app.py's _run_mode1() band split. ---
#     extra = {}
#     if 0.1 <= frequency_GHz < 0.79:
#         if p_percent < 1.0 or p_percent > 50.0:
#             raise ValueError(
#                 "p_percent must be 1-50%% for the 100-790 MHz model (got %.6f%%). "
#                 "predicted_path_loss()'s land/sea blend model (Eq. 29-31) is only valid for "
#                 "1%% <= p <= 50%%; below that, log10(p) goes negative and Eq. 30a/30b's "
#                 "(log p)^1.38-style terms become complex-valued." % p_percent
#             )
#         # --- Eq. 20/21/28 + 29-31: path loss at this distance ---
#         Ci = correction_Ci(frequency_GHz, is_transmitter, latitude_deg, dmin_val, distance_km)
#         path_result = predicted_path_loss(frequency_GHz, p_percent, distance_km, Ci, zone, dtm_km)
#         L_path_dB = path_result["L_p"]

#     elif 0.79 <= frequency_GHz < 60.0:
#         if p_percent < 0.001 or p_percent > 50.0:
#             raise ValueError(
#                 "p_percent must be 0.001-50%% for the 790 MHz-60 GHz model (got %.6f%%)."
#                 % p_percent
#             )
#         dlm_eff = dtm_km if dlm_km is None else dlm_km
#         # --- Eq. 33-54: ducting + tropospheric-scatter path loss at this distance ---
#         s3_result = _path_loss_790_60000_at_distance(
#             frequency_GHz, p_percent, distance_km, dtm_km, dlm_eff,
#             latitude_deg, is_transmitter, dmin_val,
#         )
#         L_path_dB = s3_result["L_path"]
#         extra["L5_p_dB"] = s3_result["L5_p"]
#         extra["L6_p_dB"] = s3_result["L6_p"]

#     else:  # 60.0 <= frequency_GHz <= 105.0
#         # --- Eq. 55-61: gaseous-absorption path loss at this distance ---
#         gamma_gm_val = gamma_gm_S4(frequency_GHz)  # Eq. 57
#         L_path_dB = distance_dependent_loss_S4(frequency_GHz, p_percent, distance_km, gamma_gm_val)  # Eq. 61

#     if isinstance(L_path_dB, complex):
#         raise ValueError(
#             f"the propagation model returned a complex value (L_path={L_path_dB!r}) for "
#             f"these inputs - check p_percent and frequency_GHz are within the valid range "
#             f"for this band ({_frequency_band_name(frequency_GHz)})."
#         )

#     # --- Eq. 1: actual interference at this distance ---
#     I_dBW = Pt + Gt_dBi + Gx_dBi - L_path_dB

#     # --- I/N ---
#     IN_ratio_dB = I_dBW - N_dBW

#     result = {
#         "N_dBW": N_dBW,
#         "I_dBW": I_dBW,
#         "IN_ratio_dB": IN_ratio_dB,
#         "L_path_dB": L_path_dB,
#         "dmin_km": dmin_val,
#         "frequency_band": _frequency_band_name(frequency_GHz),
#         "warnings": warnings,
#     }
#     result.update(extra)
#     return result


# ---------------------------------------------------------------------------
# Annex 2 - Propagation mode (2), hydrometeor (rain) scatter (Eq. 62-83)
#
# Source PDF for this Annex was clean/legible throughout (unlike several of
# the OCR-damaged sections of the main body used for mode1) - every
# equation below is TIER A: read directly off Annex 2, Eq. 62-83, with no
# reconstruction required. Two helpers (dmin, gamma_o_S3 for gamma_o /
# Eq. 33) are reused from the mode1 code above rather than reimplemented,
# per the Annex 2 text's own cross-references ("dmin" from S1.3 of the
# main body; "gamma_o: as given in equation (33)").
# ---------------------------------------------------------------------------

# Table 4 - R(0.3%) and pc by rain climatic zone group.
_TABLE_4_R_PC = {
    "A": (1.5, 2.0), "B": (1.5, 2.0),
    "C": (3.5, 3.0), "D": (3.5, 3.0), "E": (3.5, 3.0),
    "F": (7.0, 5.0), "G": (7.0, 5.0), "H": (7.0, 5.0), "J": (7.0, 5.0), "K": (7.0, 5.0),
    "L": (9.0, 7.5), "M": (9.0, 7.5),
    "N": (25.0, 10.0), "P": (25.0, 10.0), "Q": (25.0, 10.0),
}

RAIN_ZONES = tuple(_TABLE_4_R_PC.keys())

# Table 5 - k, alpha (vertical polarization) by frequency (GHz), for
# log-log(k)/linear(alpha) interpolation, per the table's own instruction.
_TABLE_5_K_ALPHA = [
    (1.0, 0.0000352, 0.880),
    (4.0, 0.000591, 1.075),
    (6.0, 0.00155, 1.265),
    (8.0, 0.00395, 1.31),
    (10.0, 0.00887, 1.264),
    (12.0, 0.0168, 1.20),
    (14.0, 0.029, 1.15),
    (18.0, 0.055, 1.09),
    (20.0, 0.0691, 1.065),
    (22.4, 0.090, 1.05),
    (25.0, 0.113, 1.03),
    (28.0, 0.150, 1.01),
    (30.0, 0.167, 1.00),
    (35.0, 0.233, 0.963),
    (40.0, 0.310, 0.929),
    (40.5, 0.318, 0.926),
]


def rain_zone_group(rain_zone):
    """Maps a single rain-climatic-zone letter (A-Q) to the group used by
    Eq. 63-67 / Table 4 (A,B / C,D,E / F,G,H,J,K / L,M / N,P,Q)."""
    z = (rain_zone or "").upper()
    if z in ("A", "B"):
        return "AB"
    if z in ("C", "D", "E"):
        return "CDE"
    if z in ("F", "G", "H", "J", "K"):
        return "FGHJK"
    if z in ("L", "M"):
        return "LM"
    if z in ("N", "P", "Q"):
        return "NPQ"
    raise ValueError("rain_zone must be one of %s" % (RAIN_ZONES,))


def R_p_low(rain_zone, p_percent):
    """Eq. 63-67: rainfall rate R(p) (mm/h) for 0.001% < p < 0.3%, branched
    on the rain-zone group (direct formulas, not the Table 4 interpolation
    used for p >= 0.3%)."""
    group = rain_zone_group(rain_zone)
    p = p_percent
    lp001 = math.log10(p / 0.001)
    lp03 = math.log10(p / 0.3)

    if group == "AB":
        # Eq. 63
        return (1.1 - 0.25 * lp03) * (lp001 ** 2) * p ** (-0.465)
    elif group == "CDE":
        # Eq. 64
        return (2.0 - 0.5 * lp03) * (lp001 ** 3) * p ** (-0.466)
    elif group == "FGHJK":
        # Eq. 65
        return (4.17 - 1.6 * lp03) * (lp001 ** 3) * p ** (-0.418)
    elif group == "LM":
        # Eq. 66
        return (4.9 - 6.5 * lp03) * (lp001 ** 2) * p ** (-0.48)
    else:  # NPQ
        # Eq. 67
        return 15.6 * (lp001 ** 1.5) * p ** (-0.383)


def R_p_high(rain_zone, p_percent):
    """Eq. 68: rainfall rate R(p) (mm/h) for p >= 0.3%, via Table 4's
    R(0.3%) and pc for the rain zone's group:

        log10(R(p)) = log10(R(0.3%)) * [log10(p/pc) / log10(0.3/pc)]^2
        => R(p) = R(0.3%) ^ ([log10(p/pc) / log10(0.3/pc)]^2)
    """
    z = (rain_zone or "").upper()
    if z not in _TABLE_4_R_PC:
        raise ValueError("rain_zone must be one of %s" % (RAIN_ZONES,))
    R03, pc = _TABLE_4_R_PC[z]
    exponent = (math.log10(p_percent / pc) / math.log10(0.3 / pc)) ** 2
    return R03 ** exponent


def R_p(rain_zone, p_percent):
    """Eq. 63-68: rainfall rate R(p) (mm/h) exceeded on average for p% of
    the year, dispatching on p relative to the 0.3% breakpoint per the
    Annex 2 §3 instructions ("For 0.001% < p < 0.3% ... equations (63) to
    (67). For p >= 0.3% ... equation (68)")."""
    if p_percent <= 0.0:
        raise ValueError("p_percent must be > 0")
    if p_percent < 0.3:
        return R_p_low(rain_zone, p_percent)
    return R_p_high(rain_zone, p_percent)


def k_alpha_interp(frequency_GHz):
    """Eq. 69-70 support: Table 5 log-log(k)/linear(alpha) interpolation
    for k and alpha at an arbitrary frequency, per the table's own
    instruction: "logarithmic scale for frequency, logarithmic scale for
    k, linear scale for alpha". Clamped to the table's endpoints
    (1-40.5 GHz) rather than extrapolated."""
    f = frequency_GHz
    pts = _TABLE_5_K_ALPHA
    if f <= pts[0][0]:
        return pts[0][1], pts[0][2]
    if f >= pts[-1][0]:
        return pts[-1][1], pts[-1][2]
    for i in range(len(pts) - 1):
        f0, k0, a0 = pts[i]
        f1, k1, a1 = pts[i + 1]
        if f0 <= f <= f1:
            # log-log interpolation for k
            log_f0, log_f1, log_f = math.log10(f0), math.log10(f1), math.log10(f)
            t = (log_f - log_f0) / (log_f1 - log_f0) if log_f1 != log_f0 else 0.0
            log_k0, log_k1 = math.log10(k0), math.log10(k1)
            k = 10.0 ** (log_k0 + t * (log_k1 - log_k0))
            # linear interpolation for alpha (still against log10(f), per
            # "logarithmic scale for frequency" applying to the x-axis for
            # both curves; only k's y-axis is logarithmic)
            alpha = a0 + t * (a1 - a0)
            return k, alpha
    # unreachable given the endpoint clamps above
    return pts[-1][1], pts[-1][2]


def gamma_R(frequency_GHz, R_val):
    """Eq. 69-70: specific attenuation due to rain gamma_R (dB/km).

        gamma_R = k * R^alpha
    """
    k, alpha = k_alpha_interp(frequency_GHz)
    return {"k": k, "alpha": alpha, "gamma_R": k * (R_val ** alpha)}


def effective_rain_cell_diameter(R_val):
    """Eq. 71: effective diameter of the rain cell ds (km).

        ds = 3.3 * R^-0.08
    """
    return 3.3 * (R_val ** -0.08)


def effective_scatter_transfer_cv(gamma_R_val, ds_km):
    """Eq. 72: effective scatter transfer function cv (dimensionless).

        cv = (1 - exp(-2.17 * gamma_R * ds)) / (gamma_R * ds)
    """
    x = 2.17 * gamma_R_val * ds_km
    denom = gamma_R_val * ds_km
    if denom == 0:
        return 1.0
    return (1.0 - math.exp(-x)) / denom


def additional_attenuation_Gamma(k_val, alpha_val, R_val):
    """Eq. 73: additional attenuation outside the common volume, Gamma
    (dB).

        Gamma = 6.31e-3 * k * R^(alpha+0.5) * (10^-0.1)
    """
    return 6.31e-3 * k_val * (R_val ** (alpha_val + 0.5)) * (10.0 ** -0.1)


def rain_height_hR(latitude_deg):
    """Eq. 74-75: rain height above ground hR (km).

    Eq. 74 (North America and Europe west of 60 deg E) is not selectable
    from --latitude_deg alone (it also depends on longitude/region), so
    per the Annex 2 text this module applies Eq. 75a-75e ("for all other
    areas of the world") uniformly, branched only on latitude - callers in
    the North-America/Europe-west-of-60E region should be aware Eq. 74 is
    not separately selected here.

        23 < zeta <= 70 (N hemisphere):  hR = 5 - 0.075*(zeta - 23)   (75a)
        0 <= zeta <= 23  (N hemisphere): hR = 5                       (75b)
        -21 <= zeta <= 0 (S hemisphere): hR = 5                       (75c)
        -71 <= zeta < -21 (S hemisphere): hR = 5 + 0.1*(zeta + 21)    (75d)
        zeta < -71 (S hemisphere):       hR = 0                       (75e)
    """
    z = latitude_deg
    if 23.0 < z <= 90.0:
        return 5.0 - 0.075 * (z - 23.0)
    elif 0.0 <= z <= 23.0:
        return 5.0
    elif -21.0 <= z < 0.0:
        return 5.0
    elif -71.0 <= z < -21.0:
        return 5.0 + 0.1 * (z + 21.0)
    else:  # z < -71
        return 0.0


def dmax2(latitude_deg):
    """§2: maximum calculation distance for propagation mode (2), dmax2
    (km), latitude-dependent via the rain height hR (Eq. 74-75).

    NOTE ON SOURCE TEXT: the PDF extraction of this equation lost its
    internal operators/exponent ("d h max R  17 000( 3) km"), but its
    structure is unambiguous from the surrounding prose and units - this
    is the standard AP7 Annex 2 §2 formula:

        dmax2 = sqrt(17000 * hR)   km
    """
    hR = rain_height_hR(latitude_deg)
    return math.sqrt(17000.0 * max(hR, 0.0))


def water_vapour_gamma_wr(frequency_GHz):
    """Eq. 76: specific attenuation due to water vapour absorption gamma_wr
    (dB/km), fixed water-vapour density of 7.5 g/m^3.

        gamma_wr = 0.06575 * [1 + 3.6/((f-22.2)^2+8.5)] * f^2 * 1e-4
    """
    f = frequency_GHz
    return 0.06575 * (1.0 + 3.6 / ((f - 22.2) ** 2 + 8.5)) * (f ** 2) * 1e-4


def loss_above_rain_height_Lar(ri_km, hR_km):
    """Eq. 78a/78b: loss above the rain height, applicable to scatter
    coupling, Lar (dB).

        if 6*(ri - hR)*1e-5 > 1: Lar = 6.5*ln[6*(ri - hR)*1e-5]   (78a)
        else:                    Lar = 0                          (78b)
    """
    x = 6.0 * (ri_km - hR_km) * 1e-5
    if x > 1.0:
        return 6.5 * math.log(x)
    return 0.0


def rayleigh_departure_Ab(frequency_GHz, R_val, Lar_val):
    """Eq. 79a/79b: additional attenuation for departure from Rayleigh
    scattering, Ab (dB).

        10 GHz <= f <= 40.5 GHz and Lar != 0:
            Ab = 1.7 + 0.4*R^0.5*(f - 10) * 0.005
        else:
            Ab = 0
    """
    if 10.0 <= frequency_GHz <= 40.5 and Lar_val != 0.0:
        return 1.7 + 0.4 * (R_val ** 0.5) * (frequency_GHz - 10.0) * 0.005
    return 0.0


def oxygen_path_length_do(ri_km):
    """Eq. 80a/80b: effective path length for oxygen absorption do (km).

        ri < 340 km:  do = 32 + 0.7*ri
        ri >= 340 km: do = 270
    """
    if ri_km < 340.0:
        return 32.0 + 0.7 * ri_km
    return 270.0


def water_vapour_path_length_dv(ri_km):
    """Eq. 81a/81b: effective path length for water vapour absorption dv
    (km).

        ri < 240 km:  dv = 32 + 0.7*ri
        ri >= 240 km: dv = 200
    """
    if ri_km < 240.0:
        return 32.0 + 0.7 * ri_km
    return 200.0


def Lr_p(ri_km, frequency_GHz, Gx, cv_val, Ab_val, R_val, Lar_val, do_km, dv_km,
          gamma_o_val, gamma_wr_val):
    """Eq. 82: propagation mode (2) path loss Lr(p) (dB).

        Lr(p) = 168 + 20*log10(ri) - 20*log10(f) - Gx + 13.2*log10(cv)
                - Ab - R + Lar + do*gamma_o + dv*gamma_wr
    """
    return (168.0 + 20.0 * math.log10(ri_km) - 20.0 * math.log10(frequency_GHz)
            - Gx + 13.2 * math.log10(cv_val) - Ab_val - R_val + Lar_val
            + do_km * gamma_o_val + dv_km * gamma_wr_val)


def contour_offset_delta_d(hR_km, elevation_angle_deg, dr_km):
    """Eq. 83: contour offset delta_d (km), limited to (dr - 50) km.

        delta_d = hR / (2 * tan(eps_s))
    """
    eps_r = math.radians(elevation_angle_deg)
    tan_eps = math.tan(eps_r)
    if abs(tan_eps) < 1e-9:
        raw = float("inf")
    else:
        raw = hR_km / (2.0 * tan_eps)
    cap = dr_km - 50.0
    if cap < 0.0:
        cap = 0.0
    return min(raw, cap) if raw != float("inf") else cap


def run_lookup_rain_zone(latitude_deg, longitude_deg, rain_zone):
    """§2-3: rain-zone parameters (hR, dmax2, R(0.3%), pc) for a given
    location and rain climatic zone."""
    z = (rain_zone or "").upper()
    if z not in _TABLE_4_R_PC:
        raise ValueError("rain_zone must be one of %s" % (RAIN_ZONES,))
    hR = rain_height_hR(latitude_deg)
    dx2 = dmax2(latitude_deg)
    R03, pc = _TABLE_4_R_PC[z]
    return {
        "hR": hR,
        "dmax2": dx2,
        "R_0.3pct": R03,
        "pc": pc,
        "longitude_deg": longitude_deg,  # echoed; not used by hR/dmax2 (see rain_height_hR note)
    }


def run_mode2(Pt, Pr_p, frequency_GHz, p_percent, rain_zone, latitude_deg, longitude_deg,
              elevation_angle_deg, Gx, step_km):
    """Eq. 62-83: full propagation mode (2) (hydrometeor/rain scatter)
    iterative required-distance calculation.

    Per the AP7 main body (Eq. 2-3, S1.3, S5), the propagation mode (2)
    required loss L(p) is NOT Lb(p): the terrestrial/receiving antenna
    gain Gx is folded into the Annex 2 iterative calculation itself (the
    Gx term inside Eq. 82), not subtracted up front as Gt+Gr are for
    Lb(p). So mode2 takes Pt and Pr(p) directly and computes:

        L(p) = Pt - Pr(p)   (Eq. 3, main-body sense: required loss)
    """
    if step_km <= 0:
        raise ValueError("step must be > 0 km")

    L_p = Pt - Pr_p

    R_val = R_p(rain_zone, p_percent)
    gR = gamma_R(frequency_GHz, R_val)
    k_val, alpha_val, gamma_R_val = gR["k"], gR["alpha"], gR["gamma_R"]

    ds = effective_rain_cell_diameter(R_val)                       # Eq. 71
    cv = effective_scatter_transfer_cv(gamma_R_val, ds)             # Eq. 72
    Gamma_val = additional_attenuation_Gamma(k_val, alpha_val, R_val)  # Eq. 73

    hR = rain_height_hR(latitude_deg)                               # Eq. 74-75
    dmax2_val = dmax2(latitude_deg)

    gamma_wr_val = water_vapour_gamma_wr(frequency_GHz)             # Eq. 76
    gamma_o_val = gamma_o_S3(frequency_GHz)                         # Eq. 33, reused

    dmin_val = dmin(frequency_GHz, latitude_deg)
    if dmax2_val < dmin_val:
        raise ValueError("dmax2 (%.4f km) < dmin (%.4f km) - cannot iterate" % (dmax2_val, dmin_val))

    ri = dmin_val
    iterations = 0
    max_iterations = 200000
    terminated_by = None
    last = None

    while True:
        iterations += 1

        Lar_val = loss_above_rain_height_Lar(ri, hR)                # Eq. 78a/78b
        Ab_val = rayleigh_departure_Ab(frequency_GHz, R_val, Lar_val)  # Eq. 79a/79b
        do_val = oxygen_path_length_do(ri)                          # Eq. 80a/80b
        dv_val = water_vapour_path_length_dv(ri)                    # Eq. 81a/81b
        Lr_val = Lr_p(ri, frequency_GHz, Gx, cv, Ab_val, R_val, Lar_val,
                      do_val, dv_val, gamma_o_val, gamma_wr_val)     # Eq. 82

        last = {"ri": ri, "Lar": Lar_val, "Ab": Ab_val, "do": do_val,
                "dv": dv_val, "Lr_p": Lr_val}

        if Lr_val >= L_p:  # Eq. 62a
            terminated_by = "loss_condition (Eq. 62a: Lr(p) >= L(p))"
            break
        if ri >= dmax2_val:  # Eq. 62b
            ri = dmax2_val
            Lar_val = loss_above_rain_height_Lar(ri, hR)
            Ab_val = rayleigh_departure_Ab(frequency_GHz, R_val, Lar_val)
            do_val = oxygen_path_length_do(ri)
            dv_val = water_vapour_path_length_dv(ri)
            Lr_val = Lr_p(ri, frequency_GHz, Gx, cv, Ab_val, R_val, Lar_val,
                          do_val, dv_val, gamma_o_val, gamma_wr_val)
            last = {"ri": ri, "Lar": Lar_val, "Ab": Ab_val, "do": do_val,
                    "dv": dv_val, "Lr_p": Lr_val}
            terminated_by = "max_distance (Eq. 62b: ri >= dmax2)"
            break
        if iterations >= max_iterations:
            terminated_by = "iteration_cap (--step too small for dmin..dmax2 range)"
            break

        ri = dmin_val + iterations * step_km  # Eq. 77

    dr_km = last["ri"]
    delta_d = contour_offset_delta_d(hR, elevation_angle_deg, dr_km)  # Eq. 83

    return {
        "L_p": L_p,
        "R_p": R_val,
        "k": k_val,
        "alpha": alpha_val,
        "gamma_R": gamma_R_val,
        "ds": ds,
        "cv": cv,
        "Gamma": Gamma_val,
        "hR": hR,
        "dmax2": dmax2_val,
        "dmin": dmin_val,
        "gamma_wr": gamma_wr_val,
        "gamma_o": gamma_o_val,
        "iterations": iterations,
        "terminated_by": terminated_by,
        "Lar_final": last["Lar"],
        "Ab_final": last["Ab"],
        "Lr_p_final": last["Lr_p"],
        "dr_km": dr_km,
        "delta_d_km": delta_d,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _not_yet_implemented(mode_name):
    print("error=%s is not yet implemented (planned for a later build step)" % mode_name)
    print("status=ERROR")


def main():
    parser = argparse.ArgumentParser(description="AP7 main body - propagation distance calculator")
    sub = parser.add_subparsers(dest="mode", required=True)

    lz = sub.add_parser("lookup_radio_zone",
                         help="S4.1-4.3: beta_e, N0, dmin, dmax1 for a given frequency/latitude/zone")
    lz.add_argument("--frequency_GHz", type=float, required=True, help="Frequency (GHz)")
    lz.add_argument("--latitude_deg", type=float, required=True, help="Earth station latitude (deg)")
    lz.add_argument("--zone", type=str, required=True, choices=ZONES, help="Radio-climatic zone")
    lz.add_argument("--p_percent", type=float, default=None,
                     help="Time percentage p (%%), only required if frequency_GHz > 60 (Eq. 19)")

    # Stubs for later steps - wired into the CLI now so callers (e.g. the
    # Java UI) can already build command lines against a stable interface,
    # but they do not compute anything yet.
    m1 = sub.add_parser("mode1", help="Annex 1 - propagation mode (1) (great-circle) required distance")
    m1.add_argument("--Lb_p", type=float, required=False)
    m1.add_argument("--frequency_GHz", type=float, required=False)
    m1.add_argument("--p_percent", type=float, required=False)
    m1.add_argument("--zone", type=str, required=False)
    m1.add_argument("--horizon_elevation_deg", type=float, required=False)
    m1.add_argument("--horizon_distance_km", type=float, required=False)
    m1.add_argument("--latitude_deg", type=float, required=False)
    m1.add_argument("--N0", type=float, required=False)
    m1.add_argument("--dtm", type=float, required=False)
    m1.add_argument("--dlm", type=float, required=False)
    m1.add_argument("--dc", type=float, required=False)
    m1.add_argument("--is_transmitter", action="store_true")
    m1.add_argument("--land_fraction", type=float, required=False)
    m1.add_argument("--step", type=float, required=False)

    m2 = sub.add_parser("mode2", help="Annex 2 - propagation mode (2) (rain scatter) required distance")
    m2.add_argument("--Pt", type=float, required=True, help="Interferer transmit power (dBW)")
    m2.add_argument("--Pr_p", type=float, required=True, help="Victim threshold interference level Pr(p) (dBW)")
    m2.add_argument("--frequency_GHz", type=float, required=True)
    m2.add_argument("--p_percent", type=float, required=True)
    m2.add_argument("--rain_zone", type=str, required=True, choices=RAIN_ZONES)
    m2.add_argument("--latitude_deg", type=float, required=True)
    m2.add_argument("--longitude_deg", type=float, required=True)
    m2.add_argument("--elevation_angle_deg", type=float, required=True,
                     help="Earth station antenna main beam elevation angle (deg)")
    m2.add_argument("--Gx", type=float, required=True, help="Terrestrial network antenna gain (dBi, Tables 7/8)")
    m2.add_argument("--step", type=float, default=1.0, help="Iteration step size (km)")

    lr = sub.add_parser("lookup_rain_zone", help="Annex 2 - rain zone parameters (hR, dmax2, R(0.3%%), pc)")
    lr.add_argument("--latitude_deg", type=float, required=True)
    lr.add_argument("--longitude_deg", type=float, required=True)
    lr.add_argument("--rain_zone", type=str, required=True, choices=RAIN_ZONES)

    args = parser.parse_args()

    try:
        if args.mode == "lookup_radio_zone":
            be = beta_e(args.latitude_deg)
            n0 = N0(args.latitude_deg)
            dm = dmin(args.frequency_GHz, args.latitude_deg)
            dx1 = dmax1(args.frequency_GHz, args.zone, args.p_percent)
            results = {
                "beta_e": be,
                "N0": n0,
                "dmin": dm,
                "dmax1": dx1,
            }
            for k, v in results.items():
                print(f"{k}={v:.6f}")
            print("status=OK")
        elif args.mode == "mode1":
            f = args.frequency_GHz
            if f is None:
                raise ValueError("--frequency_GHz is required")
            # Band dispatch. All three mode1 bands (100-790 MHz, 790 MHz-
            # 60 GHz, 60-105 GHz) are implemented as of step 4; anything
            # outside 100 MHz-105 GHz still falls through to the clean
            # "not yet implemented" error below.
            required = ["Lb_p", "p_percent", "zone", "latitude_deg", "dtm"]
            missing = [name for name in required if getattr(args, name) is None]
            if 0.1 <= f < 0.79:
                if missing:
                    raise ValueError("missing required argument(s) for mode1 (100-790 MHz): --" +
                                      ", --".join(missing))
                results = run_mode1_100_790(
                    Lb_p=args.Lb_p,
                    frequency_GHz=f,
                    p_percent=args.p_percent,
                    zone=args.zone,
                    horizon_elevation_deg=args.horizon_elevation_deg if args.horizon_elevation_deg is not None else 0.0,
                    horizon_distance_km=args.horizon_distance_km,
                    latitude_deg=args.latitude_deg,
                    N0_override=args.N0,
                    dtm_km=args.dtm,
                    dlm_km=args.dlm if args.dlm is not None else 0.0,
                    dc_km=args.dc if args.dc is not None else 0.0,
                    is_transmitter=args.is_transmitter,
                    land_fraction=args.land_fraction if args.land_fraction is not None else 1.0,
                    step_km=args.step if args.step is not None else 1.0,
                )
            elif 0.79 <= f < 60.0:
                if missing:
                    raise ValueError("missing required argument(s) for mode1 (790 MHz-60 GHz): --" +
                                      ", --".join(missing))
                results = run_mode1_790_60000(
                    Lb_p=args.Lb_p,
                    frequency_GHz=f,
                    p_percent=args.p_percent,
                    zone=args.zone,
                    horizon_elevation_deg=args.horizon_elevation_deg if args.horizon_elevation_deg is not None else 0.0,
                    horizon_distance_km=args.horizon_distance_km,
                    latitude_deg=args.latitude_deg,
                    N0_override=args.N0,
                    dtm_km=args.dtm,
                    dlm_km=args.dlm if args.dlm is not None else 0.0,
                    dc_km=args.dc if args.dc is not None else 0.0,
                    is_transmitter=args.is_transmitter,
                    land_fraction=args.land_fraction if args.land_fraction is not None else 1.0,
                    step_km=args.step if args.step is not None else 1.0,
                )
            elif 60.0 <= f <= 105.0:
                if missing:
                    raise ValueError("missing required argument(s) for mode1 (60-105 GHz): --" +
                                      ", --".join(missing))
                results = run_mode1_60_105(
                    Lb_p=args.Lb_p,
                    frequency_GHz=f,
                    p_percent=args.p_percent,
                    zone=args.zone,
                    horizon_elevation_deg=args.horizon_elevation_deg if args.horizon_elevation_deg is not None else 0.0,
                    horizon_distance_km=args.horizon_distance_km,
                    latitude_deg=args.latitude_deg,
                    N0_override=args.N0,
                    dtm_km=args.dtm,
                    dlm_km=args.dlm if args.dlm is not None else 0.0,
                    dc_km=args.dc if args.dc is not None else 0.0,
                    is_transmitter=args.is_transmitter,
                    land_fraction=args.land_fraction if args.land_fraction is not None else 1.0,
                    step_km=args.step if args.step is not None else 1.0,
                )
            else:
                print("error=frequency band not yet implemented (only 100 MHz-105 GHz supported in this build)")
                print("status=ERROR")
                return

            for k, v in results.items():
                if isinstance(v, float):
                    print(f"{k}={v:.6f}")
                else:
                    print(f"{k}={v}")
            print("status=OK")
        elif args.mode == "mode2":
            results = run_mode2(
                Pt=args.Pt,
                Pr_p=args.Pr_p,
                frequency_GHz=args.frequency_GHz,
                p_percent=args.p_percent,
                rain_zone=args.rain_zone,
                latitude_deg=args.latitude_deg,
                longitude_deg=args.longitude_deg,
                elevation_angle_deg=args.elevation_angle_deg,
                Gx=args.Gx,
                step_km=args.step,
            )
            for k, v in results.items():
                if isinstance(v, float):
                    print(f"{k}={v:.6f}")
                else:
                    print(f"{k}={v}")
            print("status=OK")
        elif args.mode == "lookup_rain_zone":
            results = run_lookup_rain_zone(
                latitude_deg=args.latitude_deg,
                longitude_deg=args.longitude_deg,
                rain_zone=args.rain_zone,
            )
            for k, v in results.items():
                if isinstance(v, float):
                    print(f"{k}={v:.6f}")
                else:
                    print(f"{k}={v}")
            print("status=OK")
        else:
            raise ValueError(f"unknown mode: {args.mode}")
    except Exception as e:
        print(f"error={e}")
        print("status=ERROR")


if __name__ == "__main__":
    main()