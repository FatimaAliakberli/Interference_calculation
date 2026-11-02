#!/usr/bin/env python3
"""
AP7 Annex 3 parameter calculations (ITU-R Appendix 7, equations 84-89).

Computes, given:
  zeta  - latitude of the earth station (deg, + North / - South)
  i     - latitude of the sub-satellite point (deg, + North / - South)
  delta - difference in longitude between earth station and space station
          (deg, + East / - West)
  K     - orbit radius / Earth radius (default 6.62 for the GSO)

the following:
  psi_s(i,delta)    - Eq. 84 - great-circle arc, earth station -> sub-satellite point
  eps_s(i,delta)    - Eq. 85 - space station elevation angle as seen from earth station
  alpha_s0(i,delta) - Eq. 86 - reference (unsigned) space station azimuth
  alpha_s(i,delta)  - Eq. 87/88 - signed space station azimuth (east/west branch)
  phi(alpha,i,delta)- Eq. 89 - off-axis angle between direction (alpha, eps_h) and
                                the space station direction (alpha_s, eps_s)
                                (requires the extra inputs alpha, eps_h)

Can be used either as a library (import calc) or as a CLI (see main()).
"""

import argparse
import math

K_DEFAULT = 6.62


def _clamp(x, lo=-1.0, hi=1.0):
    """Guard against floating point drift pushing acos/asin arguments
    just outside [-1, 1]."""
    return max(lo, min(hi, x))


def psi_s(zeta, i, delta):
    """Eq. 84: great-circle arc between the earth station and the sub-satellite point."""
    zeta_r, i_r, delta_r = math.radians(zeta), math.radians(i), math.radians(delta)
    val = math.sin(zeta_r) * math.sin(i_r) + math.cos(zeta_r) * math.cos(i_r) * math.cos(delta_r)
    return math.degrees(math.acos(_clamp(val)))


def eps_s(zeta, i, delta, K=K_DEFAULT):
    """Eq. 85: space station elevation angle as seen from the earth station."""
    psi = psi_s(zeta, i, delta)
    psi_r = math.radians(psi)
    cospsi = math.cos(psi_r)
    denom_sq = 1 + K * K - 2 * K * cospsi
    if denom_sq <= 0:
        raise ValueError("eps_s undefined: 1 + K^2 - 2K*cos(psi_s) <= 0")
    denom = math.sqrt(denom_sq)
    val = (K * cospsi - 1) / denom
    return math.degrees(math.asin(_clamp(val)))


def alpha_s0(zeta, i, delta):
    """Eq. 86: reference space station azimuth (0-180 deg, unsigned)."""
    psi = psi_s(zeta, i, delta)
    zeta_r, i_r, psi_r = math.radians(zeta), math.radians(i), math.radians(psi)
    sinpsi = math.sin(psi_r)
    coszeta = math.cos(zeta_r)
    if abs(sinpsi) < 1e-9 or abs(coszeta) < 1e-9:
        raise ValueError(
            "alpha_s0 undefined (earth station at sub-satellite point, "
            "at a pole, or psi_s = 0/180 deg)"
        )
    val = (math.sin(i_r) - math.sin(zeta_r) * math.cos(psi_r)) / (coszeta * sinpsi)
    return math.degrees(math.acos(_clamp(val)))


def alpha_s(zeta, i, delta):
    """Eq. 87/88: signed space station azimuth.
    delta >= 0 (space station east of earth station) -> Eq. 87
    delta <  0 (space station west of earth station) -> Eq. 88
    """
    a0 = alpha_s0(zeta, i, delta)
    if delta >= 0:
        return a0
    return 360.0 - a0


def phi(alpha, eps_h, zeta, i, delta, K=K_DEFAULT):
    """Eq. 89: off-axis angle between the direction under consideration
    (alpha, eps_h) and the space station direction (alpha_s, eps_s)."""
    es = eps_s(zeta, i, delta, K)
    as_ = alpha_s(zeta, i, delta)
    eps_h_r, es_r = math.radians(eps_h), math.radians(es)
    diff_r = math.radians(alpha - as_)
    val = math.cos(eps_h_r) * math.cos(es_r) * math.cos(diff_r) + math.sin(eps_h_r) * math.sin(es_r)
    return math.degrees(math.acos(_clamp(val)))


