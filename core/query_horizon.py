#!/usr/bin/env python3
"""
Look up the nearest precomputed horizon profile for a given lat/lon,
from a dataset built by build_horizon_grid.py.

Usage:
    python query_horizon.py --db horizon_grid.db --lat 40.409 --lon 49.867
"""
import argparse
import json
import sqlite3

import numpy as np
from pyproj import Geod
from scipy.spatial import cKDTree

try:
    from core.horizon_core import export_to_table
except ImportError:
    from horizon_core import export_to_table


def load_grid(db_path):
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("SELECT id, lat, lon FROM horizon_profiles")
    rows = cur.fetchall()
    conn.close()

    if not rows:
        raise RuntimeError(f"No grid points found in {db_path}. Did build_horizon_grid.py run successfully?")

    ids = np.array([r[0] for r in rows])
    coords = np.array([[r[1], r[2]] for r in rows])  # (lat, lon)
    return ids, coords


def fetch_record(db_path, record_id):
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("""
        SELECT lat, lon, station_alt, profile_json, max_distance_km, step_km, azimuth_increment
        FROM horizon_profiles WHERE id = ?
    """, (record_id,))
    row = cur.fetchone()
    conn.close()

    lat, lon, station_alt, profile_json, max_dist, step, incr = row
    raw_profile = json.loads(profile_json)
    # JSON object keys are always strings, but azimuths are stored as
    # ints elsewhere (and export_to_table formats them as numbers) -
    # convert back after loading.
    profile = {int(az): data for az, data in raw_profile.items()}
    return {
        'lat': lat, 'lon': lon, 'station_alt': station_alt,
        # 'altitude' is kept as an alias of 'station_alt' (the actual DB
        # column, populated from the DEM at build time by
        # build_horizon_grid.py) so callers that look for either key name
        # get the right value - app.py's Step 5 dialog used to read a
        # nonexistent 'altitude' key here and silently got 0.0 every time.
        'altitude': station_alt,
        'profile': profile,
        'max_distance_km': max_dist, 'step_km': step, 'azimuth_increment': incr,
    }


def query_location(db_path, query_lat, query_lon):
    """
    Find the nearest precomputed grid point to (query_lat, query_lon) and
    return its horizon profile, annotated with which grid point it came
    from and how far away that grid point actually is.
    """
    ids, coords = load_grid(db_path)

    # Lat/lon Euclidean nearest-neighbor is a fine approximation here:
    # Azerbaijan spans a small enough area, and the grid itself was built
    # with true geodesic spacing, that distortion from treating degrees
    # as flat coordinates for the *search* is negligible relative to the
    # 5km grid spacing. We report the exact geodesic offset afterward.
    tree = cKDTree(coords)
    _, idx = tree.query([query_lat, query_lon])
    nearest_id = int(ids[idx])

    record = fetch_record(db_path, nearest_id)

    geod = Geod(ellps='WGS84')
    _, _, dist_m = geod.inv(query_lon, query_lat, record['lon'], record['lat'])

    record['query_lat'] = query_lat
    record['query_lon'] = query_lon
    record['offset_km'] = dist_m / 1000.0
    record['grid_id'] = nearest_id
    return record


def main():
    parser = argparse.ArgumentParser(description="Look up nearest precomputed horizon profile")
    parser.add_argument("--db", required=True, help="Path to horizon grid SQLite database")
    parser.add_argument("--lat", type=float, required=True, help="Query latitude")
    parser.add_argument("--lon", type=float, required=True, help="Query longitude")
    parser.add_argument("--output", help="Optional output file for the profile table")
    args = parser.parse_args()

    record = query_location(args.db, args.lat, args.lon)

    print(f"Query location: {args.lat}°N, {args.lon}°E")
    print(f"-> Nearest precomputed grid point (id={record['grid_id']}): "
          f"{record['lat']:.5f}°N, {record['lon']:.5f}°E "
          f"({record['offset_km']:.2f} km from your query point)")
    print(f"Grid station altitude: {record['station_alt']:.2f} m")
    print(f"Precomputed with: max_dist={record['max_distance_km']}km, "
          f"step={record['step_km']}km, azimuth_increment={record['azimuth_increment']}deg\n")

    export_to_table(record['profile'], args.output)


if __name__ == "__main__":
    main()