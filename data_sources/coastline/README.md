# Coastline data for `core/radio_climatic_zone.py`

This folder is a **placeholder** — it's currently empty. `radio_climatic_zone.py`'s
`load_coastline()` calls `geopandas.read_file(shapefile_path)`, which needs
a real Esri Shapefile (or GeoPackage/GeoJSON — see "Alternative formats"
below) dropped in here before radio-climatic-zone lookups will work.

## What to download

**Natural Earth 1:10m Coastline**, the specific dataset this module was
written against:

- Download page: https://www.naturalearthdata.com/downloads/10m-physical-vectors/10m-coastline/
- Direct file: `ne_10m_coastline.zip`
  (mirrors: https://github.com/nvkelso/natural-earth-vector/tree/master/10m_physical)

Unzip it here so this folder ends up containing **all** of the sidecar
files with matching basenames — a Shapefile is not one file, it's a set:

```
data_sources/coastline/
├── README.md                 (this file)
├── ne_10m_coastline.shp      <- geometry (required)
├── ne_10m_coastline.shx      <- shape index (required)
├── ne_10m_coastline.dbf      <- attribute table (required)
├── ne_10m_coastline.prj      <- projection/CRS definition (required)
├── ne_10m_coastline.cpg      <- character encoding (usually included)
└── ne_10m_coastline.README.html / .VERSION.txt  (docs from the NE zip, optional)
```

Do not rename `.shp` without renaming every sidecar file to match — 
`geopandas`/`fiona` locate them by matching basename.

## Pointing the code at it

`radio_climatic_zone.py`'s functions take `coastline_path` /
`coastline_gdf` explicitly rather than hardcoding a path, e.g.:

```python
from core.radio_climatic_zone import load_coastline, determine_radio_climatic_zone

coastline = load_coastline("data_sources/coastline/ne_10m_coastline.shp")
zone, zone_name, dist_km = determine_radio_climatic_zone(
    station_lon=49.4797, station_lat=40.4678, station_alt=210,
    coastline_gdf=coastline,
)
```

## Alternative formats

`geopandas.read_file()` also accepts `.gpkg` (GeoPackage) or `.geojson` —
if you'd rather use one of those instead of the Shapefile bundle above,
just point `coastline_path` at that single file instead; no code changes
needed.

## Known limitation (inherited from the current module, not this data)

`radio_climatic_zone.py`'s `is_over_water()` is a simplified heuristic
(distance-to-coastline < 0.1 km) rather than a true land/water polygon
test, and Zone C ("warm sea") detection is not yet implemented (it always
falls back to Zone B, "cold sea"). See the module's docstrings — this is
a data-source note, not something fixed by dropping in a better
coastline file. A proper land/water mask (e.g. Natural Earth's
`ne_10m_land` polygons) or a warm-sea polygon set would be a follow-up
data drop into this same folder, not a code prerequisite for this phase.