def compute_all(zeta, i, delta, K=K_DEFAULT, alpha=None, eps_h=None):
    """Convenience wrapper returning a dict of every computable quantity."""
    out = {}
    out["psi_s"] = psi_s(zeta, i, delta)
    out["eps_s"] = eps_s(zeta, i, delta, K)
    out["alpha_s0"] = alpha_s0(zeta, i, delta)
    out["alpha_s"] = alpha_s(zeta, i, delta)
    if alpha is not None and eps_h is not None:
        out["phi"] = phi(alpha, eps_h, zeta, i, delta, K)
    return out


# ---------------------------------------------------------------------------
# Annex 3 section 2, Case 1 / Case 2: determination of phi(alpha) (Eq. 90-96)
# ---------------------------------------------------------------------------

def delta_s(is_max):
    """Eq. 96: maximum longitude change from the nominal sub-satellite
    longitude, for a satellite with maximum orbital inclination is_max
    (degrees)."""
    return (is_max ** 2) / 15.0


def phi_case1(alpha, eps_h, zeta, delta0, K=K_DEFAULT):
    """Case 1 (Eq. 90): no orbital inclination.
    phi(alpha) = phi(alpha, 0, delta0) -- i.e. Eq. 84-89 applied directly
    with i = 0, delta = delta0."""
    out = compute_all(zeta, 0.0, delta0, K, alpha, eps_h)
    out["case"] = "1 (no inclination, Eq. 90)"
    return out


def _frange_inclusive(lo, hi, step):
    """Points from lo to hi (inclusive of both endpoints) spaced by step."""
    if step <= 0:
        raise ValueError("step must be positive")
    if hi < lo:
        lo, hi = hi, lo
    n_steps = int(math.floor((hi - lo) / step + 1e-9))
    pts = [lo + k * step for k in range(n_steps + 1)]
    if not pts or pts[-1] < hi - 1e-9:
        pts.append(hi)
    return pts


def _scan_min(alpha, eps_h, zeta, K, i_values, delta_values):
    """Evaluates Eq. 89 over every (i, delta) pair from the two supplied
    iterables (one of which is normally a single fixed value) and returns
    (min_phi, i_at_min, delta_at_min). Points where the geometry is
    undefined are skipped."""
    best = None
    for iv in i_values:
        for dv in delta_values:
            try:
                v = phi(alpha, eps_h, zeta, iv, dv, K)
            except ValueError:
                continue
            if best is None or v < best[0]:
                best = (v, iv, dv)
    if best is None:
        raise ValueError("phi undefined over the entire search range")
    return best


def phi_case2(alpha, eps_h, zeta, delta0, is_max, K=K_DEFAULT, step=1.0):
    """Case 2 (Eq. 91-96): slightly inclined orbit.
    Bounds the satellite's trajectory by a box in (i, delta) space and
    scans its 4 edges for the minimum off-axis angle."""
    ds = delta_s(is_max)
    d_lo, d_hi = delta0 - ds, delta0 + ds
    i_lo, i_hi = -is_max, is_max

    delta_pts = _frange_inclusive(d_lo, d_hi, step)
    i_pts = _frange_inclusive(i_lo, i_hi, step)

    # Eq. 92: top edge, i = +is, delta scanned
    phi1, _, d1 = _scan_min(alpha, eps_h, zeta, K, [is_max], delta_pts)
    # Eq. 93: bottom edge, i = -is, delta scanned
    phi2, _, d2 = _scan_min(alpha, eps_h, zeta, K, [-is_max], delta_pts)
    # Eq. 94: left edge, delta = delta0 - delta_s, i scanned
    phi3, i3, _ = _scan_min(alpha, eps_h, zeta, K, i_pts, [d_lo])
    # Eq. 95: right edge, delta = delta0 + delta_s, i scanned
    phi4, i4, _ = _scan_min(alpha, eps_h, zeta, K, i_pts, [d_hi])

    candidates = {
        "phi1": (phi1, "i=+is edge (Eq. 92), delta=%.3f" % d1),
        "phi2": (phi2, "i=-is edge (Eq. 93), delta=%.3f" % d2),
        "phi3": (phi3, "delta=delta0-delta_s edge (Eq. 94), i=%.3f" % i3),
        "phi4": (phi4, "delta=delta0+delta_s edge (Eq. 95), i=%.3f" % i4),
    }
    best_key = min(candidates, key=lambda k: candidates[k][0])

    return {
        "delta_s": ds,
        "phi1": phi1,
        "phi2": phi2,
        "phi3": phi3,
        "phi4": phi4,
        "phi": candidates[best_key][0],
        "phi_source": candidates[best_key][1],
        "case": "2 (slightly inclined orbit, Eq. 91-96)",
    }


