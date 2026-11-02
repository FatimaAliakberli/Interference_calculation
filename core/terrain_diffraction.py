#!/usr/bin/env python3
"""
core/terrain_diffraction.py — ITU-R P.526 single knife-edge terrain
diffraction over a DEM-derived elevation profile.

This module is the piece `propagation_calculator.calculate_path_loss_with_
diffraction()` already imports (as `core.terrain_diffraction` / `terrain_
diffraction`) and calls — see the try/except ImportError block there. Its
absence is why every diffraction recheck was silently falling back to
"unavailable": the ImportError was being caught and turned into a
diffraction_warning, so diffraction_available stayed False for every
azimuth and nothing ever reached app.py's Step 8 table or Step 9 map.

Public API (both already assumed by propagation_calculator.py):
    extract_elevation_profile(es_lat, es_lon, target_lat, target_lon,
                               dem_path, num_points=100)
    calculate_knife_edge_diffraction(es_lat, es_lon, es_height_m,
                                      target_lat, target_lon, target_height_m,
                                      dem_path, frequency_GHz, num_points=100)

Both raise DiffractionUnavailable (never a bare ImportError/OSError) for
any condition that should trigger the "fall back to FSPL + gases only"
path in propagation_calculator.py — missing dependencies, a missing/
unreadable DEM, or a path with no usable DEM coverage.
"""
import math
import os

try:
    import numpy as np
except ImportError:
    np = None

try:
    import rasterio
    from rasterio.windows import Window
except ImportError:
    rasterio = None
    Window = None

try:
    from pyproj import Geod
except ImportError:
    Geod = None


SPEED_OF_LIGHT_M_S = 299_792_458.0
NU_CLEAR_LOS_THRESHOLD = -0.78  # ITU-R P.526: J(nu) = 0 for nu <= this

# Fraction of the total path length excluded from the knife-edge search at
# EACH end. This matters because nu = h * sqrt((2/lambda)*(1/d1 + 1/d2)):
# right next to either terminal, one of d1/d2 -> 0 while h also -> a small
# constant (h -> -terminal_height_m exactly at that terminal, by
# construction of the LoS line), so 1/sqrt(d) can dominate h shrinking and
# nu drifts toward 0 purely as a numerical artifact of sampling close to
# the antenna's own base — NOT a real terrain obstruction. This is most
# visible when a terminal height is 0 m (h -> 0 too), which silently
# produced a spurious ~6 dB "diffraction" floor over perfectly flat
# terrain before this guard was added. Excluding a small guard band at
# each end keeps the search to points that are actually candidate
# terrain obstacles.
_ENDPOINT_GUARD_FRACTION = 0.02


class DiffractionUnavailable(Exception):
    """
    Raised whenever a knife-edge diffraction loss cannot be computed for
    the requested path: missing numpy/rasterio/pyproj, a DEM path that
    doesn't exist or won't open, or a path with no usable DEM coverage.

    Callers (propagation_calculator.calculate_path_loss_with_diffraction)
    catch this specifically and fall back to plain FSPL + gaseous
    absorption, exactly as if no DEM had been supplied at all.
    """
    pass


def _check_dependencies():
    missing = []
    if np is None:
        missing.append("numpy")
    if rasterio is None:
        missing.append("rasterio")
    if Geod is None:
        missing.append("pyproj")
    if missing:
        raise DiffractionUnavailable(
            f"Terrain diffraction requires {', '.join(missing)} "
            f"(`pip install {' '.join(missing)}`)."
        )


def _safe_read_dem(src, lon, lat):
    """
    Read a single elevation value from an ALREADY-OPEN EPSG:4326 rasterio
    dataset. Returns None (not an exception) for any point outside the
    raster bounds, or at a nodata/NaN cell — mirrors horizon_core.py's
    safe_read_dem so behaviour stays consistent across the app.
    """
    try:
        if (lon < src.bounds.left or lon > src.bounds.right or
                lat < src.bounds.bottom or lat > src.bounds.top):
            return None

        row, col = src.index(lon, lat)
        if row < 0 or row >= src.height or col < 0 or col >= src.width:
            return None

        window = Window(col, row, 1, 1)
        elevation = src.read(1, window=window)
        if elevation.size == 0:
            return None

        val = float(elevation[0, 0])
        if src.nodata is not None and val == src.nodata:
            return None
        if math.isnan(val):
            return None
        return val
    except Exception:
        return None


