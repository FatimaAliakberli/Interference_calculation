#!/usr/bin/env python3
"""
Core horizon-profile calculation functions for EPSG:4326 / WGS84 DEMs.

Refactored from the original single-station script so the DEM is opened
ONCE per station point (or once per worker process, for batch jobs)
instead of once per elevation sample. The original per-sample
rasterio.open() call is far too slow once you need thousands of
profiles for a grid.
"""
import math
import numpy as np
from rasterio.windows import Window


def safe_read_dem(src, lon, lat):
    """Read elevation from an ALREADY-OPEN EPSG:4326 rasterio dataset."""
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

        elev_value = elevation[0, 0]

        if src.nodata is not None and elev_value == src.nodata:
            return None
        if np.isnan(elev_value):
            return None

        return float(elev_value)

    except Exception:
        return None


def calculate_horizon_in_direction(src, station_lat, station_lon,
                                    station_alt, azimuth, geod,
                                    max_distance_km=100, step_km=0.5):
    max_elevation_angle = -90.0
    horizon_distance = 0.0
    max_distance_m = max_distance_km * 1000
    step_m = step_km * 1000

    distance = step_m
    valid_points = 0

    while distance <= max_distance_m:
        lon2, lat2, _ = geod.fwd(station_lon, station_lat, azimuth, distance)
        terrain_elev = safe_read_dem(src, lon2, lat2)

        if terrain_elev is None:
            distance += step_m
            continue

        valid_points += 1
        delta_h = terrain_elev - station_alt
        elevation_angle = math.degrees(math.atan2(delta_h, distance))

        if elevation_angle > max_elevation_angle:
            max_elevation_angle = elevation_angle
            horizon_distance = distance / 1000.0

        distance += step_m

    if valid_points == 0:
        return -90.0, 0.0

    return max_elevation_angle, horizon_distance


def calculate_horizon_profile(src, geod, station_lat, station_lon,
                               station_alt=0, max_distance_km=100,
                               step_km=0.5, azimuth_increment=5,
                               verbose=False):
    """
    Compute the full azimuth horizon profile for one station point using
    an already-open rasterio dataset `src` and a reusable pyproj Geod
    object `geod`. Returns (results_dict, resolved_station_alt).
    """
    results = {}

    if station_alt == 0:
        dem_elev = safe_read_dem(src, station_lon, station_lat)
        if dem_elev is not None:
            station_alt = dem_elev

    for azimuth in range(0, 360, azimuth_increment):
        elev_angle, distance = calculate_horizon_in_direction(
            src, station_lat, station_lon, station_alt,
            azimuth, geod, max_distance_km, step_km
        )
        results[azimuth] = {
            'elevation_angle': elev_angle,
            'distance_km': distance
        }
        if verbose:
            print(f"  Azimuth {azimuth:3.0f}°: elev={elev_angle:6.2f}°, dist={distance:6.2f} km")

    return results, station_alt


def export_to_table(results, output_file=None):
    print("\n" + "=" * 80)
    print("HORIZON ELEVATION PROFILE (ITU Appendix 7 Format)")
    print("=" * 80)
    print(f"{'Azimuth (°)':<15} {'Elevation (°)':<20} {'Distance (km)':<20}")
    print("-" * 80)

    lines = []
    for az in sorted(results.keys()):
        data = results[az]
        line = f"{az:<15.1f} {data['elevation_angle']:<20.4f} {data['distance_km']:<20.4f}"
        print(line)
        lines.append(f"azm={az:.1f}\nelev_ang={data['elevation_angle']:.4f}\nhor_dist={data['distance_km']:.4f}\n")

    print("=" * 80)

    if output_file:
        with open(output_file, 'w') as f:
            f.writelines(lines)
        print(f"Results saved to {output_file}")