# ---------------------------------------------------------------------------
# Annex 3 section 3: antenna gain towards the horizon G(phi) (Eq. 97)
# ---------------------------------------------------------------------------

def estimate_d_over_lambda(gmax):
    """Estimate D/lambda from the main beam axis gain Gmax (dBi), for use
    when D/lambda is not directly available:
        Gmax (approx) = 20*log10(D/lambda) + 7.7
    => D/lambda = 10 ** ((Gmax - 7.7) / 20)
    """
    return 10 ** ((gmax - 7.7) / 20.0)


def g1_gain(d_over_lambda):
    """First side-lobe gain G1 (dBi). Valid for D/lambda >= 35."""
    if d_over_lambda >= 100:
        return -1.0 + 15.0 * math.log10(d_over_lambda)
    elif d_over_lambda >= 35:
        return -21.0 + 25.0 * math.log10(d_over_lambda)
    raise ValueError("This pattern requires D/lambda >= 35 (got %.3f)" % d_over_lambda)


def phi_m_deg(d_over_lambda, gmax, g1):
    """phi_m (degrees): off-axis angle where the main-lobe curve meets G1."""
    diff = gmax - g1
    if diff < 0:
        raise ValueError("Gmax must be >= G1 (got Gmax=%.3f, G1=%.3f)" % (gmax, g1))
    return (20.0 / d_over_lambda) * math.sqrt(diff)


def phi_r_deg(d_over_lambda):
    """phi_r (degrees): off-axis angle where G1 meets the 29-25*log(phi) curve."""
    if d_over_lambda >= 100:
        return 15.85 * (d_over_lambda ** -0.6)
    elif d_over_lambda >= 35:
        return 100.0 / d_over_lambda
    raise ValueError("This pattern requires D/lambda >= 35 (got %.3f)" % d_over_lambda)


def gain_at_phi(phi_val, d_over_lambda, gmax):
    """Eq. 97: horizon antenna gain G(phi) (dBi) at off-axis angle phi_val (deg)."""
    if not (0 <= phi_val <= 180):
        raise ValueError("phi must be between 0 and 180 degrees")

    g1 = g1_gain(d_over_lambda)
    phim = phi_m_deg(d_over_lambda, gmax, g1)
    phir = phi_r_deg(d_over_lambda)

    if phim > phir:
        raise ValueError(
            "phi_m (%.4f deg) > phi_r (%.4f deg): the antenna parameters given "
            "do not produce a physically ordered pattern; double-check Gmax / D/lambda." % (phim, phir)
        )

    if phi_val < phim:
        g = gmax - 2.5e-3 * (d_over_lambda * phi_val) ** 2
        branch = "0 <= phi < phi_m (main lobe)"
    elif phi_val < phir:
        g = g1
        branch = "phi_m <= phi < phi_r (near side lobes, G1)"
    elif phi_val < 36:
        g = 29.0 - 25.0 * math.log10(phi_val)
        branch = "phi_r <= phi < 36 deg"
    else:
        g = -10.0
        branch = "36 <= phi <= 180 deg"

    return {
        "d_over_lambda": d_over_lambda,
        "g1": g1,
        "phi_m": phim,
        "phi_r": phir,
        "gain": g,
        "branch": branch,
    }