def extract_elevation_profile(es_lat, es_lon, target_lat, target_lon,
                               dem_path, num_points=100):
    """
    Sample terrain elevation at `num_points` evenly spaced points along
    the geodesic path from (es_lat, es_lon) to (target_lat, target_lon),
    using an EPSG:4326 DEM GeoTIFF.

    Parameters
    ----------
    es_lat, es_lon : float
        Earth station latitude/longitude (decimal degrees).
    target_lat, target_lon : float
        Far-end point latitude/longitude (decimal degrees).
    dem_path : str
        Path to a DEM GeoTIFF (EPSG:4326).
    num_points : int
        Total number of samples along the path, including both
        endpoints. Default 100.

    Returns
    -------
    list of dict, ordered from the earth station (index 0) to the target
    (index -1):
        {
            "lat": float, "lon": float,
            "distance_km": float,          # geodesic distance from the ES
            "elevation_m": float or None,  # None where the DEM has no
                                            # coverage / nodata there
        }

    Raises
    ------
    DiffractionUnavailable
        if numpy/rasterio/pyproj aren't installed, dem_path doesn't
        exist, or the DEM can't be opened.
    ValueError
        if num_points < 2, or the two endpoints coincide.
    """
    _check_dependencies()
    if num_points < 2:
        raise ValueError("num_points must be >= 2")

    if not dem_path or not os.path.isfile(dem_path):
        raise DiffractionUnavailable(f"DEM file not found: {dem_path!r}")

    geod = Geod(ellps="WGS84")
    _, _, total_dist_m = geod.inv(es_lon, es_lat, target_lon, target_lat)
    if total_dist_m <= 0:
        raise ValueError("Earth station and target coordinates coincide (zero path length)")

    try:
        n_intermediate = max(num_points - 2, 0)
        intermediate = geod.npts(es_lon, es_lat, target_lon, target_lat, n_intermediate) \
            if n_intermediate > 0 else []
    except Exception as e:
        raise DiffractionUnavailable(f"Could not compute geodesic path points: {e}") from e

    lonlats = [(es_lon, es_lat)] + list(intermediate) + [(target_lon, target_lat)]

    try:
        src = rasterio.open(dem_path)
    except Exception as e:
        raise DiffractionUnavailable(f"Could not open DEM {dem_path!r}: {e}") from e

    profile = []
    try:
        n = len(lonlats)
        for i, (lon, lat) in enumerate(lonlats):
            frac = i / (n - 1)
            dist_km = (total_dist_m * frac) / 1000.0
            elev = _safe_read_dem(src, lon, lat)
            profile.append({
                "lat": lat, "lon": lon,
                "distance_km": dist_km, "elevation_m": elev,
            })
    finally:
        src.close()

    return profile


