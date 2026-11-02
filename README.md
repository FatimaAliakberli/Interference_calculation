# Link Budget Calculator — ITU-R AP7 Coordination Distance Tool

A Streamlit wizard that determines the **minimum permissible basic transmission loss Lb(p)** and the
**coordination contour** of a GSO earth station according to **RR Appendix 7 (AP7)**, then checks the
resulting interference-to-noise ratio (I/N) at neighbouring countries' borders and offers mitigation
options when a border azimuth fails.

> **Verification status of this document.** Equation numbers, defaults, session-state keys and file paths
> below were extracted directly from the source code (docstrings and comments included). The AP7 text itself
> was *not* re-checked while writing this README. The source modules carry their own reliability tags
> (**TIER A / TIER B**, see [§4.4](#44-source-reliability-tier-a--tier-b)) — **verify against an official copy of
> RR Appendix 7 before using any result in a real coordination filing.**

---

## Table of contents

1. [Project overview](#1-project-overview)
2. [Architecture & file structure](#2-architecture--file-structure)
3. [Datasets & external dependencies](#3-datasets--external-dependencies)
4. [Mathematical formulas & ITU sources](#4-mathematical-formulas--itu-sources)
5. [Session-state reference](#5-session-state-reference)
6. [Known limitations & future work](#6-known-limitations--future-work)

---

## 1. Project overview

The tool walks an engineer through the AP7 link-budget chain and the coordination analysis:

| Stage | What it does |
|---|---|
| **ITU SNS import (Step 0)** | Reads an ITU SNS filing database (`.mdb`), lists its earth stations, and pre-fills coordinates, satellite longitude, antenna Gmax / diameter, frequency, Pt, altitude and operating azimuth sector — via an explicit **review-then-proceed** panel. |
| **Link budget (Steps 1–4)** | Gt (Annex 3, Eq. 84–97), Pt, Gr and Pr(p) (victim parameters from AP7 Tables 7/8/9 or calculated), then **Lb(p) = Pt + Gt + Gr − Pr(p)**. |
| **Coordination contour (Step 5)** | Per-azimuth **propagation mode (1)** required distance (Annex 1, three frequency bands) and **propagation mode (2)** (Annex 2, rain scatter); final contour is the per-azimuth **maximum** (AP7 §1.6). Gt is recomputed per azimuth from each azimuth's horizon elevation. |
| **Blind I/N check (Step 6)** | Worst-case I/N at a user-chosen test distance using conservative victim parameters. |
| **Border analysis & mitigation (Step 7)** | Finds the distance to the nearest *foreign* border along each contour azimuth (Natural Earth), computes I/N there, optionally rechecks FAIL azimuths with an ITU-R P.526 knife-edge diffraction loss from a DEM, and offers Pt / satellite-longitude / antenna-diameter mitigation sliders. |
| **Map (Step 9)** | Folium map of the contour and green/red PASS/FAIL interference sectors, optional DEM hillshade, HTML download. |

Scope: **GSO earth stations**, **single-entry** interference, screening / planning use.

Run it from the project root:

```bash
streamlit run app.py
```

---

## 2. Architecture & file structure

```
link_budget_app/
├── app.py                          # Streamlit wizard (UI + orchestration)
├── requirements.txt
├── core/
│   ├── itu_import.py               # .mdb reader + e_stn / geo / grp / e_ant extraction
│   ├── propagation_calculator.py   # AP7 Annex 1 (mode 1), Annex 2 (mode 2), I/N functions
│   ├── tables_data.py              # loader/lookup for ITU_Coordination_Distance_Parameters.xlsx
│   ├── country_borders.py          # Natural Earth download + ray/border intersection
│   ├── map_visualization.py        # folium layers (contour, sectors, DEM, legend)
│   ├── streamlit_map_component.py  # glue: session-state results -> folium map -> Streamlit
│   ├── horizon_core.py             # DEM horizon-profile maths (shared by build + lookup)
│   ├── build_horizon_grid.py       # batch builder for horizon_grid.db
│   ├── query_horizon.py            # nearest-grid-point lookup in horizon_grid.db
│   ├── calculations.py             # (*) Annex 3 antenna-gain geometry, Eq. 84-97
│   ├── Pr_calculations.py          # (*) Pr(p) and Te / Ms look-ups
│   ├── draft_state.py              # (*) persistent draft values for dialogs ("ds")
│   ├── radio_climatic_zone.py      # (*) radio-climatic zone + dc from the coastline shapefile
│   └── terrain_diffraction.py      # (*) ITU-R P.526 knife-edge loss from a DEM
└── data_sources/
    ├── ITU_Coordination_Distance_Parameters.xlsx
    ├── horizon_grid.db
    ├── azerbaijan_dem.tif
    └── costline/ne_10m_coastline.shp   (+ sidecar files)
```

> **(\*)** These modules were **not among the files supplied** when this documentation was written. They are
> described only from how `app.py` and the other modules call them; their internals (e.g. the exact
> expressions in `calculations.py`) are not documented here.

### 2.1 `app.py`

Single-file Streamlit app. Structure, top to bottom:

| Region | Content |
|---|---|
| **Imports & constants** | `core.*` modules; `DEM_TIFF_PATH = os.environ.get("LBC_DEM_PATH", "data_sources/azerbaijan_dem.tif")`; `ITU_RESTRICT_SECTOR_DEFAULT = False`; `ITU_DEFAULT_*` constants for the import review panel. |
| **`DEFAULTS` dict** | Every session-state key and its initial value (see [§5](#5-session-state-reference)). "Clear all" resets to this dict and calls `ds.clear_all()`. |
| **Helpers** | `_find_horizon_db`, `_interp_eps_h`, `_compute_azimuth_and_eps_h`, `_gt_at_azimuth`, `_fetch_es_altitude`, `_itu_altitude_for`, `_adjust_horizon_for_altitude`, `_default_p_percent`, `_run_mode1`, `_compute_mode2_and_combine`, `_active_contour`, `_recompute_contour`. |
| **Dialogs (`@st.dialog`)** | `gain_workflow_dialog` (Gt/Gr, Eq. 84-97), `pt_dialog`, `pr_sub_dialog` (Pr(p)), `victim_workflow_ui` (Step 3, inline), `propagation_params_dialog` (Step 5 inputs). |
| **Page sections** | ITU import (Step 0) → Steps 1–6 → Step 7 (border analysis + mitigation) → Step 8 (status banner only, see [§6.3](#63-implementation-notes--known-quirks)) → Step 9 (map). |

### 2.2 `core/itu_import.py`

Reads the `.mdb` and extracts what the wizard needs. Table **and** column names are matched
case-insensitively against alias lists, so differently-named exports still work.

* **Backends for reading `.mdb` (tried in order):** `access-parser` (pure Python) → `mdbtools` (`mdb-export` on `PATH`) → `pyodbc` + Microsoft Access driver.
* **Table aliases:** `e_stn`; `geo` (`gso`); `non_geo` (`ngeo`, `non_gso`, `ngso`, `nongeo`); `grp` (`freq_grp`, `frequency_grp`); `e_ant` (`eant`, `ant`, `e_antenna`, `antenna`). Only `e_stn` is mandatory.
* **Key functions**

| Function | Returns / purpose |
|---|---|
| `load_mdb(file_bytes, filename_hint)` | `{"e_stn","geo","non_geo","grp","e_ant"}` → DataFrame or `None` |
| `extract_earth_stations(e_stn_df)` | list of station dicts: `label, ntc_id, lat, lon, lat_error, lon_error, sat_name, sat_long_nom, azm_fr, az_to, ant_alt, row_index` |
| `classify_satellite(sat_name, geo_df, non_geo_df)` | `"geo"` / `"non_geo"` / `"unknown"` — **joined by satellite *name*** (the satellite's `ntc_id` is a different ID space from the earth station's) |
| `extract_satellite_orbit_data(sat_name, geo_df)` | `inclin_exc` (informational only) |
| `extract_antenna_data(ntc_id, e_ant_df, emi_rcp=None, beam_name=None)` | `gain_dbi` (**Gmax**), `ant_diam_m`, `beam_name`, `match_count`; strict on `emi_rcp` when the column exists |
| `extract_group_data` / `extract_frequency_ghz` | first matching `grp` row: centre frequency (GHz), `pwr_max_dbw`, `match_count` |
| `extract_all_frequencies(ntc_id, grp_df, e_ant_df)` | **every** `grp` row: `grp_id, freq_min_mhz, freq_max_mhz, freq_center_ghz, emi_rcp, beam_name, noise_t, pwr_max_dbw, ant_gain_dbi, ant_diam_m` |
| `classify_frequencies_by_direction(freqs)` | `{"transmit": [...], "receive": [...], "unknown": [...]}` |
| `normalize_azimuth_sector`, `azimuth_in_sector`, `filter_profile_to_sector` | operating-sector (`azm_fr`/`azm_to`) handling incl. wraparound |

> **Gmax ≠ Gt.** `e_ant.gain` is the **main-beam-axis gain Gmax** (ITU SNS item B.5.a). The link budget needs the
> **horizon gain Gt = G(φ)** (Annex 3, Eq. 97). The import therefore writes Gmax **only** into the
> Gt/Gr calculators' `gw_Gt_gmax` / `gw_Gr_gmax` drafts and **never** into `gt` / `gr`.

### 2.3 `core/propagation_calculator.py`

Pure-Python AP7 engine (no Streamlit). Frequency dispatch:

| Band | Model | Required-distance runner |
|---|---|---|
| 100 MHz ≤ f < 790 MHz | Annex 1 §1 + §2 | `run_mode1_100_790` |
| 790 MHz ≤ f < 60 GHz | Annex 1 §1 + §3 | `run_mode1_790_60000` |
| 60 GHz ≤ f ≤ 105 GHz | Annex 1 §1 + §4 | `run_mode1_60_105` |
| Mode (2), rain scatter | Annex 2 | `run_mode2`, `run_lookup_rain_zone` |

Also: site shielding (`site_shielding_Ah`, `site_shielding_Ad`), `calculate_IN_ratio`,
`calculate_blind_IN_ratio`, `calculate_IN_at_distance`, `free_space_path_loss_with_gases`,
`calculate_path_loss_with_diffraction` (FSPL + gases + P.526 knife edge when a DEM covers the path).
The combination `max(mode 1, mode 2)` is done by the caller (`app.py`), not in this module.

### 2.4 Other modules

| File | Role |
|---|---|
| `tables_data.py` | pandas/openpyxl loader for the workbook (sheets *Transmitting Earth Station* = Tables 7a–7c, *Receiving Earth Station* = 8a–8d, *Bidirectional Transmitting ES* = 9a–9b). Parses engineering notation (`4x10^3`), footnotes, frequency ranges; `get_victim_parameters`, `lookup_entries`, `get_gx`, `list_bands`, … Returns plain dicts. |
| `country_borders.py` | Downloads Natural Earth *Admin 0 – Countries* (cached with `st.cache_resource`), drops sliver polygons (`DEFAULT_MIN_POLYGON_AREA_DEG2 = 0.0005`), casts a geodesic ray per azimuth (`max_distance_km=2000`) and returns the nearest foreign-border crossing. Raises `ImportError`/`RuntimeError` → the app falls back to manual border distances. |
| `map_visualization.py` | folium: `create_base_map`, `add_country_borders`, `add_earth_station_marker`, `add_coordination_contour`, `add_interference_zones` (green = PASS, red = FAIL pie-slices to the border distance + dashed arc at the required distance), `add_interference_popup`, `create_interference_legend`, `add_dem_overlay` (hillshade). |
| `streamlit_map_component.py` | `render_coordination_map(st, ppd_params, mode1_profile_result, border_analysis_rows, dem_path)`: controls (zoom 3–14 default 7; borders layer on; DEM off, opacity 0.5), builds the map, renders HTML (600 px), `download_map_button`. Rows without `border_km`, `in_result["IN_ratio_dB"]` or `limit_db` are skipped. |
| `horizon_core.py` | `safe_read_dem`, `calculate_horizon_in_direction`, `calculate_horizon_profile` (default single-station settings: 100 km radius, 0.5 km step, 5° azimuth step), `export_to_table`. |
| `build_horizon_grid.py` | Multiprocessing builder → SQLite `horizon_profiles` + `grid_meta`. Stores the DEM-derived `station_alt` of each grid point. |
| `query_horizon.py` | `query_location(db_path, lat, lon)`: cKDTree nearest grid point; returns `profile`, `station_alt` (alias `altitude`), `offset_km`, `azimuth_increment`, … |

---

## 3. Datasets & external dependencies

### 3.1 Data files

Place everything under `data_sources/` in the project root and start Streamlit **from the project root**.

| File | Location searched | Used for | Required? |
|---|---|---|---|
| `ITU_Coordination_Distance_Parameters.xlsx` | `data_sources/` (resolved relative to `core/tables_data.py`) | Step 3 auto-lookup of Gx / Te / B / Pr(p) from AP7 Tables 7/8/9 | Only for the "auto-lookup" victim option |
| `horizon_grid.db` | `data_sources/horizon_grid.db`, then `./horizon_grid.db` (or a path override in the Gt/Gr dialog) | Step 5 horizon profile (εh, dh per azimuth) **and** fallback altitude; auto α/εh in the Gt/Gr dialog | **Yes** — Step 5 has no manual horizon entry |
| DEM GeoTIFF (EPSG:4326) | `LBC_DEM_PATH` env var, default `data_sources/azerbaijan_dem.tif` | (a) input to `build_horizon_grid.py`; (b) Step 9 hillshade overlay; (c) Step 7 P.526 diffraction recheck | Optional at run time |
| `ne_10m_coastline.shp` (+ `.shx/.dbf/.prj`) | `data_sources/costline/…`, `data_sources/coastline/…`, `ne_10m_coastline/…` (note the `costline` spelling is accepted) | Radio-climatic zone auto-detection and `dc` (via `core/radio_climatic_zone.py`) | Optional — without it Step 5 falls back to **Zone A1**, `dc = 0` |
| Natural Earth *Admin 0 – Countries* (110m) | **Downloaded at run time** from `naciscdn.org` (cached per session) | Step 7 border distances | Needs internet; otherwise manual border distances |
| World-countries GeoJSON | **Downloaded at run time** from `raw.githubusercontent.com/johan/world.geo.json` | Step 9 "country borders" display layer only | Optional (map still renders without it) |
| ITU SNS `.mdb` filing database | Uploaded in the UI | Step 0 import | Optional |

**Building the horizon grid** (defaults: Azerbaijan bbox lat 38.3–41.95, lon 44.7–50.6; 5 km grid; 40 km
radius; 1 km radial step; 10° azimuth step):

```bash
python core/build_horizon_grid.py --dem data_sources/azerbaijan_dem.tif --db data_sources/horizon_grid.db
# optional: --lat-min/--lat-max/--lon-min/--lon-max --spacing-km 5 --max-dist 40 --step 1 --increment 10 --workers N
```

The batch defaults are intentionally coarser than `horizon_core`'s single-station defaults (100 km / 0.5 km / 5°)
to keep the build tractable. Tune them for accuracy vs. runtime.

### 3.2 Python dependencies

`requirements.txt` as provided:

```
streamlit  pandas  openpyxl  geopandas  shapely  pyproj  requests  matplotlib
access-parser           # .mdb reading, pure Python (tried first)
# pyodbc                # optional third backend (Windows + Access driver)
```

> **The provided `requirements.txt` is incomplete.** Imports in the code additionally require:
>
> | Package | Needed by |
> |---|---|
> | `folium` | `map_visualization.py` (imported unconditionally → Step 9) |
> | `numpy` | `horizon_core.py`, `query_horizon.py`, `map_visualization.py` |
> | `scipy` | `query_horizon.py` (`cKDTree`) |
> | `rasterio` | `horizon_core.py` (imported at module top — and `query_horizon.py` imports `horizon_core`, so it is needed even for DB look-ups, not just for building the DB) |
> | `srtm.py` *(optional)* | last-resort altitude fallback in `_fetch_es_altitude` |
>
> Suggested additions: `folium`, `numpy`, `scipy`, `rasterio` (the app's own error text mentions a
> `requirements-geo.txt`, which was not provided). A recent Streamlit is needed (`st.dialog`; ≥ 1.37 recommended).
> Python 3.9+ (the code uses built-in generics such as `list[tuple]`); the exact tested version is not recorded.

### 3.3 System dependencies

| Tool | Purpose |
|---|---|
| `mdbtools` (`mdb-export`) | Fallback `.mdb` backend on Linux/macOS (`apt install mdbtools` / `brew install mdbtools`); no native Windows build |
| Microsoft Access Database Engine + `pyodbc` | Optional third `.mdb` backend on Windows |

---

## 4. Mathematical formulas & ITU sources

Equation numbers are those quoted in the code. "Annex n" refers to the annexes of **RR Appendix 7**.

### 4.1 Link budget (main body)

```text
Lb(p) = Pt + Gt + Gr − Pr(p)               AP7 main body Eq. 2   (Step 4)
I     = Pt + Gt + Gx − L_path              AP7 main body Eq. 1   (Steps 6, 7)
L(p)  = Pt − Pr(p)                         "Eq. 3, main-body sense" — required loss used by mode (2)
Pr(p) = f(Te, B, NL, Ms, W)                Rec. ITU-R S.1430, labelled "Eq. 3" in the UI (Step 3 dialog)
N     = 10·log10(k·Te·B),  k = 1.380649e-23 J/K     "Eq. 127" (Annex 7)
I/N   = I − N
I/N limit (derived) = Pr(p) − N            per ITU-R S.1327 / main-body Eq. 1–2 (generic fallback −10 dB)
```

> The code labels two different things "Eq. 3" (the Pr(p) dialog title cites *S.1430 Eq. 3*; `run_mode2` cites the
> main-body "Eq. 3" for `L(p) = Pt − Pr(p)`). The Pr(p) expression itself lives in `core/Pr_calculations.pr_p(te, b, nl, ms, w)`
> (not provided) — confirm the exact form and numbering there.

### 4.2 Antenna gain toward the horizon — Annex 3

| Quantity | Source | Notes |
|---|---|---|
| φ, direct geometry | **Annex 3 Eq. 84–89** | from ζ, sub-satellite latitude *i*, δ, K, α, εh |
| φ, Case 1 (no orbital inclination) | **Annex 3 Eq. 90** | |
| φ, Case 2 (slightly inclined orbit) | **Annex 3 Eq. 91–96** | grid search over the inclination (`is_max`, `step`) |
| **G(φ)** = Gt (or Gr) | **Annex 3 Eq. 97** | needs D/λ and **Gmax**; D/λ can be estimated from Gmax (requires D/λ ≥ 35) |

Per azimuth in Step 5, `_gt_at_azimuth()` re-runs the same functions with that azimuth's own εh, giving
`Gt(α)` and `Lb(p, α) = Pt + Gt(α) + Gr − Pr(p)`. If Gt was typed in manually (no geometry), a single fixed Lb(p) is used.

### 4.3 Propagation mode (1) — Annex 1

**Shared foundation (main body §4 / Annex 1)**

| Quantity | Equation(s) |
|---|---|
| Reduced latitude ζr | Eq. 9/10 |
| Time percentage βe | Eq. 7/8 |
| Surface refractivity N0 | Eq. 11 |
| Minimum coordination distance dmin | Eq. 12–18 |
| Maximum distance dmax1 | **Table 3** (≤ 60 GHz: A1 500, A2 375, B 900, C 1200 km); **Eq. 19** (> 60 GHz) |
| Correction factor X(f) / Z(f) | Eq. 20 / Eq. 21 |
| Site shielding Ah (with Ad) | **Annex 1 §1**: Eq. 23 (dh contribution), Eq. 24a–c, Eq. 25 clamp −10 ≤ Ah ≤ 30 + εh |

**100 MHz ≤ f < 790 MHz — Annex 1 §2, Eq. 26–31**

| Item | Eq. |
|---|---|
| Termination L(p) ≥ L1(p); di ≥ dmax1 | 26a / 26b |
| L1(p) = Lb(p) − Ah | 27a |
| Ci correction; distance step di = dmin + n·step | 28a / 28b |
| Land path loss Lbl(p) | 29 |
| Sea path loss Lbs(p), cold sea (Zone B) / warm sea (Zone C) | 30a / 30b |
| Land/sea blended predicted loss L(p) | 31 |

**790 MHz ≤ f < 60 GHz — Annex 1 §3, Eq. 32–54**

| Item | Eq. | Item | Eq. |
|---|---|---|---|
| Termination (L5 ≥ L3 **and** L6 ≥ L4; di ≥ dmax1) | 32a / 32b | Dry-air attenuation γo | 33a / 33b |
| Water-vapour γw (P.676 Annex 2 simplified curve) | 34, 35a–c | Ducting γd | 36 |
| Coastal coupling Ac | 37 | Minimum loss A1; L3(p) = Lb(p) − A1 | 38; 39a |
| Frequency-dependent scatter term Lf | 40 | A2; L4(p) = Lb(p) − A2 | 41; 42a |
| Step di | 43 | Mixed gaseous attenuation γg | 44 |
| τ, μ1, σ, μ2, μ4 | 45, 46, 47, 48, 49a/b | β (= βe·μ1·μ2·μ4) | 50 |
| Γ1 | 51 | C2i | 52 |
| **L5(p)** ducting | 53 | **L6(p)** tropospheric scatter | 54 |

**60 GHz ≤ f ≤ 105 GHz — Annex 1 §4, Eq. 55–61**

| Item | Eq. |
|---|---|
| Dry-air γo (> 63.26 GHz / ≤ 63.26 GHz) | 55a / 55b |
| Water vapour (3 g/m³) | 56 |
| Combined gaseous γgm | 57 |
| L7 (min. loss, uses Ah); L8(p) = Lb(p) − L7 | 58; 59a |
| Step di | 60 |
| Distance-dependent loss | 61 |

### 4.4 Source reliability: TIER A / TIER B

The module header classifies each function. **TIER B** = OCR of the source was damaged and the formula was reconstructed.
From the docstrings: `beta_e`/`dx` and therefore **dmin (Eq. 12–18)**, `correction_X` (**Eq. 20**), the current-aggregate-land-distance
helper supporting **Eq. 44**, and the specific-attenuation fits **Eq. 55/56** are TIER B. `dmax2` (Annex 2 §2) is also reconstructed from
partly lost text (`dmax2 = √(17000·hR)` km). Everything else in the Annex 1 / Annex 2 code is documented as read directly from legible source.
**Treat TIER B results as unverified.**

### 4.5 Propagation mode (2) — Annex 2, Eq. 62–83 (rain scatter)

| Quantity | Source |
|---|---|
| Termination Lr(p) ≥ L(p); ri ≥ dmax2 | **Eq. 62a / 62b** |
| R(p), 0.001% < p < 0.3% (by rain-zone group) | **Eq. 63–67** |
| R(p), p ≥ 0.3% | **Eq. 68**, with **Table 4** (R(0.3%), pc per zone group) |
| Specific rain attenuation γR = k·R^α | **Eq. 69–70**; k, α from **Table 5** (vertical polarisation, log-k / linear-α interpolation, clamped to 1–40.5 GHz) |
| Effective rain-cell diameter ds = 3.3·R^−0.08 | Eq. 71 |
| Scatter transfer function cv = (1 − e^(−2.17·γR·ds)) / (γR·ds) | Eq. 72 |
| Additional attenuation Γ = 6.31·10⁻³·k·R^(α+0.5)·10^−0.1 | Eq. 73 |
| Rain height hR | Eq. 74–75 (**only Eq. 75a–e is implemented**) |
| dmax2 | §2 (reconstructed) |
| γwr (water vapour) | Eq. 76 |
| Step ri = dmin + n·step | Eq. 77 |
| Lar, Ab, do, dv | Eq. 78a/b, 79a/b, 80a/b, 81a/b |
| **Lr(p)** (Gx folded in) | **Eq. 82** |
| Contour offset Δd = hR / (2·tan εs), limited to (dr − 50) km | Eq. 83 |
| γo reused from | Eq. 33 |

Mode 2 works from `L(p) = Pt − Pr(p)`, **not** Lb(p).

### 4.6 Combined contour

```text
required_distance(α) = max( mode1(α), mode2 )          AP7 main body §1.6
```
`mode2` (dr) is azimuth-independent in this implementation, so it governs wherever mode 1 is shorter.

### 4.7 Diffraction recheck, gases, noise

| Item | Source |
|---|---|
| Terrain diffraction (single knife edge) for FAIL azimuths | **ITU-R P.526**, computed from a DEM in `core/terrain_diffraction.py` (not provided); added to FSPL + gases as a *separate* "with diffraction" result — the original I/N is not overwritten |
| FSPL + gaseous absorption (border I/N path loss, 60–105 GHz) | `free_space_path_loss_with_gases` |
| Water-vapour curve (Eq. 34) | ITU-R P.676 Annex 2 (simplified) |
| Noise floor | Eq. 127 |

### 4.8 Altitude and horizon geometry (ITU import additions)

```text
εh_new = atan( tan(εh) − Δz / dh )        first-order shift when the filing altitude differs from the grid DB's DEM altitude
```
`Δz` = filing altitude − DB `station_alt` (m); `dh` = horizon distance (m). The limiting terrain point is kept, so a higher antenna
can only *under*-state εh slightly (never over-state shielding). Applied by default (checkbox) when the two differ by ≥ 1 m.

---

## 5. Session-state reference

| Key(s) | Set by | Meaning |
|---|---|---|
| `itu_mdb_file_key`, `itu_mdb_tables`, `itu_mdb_stations`, `itu_mdb_error` | upload | parsed `.mdb` |
| `itu_mdb_pending` | "Import this earth station" | staged values awaiting review |
| `itu_mdb_last_import_msg` | "Proceed with these values" | post-import summary |
| `itu_imported_frequencies` | Proceed | `{"transmit","receive","unknown"}` lists of frequency-group dicts |
| `itu_imported_azm_fr`, `itu_imported_az_to` | Proceed | operating azimuth sector (deg) or `None` |
| `itu_imported_altitude`, `itu_imported_altitude_pos` | Proceed | reviewed antenna altitude (m MSL) and the (lat, lon) it belongs to |
| `step1_es_lat`, `step1_es_lon`, `step1_sat_lon` | Proceed | committed coordinates / satellite longitude |
| `pt`, `pt_source` | Proceed / Step 2 | Pt (dBW) |
| `gt`, `gt_source`, `gt_geometry` | Step 1 | horizon gain and its Annex 3 geometry "recipe" (`None` if typed manually) |
| `gr`, `pr_p`, `gr_pr_source`, `final_te`, `final_b` | Step 3 | victim Gr/Gx, Pr(p), Te, B |
| `step3_selected_band`, `step3_selected_freq`, `step3_band_freq_map`, `step3_freq_candidates` | Step 3 | bands offered to Step 5 |
| `lb_p` | Step 4 | Lb(p) |
| `ppd_params` | Step 5 dialog | all propagation inputs incl. `horizon_profile`, `azimuth_sector`, `altitude`, `altitude_source`, `rain_zone`, `elevation_angle`, `gx` |
| `mode1_profile_result`, `mode1_profile_diag`, `mode1_profile_errors`, `mode2_result`, `mode2_error`, `final_contour_result` | Step 5 | contour results |
| `blind_in_result` | Step 6 | blind I/N result |
| `border_analysis_rows`, `border_manual_km`, `border_mitigation_az`, `s8_applied_*` | Step 7 | border/I-N rows, manual distances, applied mitigation |

Calculator defaults (coordinates, Gmax, D, frequency, …) are **drafts** persisted through `core/draft_state.py` (`ds`): e.g. `gw_Gt_gmax`,
`gw_Gr_gmax`, `gw_Gt_D`, `gw_Gr_D`, `es_lat`, `es_lon`, `sat_lon`, `gw_Gr_sat_lon`, `freq_ghz`, `s2_manual`.

---

## 6. Known limitations & future work

### 6.1 Propagation mode (2): rain zone is manual — **P.837-8 automation not implemented**

* Mode 2 (Annex 2, Eq. 62–83) **is implemented and combined with mode 1 per AP7 §1.6**, but the **rain climatic zone (A–Q) must be
  chosen manually** in the Step 5 dialog. There is **no default** (`"(select)"`); **"Use these parameters" is blocked until a zone is chosen.**
* The **automatic derivation of the rain zone / rainfall rate from ITU-R P.837-8 digital maps or formulas is NOT implemented.**
  R(p) comes from the AP7 Annex 2 route: **Table 4** (R(0.3%) and pc per zone group) with **Eq. 63–68** — not from P.837 maps.
* Related Annex 2 limits: rain height uses **Eq. 75 for all regions** (Eq. 74 for North America / Europe west of 60°E is not selectable);
  **Table 5 (k, α) is vertical polarisation only and spans 1–40.5 GHz** (values are clamped, with a warning, outside that range);
  Annex 2 requires p > 0.001%; `dmax2` is a reconstructed formula; mode 2 is azimuth-independent.
* **Future work:** P.837-8 map/formula lookup keyed on (lat, lon) to pre-select the zone; polarisation-dependent k, α; Eq. 74.

### 6.2 Scope limitations

* **GSO only.** Stations whose satellite is found in `non_geo` are rejected at import ("This tool only supports Geostationary (GSO) satellites"). If the satellite is in neither table the import proceeds with a warning.
* **Single-entry interference** — no aggregate interference, no multiple-victim / multi-network analysis.
* **One operating sector per station** (`e_stn.azm_fr`/`azm_to`). Restricting the contour to it is **opt-in** (off by default): the sector is where the *main beam* may point, while the coordination contour is normally assessed around the whole station (off-axis Gt is handled per azimuth).
* **Screening-grade borders.** Natural Earth 110m is a cartographic dataset, not a legal boundary source; disputed borders are not resolved.
* **Horizon data** come from a precomputed grid (default 5 km spacing, 40 km radius, 1 km step, 10° azimuths) covering Azerbaijan by default — coarser than the 100 km / 0.5 km / 5° single-station settings. Outside the DB's coverage Step 5 cannot proceed (no manual horizon entry).
* **Altitude:** the filing altitude (`e_stn.ant_alt`) takes priority over the DEM, but the stored profile was computed for the DEM altitude; the first-order εh correction (§4.8) is an approximation. Rebuilding the DB with the filing altitude is the exact fix.
* **TIER B equations** (§4.4) are reconstructions and must be verified.
* **Te / B availability:** some Table 7/8/9 rows have no Te or B; Steps 6–7 then require entering them manually (Pr(p) dialog).
* **Gt entered manually** (no Annex 3 geometry): only the Pt mitigation slider works, and a single fixed Lb(p) is used for all azimuths.
* **Mitigation D slider:** if the Gt geometry recorded no real diameter/wavelength, a 3 m baseline is assumed to back out λ.

### 6.3 Implementation notes & known quirks

* **Step 8 is a leftover stub.** The page contains a second heading "Step 8 — Border Analysis & Mitigation" whose caption ends in "…" followed by a placeholder comment; it only shows the *currently active* Pt / satellite longitude / antenna diameter. All border analysis and mitigation lives in **Step 7**. Recommended clean-up: rename Step 8 to "Active parameters" or merge it into Step 7.
* **Mitigation "Apply" button logic (Step 7).** `Apply mitigation and recompute contour` is nested in the `else` branch of `if IN_new <= limit_s8`, so it is shown only when the previewed settings still FAIL. It almost certainly should be shown when the preview passes (or always). Fix: dedent the `st.button` out of the `else`.
* **Step 6 help text** says p must be ≥ 1% (true for the 100–790 MHz model only); the 790 MHz–60 GHz model accepts 0.001–50%.
* `requirements.txt` is missing `folium`, `numpy`, `scipy`, `rasterio` (see §3.2).
* The commented-out older copies of the blind-I/N functions in `propagation_calculator.py` can be deleted.
* `extract_group_data()` (single-row) takes the *first* `grp` row, which may be a receive group; the multi-frequency import (`extract_all_frequencies`) and the review panel's group selector avoid this.
* Five modules referenced by `app.py` (`calculations.py`, `Pr_calculations.py`, `draft_state.py`, `radio_climatic_zone.py`, `terrain_diffraction.py`) were not available for this documentation.

### 6.4 Future work

1. P.837-8 rain-zone / rainfall-rate automation (above).
2. Aggregate / multi-entry interference; non-GSO support.
3. Authoritative border data (official boundary sets) instead of Natural Earth 110m; 50m option in the UI.
4. Exact altitude handling (rebuild horizon DB per site, or on-the-fly profile from the DEM).
5. Complete `requirements.txt` / `requirements-geo.txt`, automated tests for `propagation_calculator.py` against published AP7 worked examples, and a CI check of the TIER B equations.
6. Merge/remove the Step 8 stub; add multi-sector support for stations with several azimuth sectors.
