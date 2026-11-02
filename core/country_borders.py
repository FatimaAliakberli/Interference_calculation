"""
core/country_borders.py — Natural Earth country-border lookup for the
"Border Analysis & Mitigation" step (Step 8) of the Link Budget Calculator.

Downloads the Natural Earth "Admin 0 - Countries" dataset at runtime (no
manual file upload) and, for a set of azimuths from an earth station,
finds the geodesic distance to the nearest FOREIGN country's border along
each azimuth ray.

This is a screening/planning aid only, not an authoritative border source.
Natural Earth is a public-domain cartographic dataset intended for small/
medium-scale mapping; it is NOT a legal definition of national boundaries
and does not resolve disputed borders. Real coordination requires official
ITU procedures and each administration's own recognized border geometry —
see the TIER B / screening-only caveat surfaced in app.py's Step 8.

Dependencies: geopandas, shapely, pyproj, requests. All are optional at
import time — if any are missing, load_country_borders() raises a plain
ImportError with a clear message, which the caller (app.py Step 8) catches
and falls back to manual border-distance entry rather than crashing.
"""
import os
import tempfile
import functools

try:
    import requests
except ImportError:
    requests = None

try:
    import geopandas as gpd
except ImportError:
    gpd = None

try:
    from shapely.geometry import Point, MultiPoint, LineString, MultiLineString, GeometryCollection
except ImportError:
    Point = MultiPoint = LineString = MultiLineString = GeometryCollection = None

try:
    from pyproj import Geod
except ImportError:
    Geod = None

try:
    import streamlit as st
    _cache_resource = st.cache_resource
except ImportError:
    # Fall back to a plain memoizing decorator so this module doesn't hard
    # -require Streamlit (e.g. if ever unit-tested standalone).
    def _cache_resource(func):
        return functools.lru_cache(maxsize=None)(func)


NATURAL_EARTH_URLS = {
    "110m": "https://naciscdn.org/naturalearth/110m/cultural/ne_110m_admin_0_countries.zip",
    "50m": "https://naciscdn.org/naturalearth/50m/cultural/ne_50m_admin_0_countries.zip",
}

# Candidate column names Natural Earth's Admin-0 Countries layer uses for a
# country's display name, in order of preference. Checked at runtime since
# the exact set present differs slightly between the 110m and 50m releases.
_NAME_COLUMNS = ("NAME", "NAME_LONG", "ADMIN", "SOVEREIGNT", "NAME_EN")

# Polygons smaller than this (in degrees^2, since the GeoDataFrame is kept
# in EPSG:4326) are dropped as border-analysis candidates. This is a coarse
# filter meant only to suppress tiny island/enclave slivers that would
# otherwise produce spurious "nearest border" hits — it is NOT a legal
# definition of what counts as a neighbouring country, and is deliberately
# small so it never drops a genuine small neighbour (e.g. a small but
# contiguous mainland state).
DEFAULT_MIN_POLYGON_AREA_DEG2 = 0.0005

_REQUIRED_LIBS_MSG = (
    "Border analysis requires geopandas, shapely, pyproj, and requests "
    "(`pip install geopandas shapely pyproj requests`)."
)


def _check_dependencies():
    missing = [name for name, mod in (
        ("requests", requests), ("geopandas", gpd),
        ("shapely", LineString), ("pyproj", Geod),
    ) if mod is None]
    if missing:
        raise ImportError(f"{_REQUIRED_LIBS_MSG} Missing: {', '.join(missing)}.")


@_cache_resource
def load_country_borders(resolution: str = "110m", min_area_deg2: float = DEFAULT_MIN_POLYGON_AREA_DEG2):
    """
    Download (once per session — cached via st.cache_resource) the Natural
    Earth "Admin 0 - Countries" dataset and return it as a GeoDataFrame in
    EPSG:4326 (longitude/latitude, degrees), with tiny sliver polygons
    dropped.

    Parameters
    ----------
    resolution : "110m" (smallest/fastest, default) or "50m" (more detail).
    min_area_deg2 : polygons smaller than this (deg^2) are excluded.

    Raises
    ------
    ImportError
        if geopandas/shapely/pyproj/requests aren't installed.
    RuntimeError
        if the download or shapefile parse fails (no internet, firewall,
        bad response, corrupted zip, etc).

    Callers (app.py Step 8) should catch both and fall back to manual
    border-distance entry rather than letting the app crash.
    """
    _check_dependencies()
    if resolution not in NATURAL_EARTH_URLS:
        raise ValueError(f"resolution must be one of {list(NATURAL_EARTH_URLS)}")

    url = NATURAL_EARTH_URLS[resolution]
    try:
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
    except Exception as e:
        raise RuntimeError(
            f"Could not download Natural Earth country borders from {url}: {e}"
        ) from e

    # Write to a temp file and hand GDAL's "zip://" virtual-filesystem
    # prefix the path — this is the well-supported way to get
    # geopandas/fiona/pyogrio to read a shapefile straight out of a zip
    # without manually extracting it to disk.
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
            tmp.write(resp.content)
            tmp_path = tmp.name
        gdf = gpd.read_file(f"zip://{tmp_path}")
    except Exception as e:
        raise RuntimeError(
            f"Downloaded Natural Earth data from {url} but could not parse it "
            f"as a shapefile: {e}"
        ) from e
    finally:
        if tmp_path is not None:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    if gdf.crs is None:
        gdf = gdf.set_crs(epsg=4326)
    elif gdf.crs.to_epsg() != 4326:
        gdf = gdf.to_crs(epsg=4326)

    if min_area_deg2 and min_area_deg2 > 0:
        gdf = gdf[gdf.geometry.area > min_area_deg2].reset_index(drop=True)

    return gdf