def calculate_knife_edge_diffraction(es_lat, es_lon, es_height_m,
                                      target_lat, target_lon, target_height_m,
                                      dem_path, frequency_GHz, num_points=100):
    """
    ITU-R P.526 single knife-edge diffraction loss over a DEM-derived
    terrain profile between an earth station and a target point.

    Method
    ------
    1. Sample the terrain profile between the two ends
       (extract_elevation_profile).
    2. Build the absolute height of the straight line-of-sight (LoS) at
       every sample point, interpolating linearly between the two
       terminals' absolute heights (terrain elevation + antenna/
       reference height above ground).
    3. At every intermediate sample, compute the terrain height above
       the LoS line (h, metres — positive means the terrain pokes up
       into the path) and the Fresnel-Kirchhoff diffraction parameter
       for a knife edge placed at that point:

           nu = h * sqrt( (2/lambda) * (1/d1 + 1/d2) )

       where d1, d2 are the distances (m) from each terminal to that
       sample and lambda is the wavelength (m) at frequency_GHz.
    4. The sample with the LARGEST nu is the worst-case (most
       obstructing) point and is treated as the single equivalent knife
       edge — valid because J(nu) is monotonically increasing in nu, so
       the point that maximises nu also maximises loss.
    5. Diffraction loss (ITU-R P.526):
           J(nu) = 6.9 + 20*log10( sqrt((nu-0.1)^2 + 1) + nu - 0.1 )   nu >  -0.78
           J(nu) = 0                                                   nu <= -0.78

    Parameters
    ----------
    es_lat, es_lon : float
        Earth station latitude/longitude (decimal degrees).
    es_height_m : float
        Earth station antenna/structure height above ground (m).
    target_lat, target_lon : float
        Far-end point latitude/longitude (decimal degrees).
    target_height_m : float
        Far-end reference height above ground (m).
    dem_path : str
        Path to a DEM GeoTIFF (EPSG:4326).
    frequency_GHz : float
        Operating frequency (GHz), used for the wavelength.
    num_points : int
        Number of DEM samples along the path. Default 100.

    Returns
    -------
    dict:
        {
            "diffraction_loss_db": float,  # J(nu) at the worst-case point, >= 0
            "nu": float,                   # Fresnel-Kirchhoff parameter there
            "h_m": float,                  # obstruction height above LoS (m)
            "d1_km": float, "d2_km": float,  # distances to the knife edge (km)
            "obstruction_lat": float, "obstruction_lon": float,
            "clear_line_of_sight": bool,   # True if nu <= -0.78 (no loss)
            "wavelength_m": float,
            "profile": list,               # the raw sampled profile (see
                                            # extract_elevation_profile)
        }

    Raises
    ------
    DiffractionUnavailable
        if the profile can't be computed (see extract_elevation_profile),
        or the DEM has no usable coverage along the path.
    ValueError
        if frequency_GHz <= 0, or the two endpoints coincide.
    """
    _check_dependencies()
    if frequency_GHz <= 0:
        raise ValueError("frequency_GHz must be > 0")

    profile = extract_elevation_profile(
        es_lat, es_lon, target_lat, target_lon, dem_path, num_points
    )

    valid = [p for p in profile if p["elevation_m"] is not None]
    if len(valid) < 3:
        raise DiffractionUnavailable(
            "DEM has no usable coverage along this path (fewer than 3 valid "
            "samples) — cannot evaluate terrain diffraction."
        )

    # Prefer the exact terminal samples; fall back to the nearest valid
    # sample at that end if the exact endpoint itself has no coverage
    # (e.g. right at the edge of the raster).
    es_elev = profile[0]["elevation_m"] if profile[0]["elevation_m"] is not None else valid[0]["elevation_m"]
    target_elev = profile[-1]["elevation_m"] if profile[-1]["elevation_m"] is not None else valid[-1]["elevation_m"]

    es_abs_height = es_elev + es_height_m
    target_abs_height = target_elev + target_height_m

    total_dist_km = profile[-1]["distance_km"]
    if total_dist_km <= 0:
        raise ValueError("Earth station and target coordinates coincide (zero path length)")
    total_dist_m = total_dist_km * 1000.0

    wavelength_m = SPEED_OF_LIGHT_M_S / (frequency_GHz * 1e9)

    guard_m = _ENDPOINT_GUARD_FRACTION * total_dist_m

    best = None  # (nu, h, d1_m, d2_m, lat, lon)
    for p in profile[1:-1]:
        if p["elevation_m"] is None:
            continue
        d1_m = p["distance_km"] * 1000.0
        d2_m = total_dist_m - d1_m
        if d1_m < guard_m or d2_m < guard_m:
            # Too close to a terminal to be a meaningful separate knife
            # edge — see _ENDPOINT_GUARD_FRACTION above.
            continue

        frac = d1_m / total_dist_m
        los_height = es_abs_height + (target_abs_height - es_abs_height) * frac
        h = p["elevation_m"] - los_height

        nu = h * math.sqrt((2.0 / wavelength_m) * (1.0 / d1_m + 1.0 / d2_m))

        if best is None or nu > best[0]:
            best = (nu, h, d1_m, d2_m, p["lat"], p["lon"])

    if best is None:
        raise DiffractionUnavailable(
            "No usable intermediate DEM samples along this path — cannot "
            "evaluate terrain diffraction."
        )

    nu, h, d1_m, d2_m, obs_lat, obs_lon = best

    if nu > NU_CLEAR_LOS_THRESHOLD:
        j_nu = 6.9 + 20.0 * math.log10(math.sqrt((nu - 0.1) ** 2 + 1.0) + nu - 0.1)
        j_nu = max(j_nu, 0.0)  # guards only against floating-point drift right at the boundary
    else:
        j_nu = 0.0

    return {
        "diffraction_loss_db": j_nu,
        "nu": nu,
        "h_m": h,
        "d1_km": d1_m / 1000.0,
        "d2_km": d2_m / 1000.0,
        "obstruction_lat": obs_lat,
        "obstruction_lon": obs_lon,
        "clear_line_of_sight": nu <= NU_CLEAR_LOS_THRESHOLD,
        "wavelength_m": wavelength_m,
        "profile": profile,
    }


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="ITU-R P.526 single knife-edge diffraction loss from a DEM"
    )
    parser.add_argument("--dem", required=True, help="Path to DEM GeoTIFF (EPSG:4326)")
    parser.add_argument("--es-lat", type=float, required=True)
    parser.add_argument("--es-lon", type=float, required=True)
    parser.add_argument("--es-height", type=float, default=10.0, help="ES height above ground (m)")
    parser.add_argument("--target-lat", type=float, required=True)
    parser.add_argument("--target-lon", type=float, required=True)
    parser.add_argument("--target-height", type=float, default=0.0, help="Target height above ground (m)")
    parser.add_argument("--freq-ghz", type=float, required=True)
    parser.add_argument("--num-points", type=int, default=100)
    args = parser.parse_args()

    try:
        result = calculate_knife_edge_diffraction(
            es_lat=args.es_lat, es_lon=args.es_lon, es_height_m=args.es_height,
            target_lat=args.target_lat, target_lon=args.target_lon,
            target_height_m=args.target_height,
            dem_path=args.dem, frequency_GHz=args.freq_ghz, num_points=args.num_points,
        )
        for k, v in result.items():
            if k == "profile":
                continue
            print(f"{k}={v}")
        print("status=OK")
    except Exception as e:
        print(f"error={e}")
        print("status=ERROR")