def main():
    parser = argparse.ArgumentParser(description="AP7 Annex 3 calculator")
    sub = parser.add_subparsers(dest="mode", required=True)

    geo = sub.add_parser("geometry", help="Eq. 84-89: general geometry for given zeta, i, delta")
    geo.add_argument("--zeta", type=float, required=True, help="Earth station latitude (deg)")
    geo.add_argument("--i", type=float, required=True, help="Sub-satellite latitude (deg)")
    geo.add_argument("--delta", type=float, required=True, help="Longitude difference (deg)")
    geo.add_argument("--K", type=float, default=K_DEFAULT, help="Orbit radius / Earth radius")
    geo.add_argument("--alpha", type=float, default=None, help="Azimuth under consideration (deg)")
    geo.add_argument("--eps_h", type=float, default=None, help="Horizon elevation at that azimuth (deg)")

    c1 = sub.add_parser("case1", help="Eq. 90: phi(alpha) with no orbital inclination")
    c1.add_argument("--zeta", type=float, required=True, help="Earth station latitude (deg)")
    c1.add_argument("--delta0", type=float, required=True, help="Nominal longitude difference (deg)")
    c1.add_argument("--K", type=float, default=K_DEFAULT, help="Orbit radius / Earth radius")
    c1.add_argument("--alpha", type=float, required=True, help="Azimuth under consideration (deg)")
    c1.add_argument("--eps_h", type=float, required=True, help="Horizon elevation at that azimuth (deg)")

    c2 = sub.add_parser("case2", help="Eq. 91-96: phi(alpha) with a slightly inclined orbit")
    c2.add_argument("--zeta", type=float, required=True, help="Earth station latitude (deg)")
    c2.add_argument("--delta0", type=float, required=True, help="Nominal longitude difference (deg)")
    c2.add_argument("--is_max", type=float, required=True, help="Maximum orbital inclination is (deg)")
    c2.add_argument("--K", type=float, default=K_DEFAULT, help="Orbit radius / Earth radius")
    c2.add_argument("--step", type=float, default=1.0, help="Grid search step in i/delta (deg), 0.5-1.0 recommended")
    c2.add_argument("--alpha", type=float, required=True, help="Azimuth under consideration (deg)")
    c2.add_argument("--eps_h", type=float, required=True, help="Horizon elevation at that azimuth (deg)")

    est = sub.add_parser("estimate_dl", help="Estimate D/lambda from Gmax when D and/or lambda are unknown")
    est.add_argument("--gmax", type=float, required=True, help="Main beam axis antenna gain (dBi)")

    gain = sub.add_parser("gain", help="Eq. 97: horizon antenna gain G(phi)")
    gain.add_argument("--phi", type=float, required=True, help="Off-axis angle (deg)")
    gain.add_argument("--gmax", type=float, required=True, help="Main beam axis antenna gain (dBi)")
    gain.add_argument("--wavelength", type=float, default=None, help="Wavelength lambda (m)")
    gain.add_argument("--diameter", type=float, default=None, help="Antenna diameter D (m)")
    gain.add_argument("--d_over_lambda", type=float, default=None,
                       help="D/lambda directly (overrides wavelength/diameter if given)")

    args = parser.parse_args()

    # Simple "key=value" output, one per line: easy for any caller (Java, shell, etc.)
    # to parse without needing a JSON library.
    try:
        if args.mode == "geometry":
            results = compute_all(args.zeta, args.i, args.delta, args.K, args.alpha, args.eps_h)
        elif args.mode == "case1":
            results = phi_case1(args.alpha, args.eps_h, args.zeta, args.delta0, args.K)
        elif args.mode == "case2":
            results = phi_case2(args.alpha, args.eps_h, args.zeta, args.delta0, args.is_max, args.K, args.step)
        elif args.mode == "estimate_dl":
            results = {"d_over_lambda": estimate_d_over_lambda(args.gmax)}
        elif args.mode == "gain":
            if args.d_over_lambda is not None:
                dl = args.d_over_lambda
            elif args.wavelength is not None and args.diameter is not None:
                if args.wavelength <= 0:
                    raise ValueError("wavelength must be > 0")
                dl = args.diameter / args.wavelength
            else:
                raise ValueError(
                    "Provide D/lambda directly, or both wavelength and diameter, "
                    "or first use 'Estimate D/lambda from Gmax'."
                )
            results = gain_at_phi(args.phi, dl, args.gmax)
        else:
            raise ValueError(f"unknown mode: {args.mode}")

        for k, v in results.items():
            if isinstance(v, float):
                print(f"{k}={v:.6f}")
            else:
                print(f"{k}={v}")
        print("status=OK")
    except Exception as e:
        print(f"error={e}")
        print("status=ERROR")


if __name__ == "__main__":
    main()