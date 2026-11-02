# Data setup

This backend port ships with **placeholder** files so the project structure,
imports, and schema are all correct out of the box — but three pieces of
real-world reference data still need to be dropped in manually before the
corresponding `core/` modules are fully functional. Everything else
(`calculations.py`, `Pr_calculations.py`, `propagation_calculator.py`) has
no external data dependency and works as-is.

| # | Module(s) affected | Placeholder path | Status | What you need |
|---|---|---|---|---|
| 1 | `tables_data.py` | `data_sources/ITU_Coordination_Distance_Parameters.xlsx` | ✅ **Already populated** | Nothing — the real workbook (Tables 7a-7c, 8a-8d, 9a-9b) is already in place. |
| 2 | `horizon_core.py`, `build_horizon_grid.py`, `query_horizon.py` | `data_sources/azerbaijan_dem.tif` | ⚠️ Empty (0 bytes) | A real EPSG:4326 DEM GeoTIFF |
| 3 | `radio_climatic_zone.py` | `data_sources/coastline/` | ⚠️ Empty folder | Natural Earth 1:10m coastline shapefile bundle |
| 4 | `query_horizon.py` | `data_sources/horizon_grid.db` | ⚠️ Valid schema, 0 rows | Run `build_horizon_grid.py` against a real DEM (item 2) to populate it |

Run `streamlit run app.py` at any point — it checks all four of these and
tells you which are still placeholders, without crashing either way.

---

## 1. `ITU_Coordination_Distance_Parameters.xlsx` — already done

No action needed. This is the workbook `tables_data.py` loads via pandas.

## 2. `azerbaijan_dem.tif` — Digital Elevation Model

**Currently:** an empty (0-byte) file — not a valid GeoTIFF.

**Needed:** a single-band, EPSG:4326 (WGS84 lat/lon) GeoTIFF covering the
area you want horizon profiles for. Full detail, including download
sources (Copernicus GLO-30, SRTM, ASTER GDEM) and CRS/format requirements,
is in `data_sources/DEM_README.md`.

**Drop it in at:** `data_sources/azerbaijan_dem.tif` (or point `--dem` at
a different path/filename — nothing hardcodes this exact name outside the
`build_horizon_grid.py` docstring's example command).

## 3. Coastline shapefile — for radio-climatic-zone lookups

**Currently:** `data_sources/coastline/` is empty.

**Needed:** the Natural Earth 1:10m coastline shapefile bundle
(`ne_10m_coastline.shp` + its `.shx`/`.dbf`/`.prj` sidecar files — a
shapefile is several files, not one). Full download link and exact file
list is in `data_sources/coastline/README.md`.

**Drop it in at:** `data_sources/coastline/` (all sidecar files together).

## 4. `horizon_grid.db` — precomputed horizon-profile grid

**Currently:** a valid, empty SQLite database — the schema was
pre-created via `build_horizon_grid.py`'s `_init_db()` so the file is
immediately overwritable/appendable without a migration step, but it has
0 rows.

**Needed:** run the batch builder once you have a real DEM (item 2):

```bash
python -m core.build_horizon_grid \
    --dem data_sources/azerbaijan_dem.tif \
    --db data_sources/horizon_grid.db
```

This is a heavy batch job by design (tens of millions of DEM reads across
a full grid) — see the warning in `build_horizon_grid.py`'s module
docstring about tuning `--spacing-km`, `--max-dist`, `--step`, and
`--increment` to trade accuracy for runtime, and `--workers` to use more
CPU cores. Expect this to take a while even on a coarse grid; it is not
something to run inside a Streamlit request.

Once populated, `query_horizon.py` (or `core.query_horizon.query_location(...)`
as a function call) does fast nearest-grid-point lookups against it instead
of recomputing a horizon profile from scratch per request.

---

## Verifying everything is wired up

```bash
pip install -r requirements.txt
streamlit run app.py
```

The placeholder `app.py` imports every `core/` module and reports the
status of all four data files above directly in the page — no need to dig
through logs.