def _resolve_name_columns(gdf):
    cols = [c for c in _NAME_COLUMNS if c in gdf.columns]
    if not cols:
        raise RuntimeError(
            "Natural Earth GeoDataFrame has none of the expected name columns "
            f"({', '.join(_NAME_COLUMNS)}) — dataset format may have changed."
        )
    return cols


def _extract_points(geom):
    """Flatten a shapely geometry (the result of intersecting a ray with a
    country's boundary) into a flat list of (lon, lat) tuples, regardless of
    whether it came back as a Point, MultiPoint, LineString (ray briefly
    running along a border segment), or a GeometryCollection mixing those."""
    pts = []
    if geom is None or geom.is_empty:
        return pts
    if isinstance(geom, Point):
        pts.append((geom.x, geom.y))
    elif isinstance(geom, MultiPoint):
        pts.extend((p.x, p.y) for p in geom.geoms)
    elif isinstance(geom, LineString):
        pts.extend(list(geom.coords))
    elif isinstance(geom, MultiLineString):
        for line in geom.geoms:
            pts.extend(list(line.coords))
    elif isinstance(geom, GeometryCollection):
        for g in geom.geoms:
            pts.extend(_extract_points(g))
    else:
        # Unexpected geometry type (e.g. a Polygon, if a ray were coincident
        # with a boundary in a degenerate way) — fall back to its coordinates.
        try:
            pts.extend(list(geom.exterior.coords))
        except Exception:
            pass
    return pts


def find_border_distances(lat, lon, azimuths_deg, home_country_name,
                           max_distance_km: float = 2000.0, resolution: str = "110m"):
    """
    For each azimuth (degrees, 0=North, clockwise — same convention used
    elsewhere in this app), find the geodesic distance (km) from
    (lat, lon) to the first FOREIGN country's border crossed along that
    direction.

    Algorithm, per azimuth alpha:
      1. Project a point max_distance_km away along alpha using
         pyproj.Geod.fwd(lon, lat, alpha, max_distance_km * 1000).
      2. Build a LineString "ray" from (lon, lat) to that far point.
      3. For every country polygon that is NOT the home country (matched
         case-insensitively against whichever of NAME/NAME_LONG/ADMIN/
         SOVEREIGNT/NAME_EN columns are present), intersect the ray with
         that polygon's BOUNDARY (not its interior) to get the exact
         border-crossing point(s). A spatial index (GeoDataFrame.sindex)
         is used to skip the large majority of polygons the ray obviously
         can't reach, which is what keeps ~250 polygons x ~72 azimuths
         fast.
      4. Among all crossing points found (across all candidate countries),
         keep the one closest to the origin, via Geod.inv geodesic
         distance — this is the border distance for that azimuth.
      5. If no country polygon's boundary intersects the ray within
         max_distance_km, that azimuth's border distance is None (no
         neighbour in that direction within range).

    Parameters
    ----------
    lat, lon : float
        Earth station latitude/longitude (degrees).
    azimuths_deg : iterable of float
        Azimuths (degrees, 0-360, 0=North) to test.
    home_country_name : str
        Country to exclude from the search (case-insensitive, matched
        against NAME/NAME_LONG/ADMIN/SOVEREIGNT/NAME_EN).
    max_distance_km : float
        Ray length / search radius (km). Default 2000 km.
    resolution : "110m" or "50m", passed through to load_country_borders().

    Returns
    -------
    dict {azimuth_deg: border_distance_km_or_None}

    Raises
    ------
    ImportError / RuntimeError — see load_country_borders(). Callers should
    catch these and fall back to manual entry.
    """
    _check_dependencies()
    if max_distance_km <= 0:
        raise ValueError("max_distance_km must be > 0")

    gdf = load_country_borders(resolution)
    name_cols = _resolve_name_columns(gdf)

    home_lower = (home_country_name or "").strip().lower()
    if not home_lower:
        raise ValueError("home_country_name must not be empty")

    def _is_home(row):
        return any(str(row.get(c, "") or "").strip().lower() == home_lower for c in name_cols)

    is_home_mask = gdf.apply(_is_home, axis=1)
    foreign = gdf[~is_home_mask].reset_index(drop=True)
    if len(foreign) == len(gdf):
        # Nothing matched home_country_name — likely a typo. Not fatal (the
        # analysis can still run, just without excluding the home country),
        # but worth telling the caller so it can warn the user.
        pass

    sindex = foreign.sindex
    geod = Geod(ellps="WGS84")

    results = {}
    for az in azimuths_deg:
        far_lon, far_lat, _ = geod.fwd(lon, lat, az, max_distance_km * 1000.0)
        ray = LineString([(lon, lat), (far_lon, far_lat)])

        try:
            candidate_idx = list(sindex.query(ray, predicate="intersects"))
        except TypeError:
            # Older shapely/geopandas sindex signatures don't accept
            # predicate= for query(); fall back to a manual bbox filter.
            candidate_idx = list(sindex.query(ray))

        best_km = None
        for idx in candidate_idx:
            poly = foreign.geometry.iloc[idx]
            try:
                inter = ray.intersection(poly.boundary)
            except Exception:
                continue
            for px, py in _extract_points(inter):
                _, _, dist_m = geod.inv(lon, lat, px, py)
                dist_km = dist_m / 1000.0
                if best_km is None or dist_km < best_km:
                    best_km = dist_km
        results[az] = best_km

    return results