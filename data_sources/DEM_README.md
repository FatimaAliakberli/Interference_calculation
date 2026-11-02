# `azerbaijan_dem.tif` (placeholder — currently 0 bytes)

This file is a **placeholder only**. It is not a valid GeoTIFF and will
fail to open with `rasterio.open()` as-is. Overwrite it with a real
Digital Elevation Model before using anything in `core/horizon_core.py`,
`core/build_horizon_grid.py`, or `core/query_horizon.py`.

## What to put here

A single-band, EPSG:4326 (WGS84 lat/lon) GeoTIFF covering the area you
need horizon profiles for (Azerbaijan by default, per
`build_horizon_grid.py`'s default bounding box: lat 38.3–41.95°N,
lon 44.7–50.6°E — adjust with `--lat-min/--lat-max/--lon-min/--lon-max`
if you use a different DEM footprint).

Good sources:

- **Copernicus GLO-30** (30 m, near-global, open license) —
  https://dataspace.copernicus.eu/ or via the AWS Open Data registry
  (`s3://copernicus-dem-30m`).
- **SRTM 1 Arc-Second Global** (~30 m, 60°N–56°S) —
  https://earthexplorer.usgs.gov/ (product: "SRTM 1 Arc-Second Global").
- **ASTER GDEM v3** (30 m, near-global) — via NASA Earthdata.

## Requirements the file must satisfy

- CRS: EPSG:4326 (reproject with `gdalwarp -t_srs EPSG:4326 ...` if needed).
- Single elevation band, meters above sea level.
- Reasonable `nodata` value set in the GeoTIFF metadata (used by
  `horizon_core.safe_read_dem()` to detect off-raster / sea / missing
  cells).
- If you mosaic multiple SRTM/Copernicus tiles, merge them into one
  GeoTIFF first (e.g. `gdal_merge.py` or `gdalbuildvrt` + `gdal_translate`)
  — the code opens a single file path.

## File name / path

The filename itself isn't hardcoded anywhere except as the `--dem`
argument's example value in `build_horizon_grid.py`'s docstring — any
path works as long as you pass it explicitly:

```
python core/build_horizon_grid.py --dem data_sources/azerbaijan_dem.tif --db data_sources/horizon_grid.db
```
