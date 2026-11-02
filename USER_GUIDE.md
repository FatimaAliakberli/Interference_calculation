# Link Budget Calculator — User Guide

A step-by-step guide to running an **ITU RR Appendix 7 coordination analysis** for a GSO earth station:
from importing the ITU filing to the coordination contour, border I/N check, mitigation and map.

> **Screening tool.** Results are a planning aid. Several equations are flagged in the code as reconstructed
> ("TIER B") and the border data are a cartographic dataset, not legal boundaries. Verify against an official copy of
> RR Appendix 7 and authoritative border data before using results in a real filing.

---

## Contents

1. [Installation & setup](#1-installation--setup)
2. [The workflow at a glance](#2-the-workflow-at-a-glance)
3. [Step 0 — Import from an ITU filing database (.mdb)](#3-step-0--import-from-an-itu-filing-database-mdb)
4. [Link budget, Steps 1 to 4](#4-link-budget-steps-1-to-4)
5. [Step 5 — Coordination distance (propagation parameters)](#5-step-5--coordination-distance)
6. [Step 6 — Blind I/N check](#6-step-6--blind-in-check)
7. [Step 7 — Border analysis & mitigation](#7-step-7--border-analysis--mitigation)
8. [Step 9 — Map](#8-step-9--map-visualization)
9. [Data flow](#9-inputoutput-data-flow)
10. [Default values cheat-sheet](#10-default-values-cheat-sheet)
11. [Troubleshooting](#11-troubleshooting)

---

## 1. Installation & setup

You can access the stresmlit page directly from this link:

https://interferencecalculation-hshgqf9whaob7adiyz6u2p.streamlit.app/


Because of data load, we were not able to add 'azerbaijan_dem.tif' to github repo (currently). So, you can accesss drive link to download that file and follow steps below to add that file to relevant location to be able to run the streamlit application succesfully:

https://drive.google.com/file/d/1AmiP1aH41SAy4WtWPejTmyB9G5Kyy5AY/view?usp=sharing


Or you can follow steps below to download the full project.

### 1.1 Install

```bash
# from the project root (the folder containing app.py and core/)
python -m venv .venv
# Windows: .venv\Scripts\activate        Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
pip install folium numpy scipy rasterio     # needed by the code but missing from requirements.txt
```

* **Python 3.9+**, and a recent Streamlit (the app uses `st.dialog`; 1.37 or newer recommended).
* **Reading `.mdb` files** needs one backend: `access-parser` (installed by `requirements.txt`; works on Windows/Linux/macOS), or
  `mdbtools` (`sudo apt install mdbtools` / `brew install mdbtools`; Linux/macOS), or `pyodbc` + the Microsoft Access Database Engine (Windows).
  The import section shows which backend it found ("✅ Ready to read .mdb files via: …").
* Optional: `srtm.py` (last-resort altitude lookup).

### 1.2 Put the data files in `data_sources/`

| File | Path | Needed for |
|---|---|---|
| `horizon_grid.db` | `data_sources/horizon_grid.db` (or project root) | **Step 5 — required** (horizon profile) |
| `ITU_Coordination_Distance_Parameters.xlsx` | `data_sources/` | Step 3 auto-lookup (AP7 Tables 7/8/9) |
| `ne_10m_coastline.shp` (+ `.shx .dbf .prj`) | `data_sources/costline/` or `data_sources/coastline/` | automatic radio-climatic zone and `dc` |
| `azerbaijan_dem.tif` | `data_sources/` (or set the `LBC_DEM_PATH` environment variable) | map hillshade, P.526 diffraction recheck |




Natural Earth country borders are **downloaded automatically** at run time (internet needed for Step 7; otherwise use manual border distances).

Building `horizon_grid.db` (once, for your region):

```bash
python core/build_horizon_grid.py --dem data_sources/azerbaijan_dem.tif --db data_sources/horizon_grid.db
```

### 1.3 Start

```bash
streamlit run app.py
```

Run it **from the project root** so the relative `data_sources/` paths resolve.

---

## 2. The workflow at a glance

| Step | Purpose | Key output |
|---|---|---|
| **0 Import** (optional) | Pre-fill from an ITU `.mdb` filing, with a review panel | coordinates, Gmax, D, f, Pt, altitude, sector |
| **1 Gt** | Horizon gain of the interfering earth station (Annex 3, Eq. 84–97) | `Gt` |
| **2 Pt** | Maximum transmit power | `Pt` (dBW) |
| **3 Gr & Pr(p)** | Victim parameters (Tables 7/8/9, or calculated) | `Gr`, `Pr(p)`, `Te`, `B` |
| **4 Result** | **Lb(p) = Pt + Gt + Gr − Pr(p)** | `Lb(p)` |
| **5 Coordination distance** | Mode 1 + Mode 2 → contour = max per azimuth | per-azimuth required distance |
| **6 Blind I/N** | Worst-case I/N at a test distance | PASS/FAIL |
| **7 Border analysis & mitigation** | I/N at each foreign-border crossing; mitigation sliders | PASS/FAIL table, mitigation |
| **8 (status banner)** | Shows the currently active Pt / satellite longitude / diameter | — |
| **9 Map** | Contour + PASS/FAIL sectors | interactive map, HTML download |

> In the current app, **border analysis and mitigation are both in Step 7.** The "Step 8" heading only shows a status banner
> with the active parameters (it is a leftover of an earlier layout).

You do not have to use the import: every value can also be typed in manually.

---

## 3. Step 0 — Import from an ITU filing database (.mdb)

Open **"Import from ITU filing database (.mdb)"** → expander *"Upload an ITU SNS database (.mdb) to auto-fill earth station
coordinates and frequency"*.

### 3.1 Procedure

1. **Upload** the file with *"ITU SNS database file (.mdb)"*. It is parsed once per file (name + size); uploading a new file clears the previous import.
2. **Select the earth station** from the dropdown. The app shows the associated satellite (`e_stn.sat_name`) and warns if coordinates could not be converted.
3. Click **"Import this earth station"**. *This does not commit anything yet* — it opens the **Review panel**.
4. Check/edit the values (see §3.3) and click **"Proceed with these values"** (or **"Cancel import"**).

If the satellite is found in the `non_geo` table the import is refused (*"This tool only supports Geostationary (GSO) satellites."*).
If it is in neither `geo` nor `non_geo`, a warning is shown and you may continue after confirming GSO status yourself.

### 3.2 What is extracted from which table

| Table | Columns used | Becomes |
|---|---|---|
| **`e_stn`** (required) | `stn_name` | station label |
| | `ntc_id` | key into `grp` and `e_ant` (the earth station's own notice) |
| | `lat_deg`, `lat_min`, `lat_sec`, `lat_ns` | latitude (decimal degrees, +N/−S) |
| | `long_deg`, `long_min`, `long_sec`, `long_ew` | longitude (decimal degrees, +E/−W) |
| | `sat_name`, `long_nom` | associated satellite name and **satellite orbital longitude** |
| | `ant_alt` (aliases `ant_alt_m`, `altitude`, `height_msl`) | **antenna altitude**, m above mean sea level |
| | `azm_fr`, `azm_to` (aliases `az_fr`/`az_to`, …) | **operating azimuth sector** (deg clockwise from true north) |
| **`geo`** / **`non_geo`** | `sat_name`, `long_nom` | GSO / non-GSO classification — **matched by satellite name**, not by ID |
| | `inclin_exc` | informational note only (a Plans/AP30B field; *not* auto-filled) |
| **`grp`** | `freq_min`, `freq_max` (MHz) | frequency groups: centre = (min+max)/2 → GHz |
| | `pwr_max` | maximum aggregate power → **Pt** default (dBW) |
| | `emi_rcp`, `beam_name`, `grp_id` | transmit (`E`) / receive (`R`) classification, beam, group id |
| | `noise_t` | the *imported station's own* receive noise temperature — shown for reference, **not** used as the victim's Te |
| **`e_ant`** | `gain` | **main-beam gain Gmax** (dBi) — *not* Gt |
| | `ant_diam` | antenna diameter D (m) |
| | `emi_rcp`, `beam_name` | selects the transmit (`E`) / receive (`R`) antenna; matched to the `grp` row by (`emi_rcp`, `beam_name`) |

Only `e_stn` is mandatory. A missing optional table simply means those fields come up as defaults/empty in the review panel.

> ### ⚠️ Gmax is not Gt
> `e_ant.gain` is the antenna's **main-beam axis gain (Gmax)**. The link budget needs the **horizon gain Gt = G(φ)**,
> which is *lower* and depends on the geometry (Annex 3, Eq. 84–97). The import therefore pre-fills Gmax **only** into the
> Gt/Gr calculators' "Main beam axis gain Gmax" fields. **Gt and Gr are never set by the import** — you must still run the
> calculator (Step 1 for Gt, Step 3 for Gr).

### 3.3 The Review panel and its default values

Every field shows where its value came from:

| Marker | Meaning |
|---|---|
| **✅ from MDB** | value was found in the file |
| **⚠️ (default)** | not in the file — a default was filled in; edit it if you know better |
| **❗ missing, enter a value** | required value not in the file; **no default** — the field is empty |

| Field | If missing in the MDB | Notes / validation on **Proceed** |
|---|---|---|
| Latitude (deg, +N/−S) | **empty — required** | −90° … +90° |
| Longitude (deg, +E/−W) | **empty — required** | −180° … +180° |
| Satellite orbital longitude (deg) | **empty — required** | −180° … +180° |
| Antenna altitude (m above sea level) | **empty — optional** | −500 … 9000 m. Leave empty to use the DEM/grid-DB altitude in Step 5 |
| Gmax, transmit (dBi) | **50.0 dBi** (typical GSO earth station) | from `e_ant` row with `emi_rcp = E`; if absent, the receive value is used before the default |
| Gmax, receive (dBi) | the transmit Gmax, else **50.0 dBi** | from `e_ant` row with `emi_rcp = R` |
| Antenna diameter D (m) | **3.7 m** (common for 11–12 GHz) | must be > 0 |
| Frequency (GHz) | **11.0 GHz** (common Ku-band downlink) | must be > 0 |
| Max transmit power Pt (dBW) | **35.0 dBW** (≈ 3 kW, typical HPA) | cannot be empty |

> **There is no default for the rain climatic zone** (see Step 5): it is always chosen manually. The only zone default in the
> app is the **radio-climatic zone A1**, used when automatic detection is unavailable (§5.3).

**Frequency groups.** If the file has several `grp` rows, a selector — *"Frequency group for this analysis"* — lists them
(**transmit groups first**, then receive, then unknown direction); an expander shows the full table (direction, beam, min/max MHz,
centre GHz, `pwr_max`, `noise_t`, e_ant Gmax and D). The chosen group fills **Frequency** and **Pt**:

* Pt = that group's `pwr_max` — unless it is a **receive** group, in which case Pt is taken from the first transmit group that has a `pwr_max`.
* Changing the group re-seeds those two fields (your edits to them are lost).
* **All** groups are kept for Step 3 and Step 5.

**Operating azimuth sector.** If `azm_fr`/`azm_to` exist, the panel shows the sector (e.g. "170.0° → 250.0°, 80° wide, crossing north"
for a wraparound). Equal limits, 0→360, missing or out-of-range values mean "no sector — full 360°".

### 3.4 After "Proceed with these values"

* Values are validated; errors are listed and nothing is saved until fixed.
* **Stale results are cleared** (Gt, Lb(p), contour, border results …) because they belonged to the previous station.
* Saved: coordinates and satellite longitude, Gmax (Tx and Rx drafts), D, frequency, Pt, altitude (if given), the frequency list, the sector.
* A green message summarises what was imported, which values are defaults, and **reminds you that Gt and Gr still have to be calculated from Gmax via the Eq. 84–97 calculator in Step 1 (Gt) and Step 3 (Gr).**

---

## 4. Link budget, Steps 1 to 4

For Steps 1–3, a radio selector offers **"I already have this value"** (type it) or **"Help me calculate it"** (opens the calculator).

### Step 1 — Gt (AP7 Annex 3, Eq. 84–97)

*Gt = gain of the interfering earth station antenna **toward the victim's horizon**.* Open **"Open Gt calculator…"**:

1. **§1–2 Geometry:** earth-station latitude ζ, longitude, satellite longitude (δ = satellite − station is shown). Pre-filled by the import. *K* (orbit radius / Earth radius) has a built-in default.
2. **Orbital inclination method:** *Direct geometry (Eq. 84–89)* (enter sub-satellite latitude *i*), *Case 1 – no orbital inclination (Eq. 90)*, or *Case 2 – slightly inclined orbit (Eq. 91–96)* (max inclination *iₛ* and a grid-search step, default 1°).
3. **α and εh:** azimuth to the satellite and horizon elevation. Click **"🔄 Auto-calculate α and εh from coordinates"** (needs `pyproj` and `horizon_grid.db`) or type them.
4. **Calculate φ**, then **§3:** enter Gmax (pre-filled from the import), and D/λ — directly, or λ and D, or **"Estimate D/λ from Gmax"** (requires D/λ ≥ 35) — then **Calculate G(φ)** (Eq. 97).
5. Accept the value as Gt. The geometry is stored, so **Step 5 can recompute Gt for every azimuth** and the mitigation sliders can change satellite longitude and diameter.

If you type Gt manually there is no geometry: a single fixed Gt/Lb(p) is used everywhere and only the Pt slider works in mitigation.

### Step 2 — Pt

**"Open Pt calculator…"** → *Option A – Manual (Watts → dBW)*: `Pt = 10·log10(W)`; or *Option B – From EIRP*: `Pt = EIRP − G`, where G is the **main-beam** gain (pre-filled with Gmax, because EIRP is a main-beam quantity). The import already pre-sets Pt from `grp.pwr_max` (or the 35 dBW default after review).

### Step 3 — Gr and Pr(p) (victim)

**"Open victim parameters workflow…"** offers:

* **Use frequencies from imported ITU MDB file** *(appears only after an import with frequency data)* — pick the direction (Transmit / Receive / Unknown-All; *Transmit* is normally right because the victim shares the band the imported station transmits in) and a frequency group, optionally filter by service/orbit/modulation, **Find matching rows**, then choose a row and **Use this row's values**.
* **Auto-lookup from ITU coordination-distance tables (Tables 7/8/9)** — choose Table 7 (transmitting ES), 8 (receiving ES) or 9 (bidirectional), pick frequency band(s) (or enter a frequency), choose a row, **Use this row's values**. This sets **Gx/Gr, Pr(p), Te and B** (Te/B may be blank for some rows — then enter them via the Pr(p) dialog) and remembers the band(s) for Step 5.
* **Calculate from station specs** — **Gr** through the same Eq. 84–97 calculator (Gmax pre-filled for Gr) and **Pr(p)** through *Pr(p) — Threshold Interference Level* (Rec. ITU-R S.1430, "Eq. 3"). Inputs and defaults:

| Pr(p) input | Default | Guidance |
|---|---|---|
| Te (K) | 150 | 75 K (< 10 GHz), 150 K (10–17 GHz), 300 K (> 17 GHz); **"Look up Te & Ms from frequency"** fills them |
| B (Hz) | 12 500 | reference bandwidth |
| NL (dB) | `Pr_calculations.NL_DEFAULT` (1 dB for FSS links absent other data, per the dialog help) | link noise contribution |
| Ms (dB) | 4 | 2 dB (< 10 GHz), 4 dB (10–17 GHz), 6 dB (> 17 GHz) |
| W (dB) | 0 | usually ≤ 0 dB for digital wanted signals |

The Te and B you use are saved (`final_te`, `final_b`) for the I/N steps.

> If you use the import's frequency list, note that the imported station's `grp.noise_t` is *its own* receiver noise and is **not** used as the victim's Te.

### Step 4 — Result

Click **Calculate Lb(p)**: `Lb(p) = Pt + Gt + Gr − Pr(p)` (AP7 main body Eq. 2). All four must be set. **Clear all** resets the wizard and all saved drafts.

---

## 5. Step 5 — Coordination distance

Requires Lb(p), Pt and Pr(p). Click **"Enter propagation parameters…"** (dialog), then **"Use these parameters"**, then **"Calculate coordination distance"**.

### 5.1 Earth-station location and altitude

* Latitude/longitude are pre-filled (from the import / Step 1) and editable.
* **Altitude** — priority order:
  1. **ITU filing altitude** (`e_stn.ant_alt`, reviewed in Step 0) — displayed as *"Altitude (from ITU MDB filing)"*, used only if the entered coordinates are within ≈ 1 km of the imported station;
  2. otherwise the **horizon grid DB** altitude (nearest grid point), then the optional `srtm` package, then 0.0 m with a warning.
* If the filing altitude differs from the DB's DEM altitude by ≥ 1 m, a checkbox **"Adjust horizon elevations for the filing altitude"** (on by default) shifts the horizon elevations (a higher antenna sees a lower horizon). This is a first-order correction; untick it to keep the profile as computed.

### 5.2 Fetching the horizon profile

The profile is fetched **automatically** from `horizon_grid.db` whenever lat/lon change (nearest grid point; its distance is shown). **"🔄 Refetch horizon profile + altitude"** forces a refresh; **"Preview profile"** lists azimuth / εh / horizon distance.
There is **no manual single-azimuth entry**: a valid profile is mandatory. If the DB is missing, the dialog lists the paths it searched.

### 5.3 Frequency, time percentage and climatic parameters

| Item | Behaviour / default |
|---|---|
| **Frequency (GHz)** | Saved draft (import / last entry); else the Step 3 band midpoint; else **1.0 GHz**. Buttons **"Use this frequency"** (Step 3 bands) and **"Use this imported frequency"** (all imported groups) set frequency and p. |
| **Time percentage p (%)** | **1 %** below 10 GHz, **0.01 %** from 10 GHz up (AP7 starting values); editable |
| **Transmitting earth station** checkbox | on |
| **Radio-climatic zone** | Auto-detected from the coastline shapefile when Step 1 used the calculator (zone shown with the message *"Auto-detected Zone: …"*). If the shapefile is missing or detection fails → **Zone A1** and `dc = 0`. With a manually typed Gt, choose the zone (A1/A2/B/C) yourself and click **"Auto-fill N0 / β_e / dmin / dmax1 / dc"**. |
| N0, β_e, dmin, dmax1 | auto-filled (Eq. 11, 7/8, 12–18, Table 3 / Eq. 19), editable; initial value 0.0 until filled |
| dtm, dlm, dc (km) | **0.0** — longest continuous land / inland (zone A2) distance, distance to coast |
| Land fraction of path | **1.0** |
| Iteration step (km) | **1.0** |

### 5.4 Operating azimuth sector (only if the import supplied one)

A checkbox **"Restrict the contour to this sector (Mode 1, borders, plot)"** is offered. It is **off by default**, deliberately: `azm_fr`–`azm_to` are the azimuths the *main beam may be pointed at*, whereas the coordination contour is normally assessed all around the station (off-axis Gt per azimuth). Restricting it leaves other azimuths unassessed. If switched on, the profile is filtered (wraparound handled, ordered clockwise from `azm_fr`), and warnings appear for missing azimuths, sector limits between grid azimuths, or a sector narrower than the grid spacing. The plot then shows a wedge instead of a ring and the title names the sector.

### 5.5 Rain climatic zone (Mode 2) — **you must choose it**

Section *"Rain climatic zone & geometry (Mode 2)"*:

| Field | Default | Notes |
|---|---|---|
| **Rain climatic zone (A–Q)** | **none — "(select)"** | ITU-R P.837 zone of the site (Table 4). **"Use these parameters" is blocked until you pick one.** The tool does **not** derive the zone from P.837-8 maps — look it up yourself. |
| Main-beam elevation εs (deg) | **30.0** (0.1–90) | used for the Eq. 83 contour offset |
| Terrestrial antenna gain Gx (dBi) | Gr from Step 3, else 0.0 | enters Eq. 82 |

**"Look up hR / dmax2 / R(0.3%)"** previews the derived values for the chosen zone (rain height uses Eq. 75 for all regions).

### 5.6 Results

**"Calculate coordination distance"** runs, for every azimuth in the profile, **Gt(α)** → **Lb(p, α)** → Mode (1) required distance (Annex 1, band chosen by frequency), then Mode (2) (Annex 2, one value for all azimuths) and the **final contour = max(Mode 1, Mode 2)** (AP7 §1.6). Messages tell you how many azimuths Mode 2 governs; errors on individual azimuths are listed in an expander; an expander gives the full breakdown. Warnings appear if Mode 2 failed or the frequency is outside 1–40.5 GHz (rain coefficients clamped). A polar plot shows Mode 1, Mode 2 and the final contour.

---

## 6. Step 6 — Blind I/N check

Worst-case I/N at a **test distance** (e.g. distance to a neighbour's border) using the conservative victim parameters — before the victim's exact location is known. Needs Pt, Gt, Gr/Gx and the Step 5 parameters.

| Input | Default |
|---|---|
| Victim antenna gain Gx (dBi) | Gr from Step 3 |
| Victim noise temperature Te (K) | Step 3 value, else **150** |
| Victim reference bandwidth B (Hz) | Step 3 value, else **12 500** |
| Test distance (km) | **100** |
| I/N limit (dB) | **−10** (generic Fixed-Service value, ITU-R S.1327) |
| Frequency, zone, p, dtm, dlm, dc, latitude, transmitter flag | from Step 5 (editable for this check only) |

**Calculate Blind I/N** → path loss, noise floor `N = 10·log10(k·Te·B)`, interference `I = Pt + Gt + Gx − L_path`, `I/N = I − N`; **PASS** if I/N ≤ limit.
(The 100–790 MHz model requires p between 1 % and 50 %; the 790 MHz–60 GHz model accepts 0.001–50 %.)

---

## 7. Step 7 — Border analysis & mitigation

Needs the Step 5 contour, Pt, Gx/Gr, **Te and B**, and **Pr(p)**.

1. **I/N limit.** Default *"Derived from Pr(p) (recommended)"*: `limit = Pr(p) − N` with `N = 10·log10(k·Te·B)`. Alternatively *"Manual override"*.
2. **Home country name** (default **Azerbaijan**) — excluded from the border search (matched against Natural Earth names).
3. **Terrain diffraction (ITU-R P.526) recheck** (expander): *"Check terrain diffraction on FAIL azimuths"* — **on** by default (needs `pyproj`); DEM path (defaults to the map's DEM); earth-station antenna height above ground **10 m**; border-point reference height **0 m** (conservative). If the DEM is missing or doesn't cover the path, the recheck is silently skipped for that azimuth.
4. Click **"Analyze borders"**. For each contour azimuth the tool finds the distance to the **nearest foreign border** (search radius 2000 km, Natural Earth 110m), marks **Overlap = Yes** if the required distance exceeds it, and for overlapping azimuths computes the I/N **at the border distance** using that azimuth's Gt, horizon elevation and horizon distance.
   If the download/geo libraries fail, a warning appears and you can type border distances manually in *"Manual border distance entry (km)"*.

The result table shows, per azimuth: required distance, border distance, overlap, I/N at border, limit, **Status**, diffraction loss, I/N with diffraction and **Effective status**:

| Status | Meaning |
|---|---|
| — (no overlap) / — (no neighbor found) | contour does not reach a foreign border |
| ✅ PASS | I/N at the border ≤ limit |
| ❌ FAIL | I/N > limit |
| ✅ PASS (diffraction) | FAILs on FSPL + gases but passes once the P.526 diffraction loss is added |
| ⚠️ error | I/N could not be computed |

### 7.1 Mitigation panel

If any azimuth is still FAIL (effective status), the **worst azimuth** is targeted and a **Mitigation panel** appears with live sliders:

| Slider | Range | Needs |
|---|---|---|
| **Transmit power Pt (dBW)** | current Pt − 20 … + 5 (step 0.1) | always available |
| **Satellite orbital longitude δ (deg)** | −180 … 180 (step 0.1) | Gt from the Eq. 84–97 calculator (Step 1) |
| **Antenna diameter D (m)** | 1 … 15 (step 0.1) | Gt from the Eq. 84–97 calculator; if no real λ/D was recorded, a **3 m** baseline is assumed to derive λ |

The panel recomputes **Gt(φ)** at the worst azimuth and shows the new **I/N** and the **margin vs limit**, with a PASS/FAIL verdict. (Path loss and noise do not depend on Pt, Gt or D, so they are reused.)

When you are satisfied, **"Apply mitigation and recompute contour"** stores the slider values, updates the Gt geometry, recomputes the whole contour (Step 5 logic) and clears the old border/blind-I/N results — then run **Analyze borders** again.

> **Note — current behaviour:** in the present code the *Apply* button is only drawn while the previewed settings **still fail** at the
> worst azimuth; when the sliders already give a PASS, the panel shows the success message but **no Apply button**. This looks like an
> inverted condition in `app.py` (see the README, §6.3). Until it is fixed, a passing setting cannot be applied from this panel — change Pt
> (Step 2) or the Gt inputs (Step 1) directly, recalculate Lb(p), and rerun Step 5 and **Analyze borders**.

The **"Currently Active Parameters"** banner (under the "Step 8" heading) shows the Pt, satellite longitude and antenna diameter now in effect.

---

## 8. Step 9 — Map visualization

Requires the Step 5 contour; PASS/FAIL sectors appear after **Analyze borders**.

**Controls:** *Zoom level* slider (3–14, default **7**); *Show country borders layer* (on); *Show DEM hillshade overlay* (off; disabled if the DEM isn't found) with an *opacity* slider (0–1, default **0.5**).

**Reading the map**

| Element | Meaning |
|---|---|
| **Earth-station marker** | station location (centre of the map) |
| **Red dashed polygon** | the **coordination contour** — required distance per azimuth (final = max of Mode 1 and Mode 2; a wedge if the sector restriction is on) |
| **Green pie-slice** | azimuth where the I/N at the border is **within the limit (PASS)**; the slice extends to the **border distance** |
| **Red pie-slice** | azimuth where I/N at the border **exceeds the limit (FAIL)** |
| **Dashed arc inside a slice** | the **required coordination distance** at that azimuth — compare it with the slice length (the border) |
| **Popup (click a slice)** | azimuth, border and required distance, I/N, limit, status; if a diffraction recheck ran: diffraction loss, adjusted I/N and its status (the slice's *colour* stays the original, pre-diffraction status) |
| **Legend (bottom-left)** | green = PASS (within I/N limit), red = FAIL (exceeds I/N limit), dashed = required coordination distance |
| **Country borders layer** | display only (public world-countries GeoJSON); the I/N analysis uses the Natural Earth data from Step 7 |
| **DEM hillshade** | terrain context from your GeoTIFF |

Azimuths with no border crossing, no computed I/N or no limit are skipped (a note says how many). Use **⬇️ Download interactive map (HTML)** to save a standalone file.

> **Known issue — sector wedge on the map.** When the sector restriction is on, the map receives the contour in sector order; depending on
> how the polygon is closed in `map_visualization.py`, it may be drawn as a closed ring joining the sector ends rather than as a wedge through the station.

---

## 9. Input/output data flow

```text
 .mdb file ──► itu_import.load_mdb ──► tables {e_stn, geo, non_geo, grp, e_ant}
                                      │
                    extract_earth_stations / classify_satellite /
                    extract_antenna_data / extract_all_frequencies
                                      ▼
                          itu_mdb_pending  ──► REVIEW PANEL (edit, defaults marked)
                                      │  "Proceed with these values"
                                      ▼
   Session state:  step1_es_lat/lon, step1_sat_lon, pt, itu_imported_{frequencies, altitude, azm_fr, az_to}
   Calculator drafts (ds): es_lat, es_lon, sat_lon, gw_Gt_gmax, gw_Gr_gmax, gw_Gt_D, gw_Gr_D, freq_ghz, s2_manual
                                      │            (Gmax only — never gt / gr)
        Step 1  Gt (Eq.84-97) ─ gt, gt_geometry ──┐
        Step 2  Pt ─────────── pt ───────────────┤
        Step 3  Gr, Pr(p), Te, B ─ gr, pr_p, final_te, final_b ┤
        Step 4  Lb(p) = Pt+Gt+Gr−Pr(p) ─ lb_p ───┘
                                      ▼
        Step 5  dialog ─► ppd_params {lat, lon, altitude, f, p, zone, N0, dmin, dmax1, dtm, dlm, dc,
                                      horizon_profile (grid DB ± altitude adj. ± sector), rain_zone, εs, Gx}
                horizon_grid.db ──► per-azimuth (εh, dh)
                Gt(α) per azimuth ─► Lb(p,α) ─► Mode 1 (Annex 1) ┐
                L(p)=Pt−Pr(p) ─────────────────► Mode 2 (Annex 2) ┴─► max ─► final_contour_result
                                      ▼
        Step 6  blind_in_result            Step 7  borders (Natural Earth) + I/N (+ P.526 with DEM)
                                                   ─► border_analysis_rows ─► mitigation ─► (recompute Step 5)
                                      ▼
        Step 9  streamlit_map_component ─► map_visualization (folium) ─► map + HTML download
```

---

## 10. Default values cheat-sheet

| Where | Parameter | Default |
|---|---|---|
| Import review | Latitude / longitude / satellite longitude | **none — required** |
| Import review | Antenna altitude | none (optional) → DEM/grid-DB altitude in Step 5 |
| Import review | Gmax (Tx / Rx) | **50.0 dBi** (Rx copies Tx first) |
| Import review | Antenna diameter | **3.7 m** |
| Import review | Frequency | **11.0 GHz** |
| Import review | Pt | **35.0 dBW** |
| Step 5 | Frequency (no import / Step 3) | 1.0 GHz |
| Step 5 | p | 1 % (< 10 GHz), 0.01 % (≥ 10 GHz) |
| Step 5 | Radio-climatic zone fallback | **A1** (and dc = 0) |
| Step 5 | dtm, dlm, dc | 0.0 km |
| Step 5 | Land fraction / step | 1.0 / 1.0 km |
| Step 5 | **Rain zone** | **none — must be selected** |
| Step 5 | Elevation εs | 30.0° |
| Step 5 | Gx | Gr (Step 3), else 0.0 dBi |
| Step 5 | Filing-altitude horizon adjustment | on |
| Step 5 | Restrict contour to operating sector | **off** |
| Pr(p) dialog | Te / B / Ms / W | 150 K / 12 500 Hz / 4 dB / 0 dB (NL: `NL_DEFAULT`, 1 dB per dialog help) |
| Step 6 | Te / B / test distance / I/N limit | 150 K / 12 500 Hz / 100 km / −10 dB |
| Step 7 | Home country | Azerbaijan |
| Step 7 | Border search radius | 2000 km (Natural Earth 110m) |
| Step 7 | Diffraction recheck / ES height / border-point height | on / 10 m / 0 m |
| Step 7 | I/N limit | derived from Pr(p) and N |
| Step 9 | Zoom / borders layer / DEM / DEM opacity | 7 / on / off / 0.5 |
| Paths | DEM | `LBC_DEM_PATH` or `data_sources/azerbaijan_dem.tif` |

---

## 11. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| *"No .mdb-reading backend is currently installed"* | `pip install access-parser` (or install mdbtools / pyodbc) |
| *"This tool only supports Geostationary (GSO) satellites."* | the station's satellite is in `non_geo`; use a GSO station |
| Proceed blocked: *"Latitude, longitude, and satellite orbital longitude are required."* | the file had no usable value — enter it in the ❗ field |
| Step 5: *"horizon_grid.db not found"* | build the DB or copy it to `data_sources/` |
| Step 5: "Use these parameters" refuses | no horizon profile (check DB coverage for the site) **or** no rain zone selected |
| Altitude shows 0.0 m with a warning | no grid-DB coverage and `srtm` not installed — enter/import an altitude or fix the DB |
| *"Coastline shapefile not found. Using default Zone A1."* | put `ne_10m_coastline.shp` (+ sidecars) in `data_sources/costline/` |
| Mode 2 error / warning about 1–40.5 GHz | Table 5 rain coefficients only cover that range (values are clamped) |
| Step 7 asks for Te/B | the Step 3 table row had none — enter them through the Pr(p) dialog |
| Border analysis warning (download) | no internet / blocked: type border distances manually |
| Satellite/diameter sliders disabled | Gt was typed manually; use the Step 1 calculator to create the geometry |
| Map missing / error | `pip install folium`; DEM overlay needs the GeoTIFF and `rasterio` |
