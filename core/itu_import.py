#!/usr/bin/env python3
"""
core/itu_import.py — Parse an ITU SNS filing database (.mdb, Jet/Access
format) and extract earth-station parameters for auto-population of the
Link Budget Calculator wizard (app.py's "Import from ITU filing database"
section, ahead of Step 1).

Reads five tables from an exported ITU SNS Access database (see the ITU
SNS Preface, Section III Chapter 1, "Details relating to the contents of
the SNS data items"):
    e_stn    — Earth stations: name, coordinates as D/M/S + hemisphere,
               the associated satellite's name and nominal longitude
               (sat_name / long_nom), and ntc_id — the EARTH STATION's
               own notice ID.
    geo      — Geostationary (GSO) satellite notices: sat_name, long_nom,
               and (rarely populated, Plans-only) inclin_exc, under the
               SATELLITE's own ntc_id — a different notice, and a
               different ID space, from the earth station's.
    non_geo  — Non-geostationary (NGSO) satellite notices: sat_name,
               again under the SATELLITE's own ntc_id.
    grp      — Frequency groups (freq_min / freq_max in MHz, pwr_max in
               dBW), keyed by the EARTH STATION's ntc_id (same notice as
               e_stn — this FK is genuine).
    e_ant    — Earth station antennas (gain in dBi, diameter in m,
               emi_rcp direction E/R), keyed by the EARTH STATION's
               ntc_id (same notice as e_stn — this FK is genuine too).

IMPORTANT — two different kinds of "link" in this schema:
  - e_stn.ntc_id -> grp.ntc_id and e_stn.ntc_id -> e_ant.ntc_id ARE
    genuine foreign keys: grp and e_ant both belong to the earth
    station's own notice.
  - e_stn.ntc_id is NOT comparable to geo.ntc_id / non_geo.ntc_id.
    Those hold the SATELLITE's own, separate notice — a completely
    different ID space. The only link the schema actually provides from
    an earth station to its satellite is by NAME: e_stn.sat_name against
    geo.sat_name / non_geo.sat_name. classify_satellite() below joins on
    name for exactly this reason (this was previously joining on ntc_id,
    which could only ever match by coincidence).

This is a screening/import convenience, not an authoritative ITU BR IFIC
parser. The exact .mdb table/column layout varies between SNS exports —
e.g. the non-GSO table shows up as "non_geo" in some exports and "ngeo"
or "non_gso" in others — so every lookup below is written defensively:
table AND column names are matched case-insensitively against a list of
known aliases, and a missing/renamed table or column is reported back to
the caller as a clear error (ItuImportError) or a None/"unknown" result
rather than raising an unhandled exception. Only e_stn is treated as
required — every other table is optional and simply degrades that
table's downstream feature (GSO check, frequency, power, gain, diameter,
orbital data) without blocking the import or warning the user, since an
absent optional table is a completely normal thing for a given export
(e.g. a GSO-only extract legitimately has no non_geo table at all).

READING THE .mdb FILE ITSELF — three backends, tried in order
---------------------------------------------------------------------------
Neither of the two "obvious" approaches is cross-platform on its own:
  - `mdbreader` (pure Python) is effectively unmaintained and fails to
    parse a number of real-world .mdb files.
  - `mdbtools` (the `mdb-export` CLI) has no native Windows build — it's
    a Linux/macOS system package (`apt install mdbtools` /
    `brew install mdbtools`), so a Windows dev machine will never have
    `mdb-export` on PATH.

So `load_mdb()` tries three backends, in this order, and uses whichever
one is actually available / succeeds:

  1. **access-parser** (`pip install access-parser`) — pure Python, no
     system binary or ODBC driver needed, works out of the box on
     Windows/Linux/macOS alike. This is the recommended default and the
     one that fixes the Windows case.
  2. **mdbtools** (`mdb-export` on PATH) — used automatically if
     access-parser isn't installed or fails to parse the file; this is
     what a Linux deployment target (e.g. Streamlit Community Cloud)
     will typically have available via apt.
  3. **pyodbc** + the Microsoft Access Database Engine driver — a
     Windows-only fallback for machines that already have Access (or the
     free "Access Database Engine Redistributable") installed. Requires
     pyodbc's bitness to match the driver's (almost always 64-bit today).

If none of the three are usable, load_mdb() raises ItuImportError with
install instructions for all three so the message is actionable on
whatever OS the user is on.
"""
import io
import logging
import os
import shutil
import subprocess
import tempfile

try:
    from access_parser import AccessParser
except ImportError:
    AccessParser = None

try:
    import pyodbc
except ImportError:
    pyodbc = None

try:
    import pandas as pd
except ImportError:
    pd = None


_log = logging.getLogger(__name__)


class ItuImportError(Exception):
    """
    Raised for conditions the caller should surface as a clean message
    rather than a raw traceback: no usable backend to read the .mdb file
    at all, or the one genuinely required table (e_stn) being entirely
    absent/empty/unparseable. Everything else (geo, non_geo, grp, e_ant
    missing, or a row-level parsing hiccup within them) degrades quietly
    instead of raising — see the module docstring.
    """
    pass


# ---------------------------------------------------------------------------
# Table name aliasing — table names vary between SNS exports (this is
# what fixes the "Table 'non_geo' was not found" issue: some exports
# call it "ngeo" or "non_gso" instead). Every table lookup below goes
# through _resolve_table_name(), which tries each alias in turn,
# case-insensitively, against whatever tables actually exist in the
# file.
# ---------------------------------------------------------------------------

_TABLE_ALIASES = {
    "e_stn": ("e_stn",),
    "geo": ("geo", "gso"),
    "non_geo": ("non_geo", "ngeo", "non_gso", "ngso", "nongeo"),
    "grp": ("grp", "freq_grp", "frequency_grp"),
    "e_ant": ("e_ant", "eant", "ant", "e_antenna", "antenna"),
}
_WANTED_TABLES = tuple(_TABLE_ALIASES.keys())
_CRITICAL_TABLES = ("e_stn",)  # only these block the import if missing


def _resolve_table_name(available_names, canonical_key):
    """
    available_names: iterable of the table names actually present in
    the file (any case). Returns the real, on-disk name for the first
    alias of canonical_key that's present (case-insensitive match), or
    None if none of that key's aliases exist in this file.
    """
    lower_map = {str(n).lower(): n for n in available_names}
    for alias in _TABLE_ALIASES.get(canonical_key, (canonical_key,)):
        real = lower_map.get(alias.lower())
        if real is not None:
            return real
    return None


# ---------------------------------------------------------------------------
# Backend 1 — access-parser (pure Python, cross-platform)
# ---------------------------------------------------------------------------

def _read_via_access_parser(mdb_path):
    if AccessParser is None:
        raise ItuImportError("access-parser is not installed (`pip install access-parser`).")

    db = AccessParser(mdb_path)  # raises on a file it can't parse at all

    try:
        catalog_names = list(db.catalog.keys())
    except Exception:
        catalog_names = []

    tables = {}
    for wanted in _WANTED_TABLES:
        real_name = _resolve_table_name(catalog_names, wanted)
        if real_name is None:
            tables[wanted] = None
            continue
        try:
            raw = db.parse_table(real_name)  # {column: [values...]}, table[col][row]
            tables[wanted] = pd.DataFrame(dict(raw))
        except Exception:
            tables[wanted] = None
    return tables


# ---------------------------------------------------------------------------
# Backend 2 — mdbtools (mdb-export / mdb-tables CLI; Linux/macOS)
# ---------------------------------------------------------------------------

def _read_via_mdbtools(mdb_path):
    if shutil.which("mdb-export") is None:
        raise ItuImportError(
            "mdbtools is not installed or not on PATH "
            "(Ubuntu/Debian: `sudo apt install mdbtools`, macOS: `brew install mdbtools`; "
            "no native Windows build)."
        )

    # List actual table names first (mdb-tables -1 -> one name per line) so
    # aliases can be resolved the same way as the other two backends. If
    # mdb-tables itself isn't available for some reason, fall back to
    # trying each canonical name directly against mdb-export — that still
    # works for files using the exact canonical names, just without alias
    # matching.
    available_names = []
    if shutil.which("mdb-tables") is not None:
        try:
            result = subprocess.run(
                ["mdb-tables", "-1", mdb_path],
                capture_output=True, text=True, check=True,
            )
            available_names = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        except Exception:
            available_names = []

    tables = {}
    for wanted in _WANTED_TABLES:
        if available_names:
            real_name = _resolve_table_name(available_names, wanted)
            if real_name is None:
                tables[wanted] = None
                continue
        else:
            real_name = wanted  # best effort without a table listing

        try:
            result = subprocess.run(
                ["mdb-export", mdb_path, real_name],
                capture_output=True, text=True, check=True,
            )
        except subprocess.CalledProcessError:
            tables[wanted] = None
            continue
        if not result.stdout.strip():
            tables[wanted] = pd.DataFrame()
            continue
        try:
            tables[wanted] = pd.read_csv(io.StringIO(result.stdout))
        except Exception:
            tables[wanted] = None
    return tables


# ---------------------------------------------------------------------------
# Backend 3 — pyodbc + Microsoft Access driver (Windows, if installed)
# ---------------------------------------------------------------------------

def _read_via_pyodbc(mdb_path):
    if pyodbc is None:
        raise ItuImportError("pyodbc is not installed (`pip install pyodbc`).")

    conn_str = r"DRIVER={Microsoft Access Driver (*.mdb, *.accdb)};" rf"DBQ={mdb_path};"
    try:
        conn = pyodbc.connect(conn_str, autocommit=True)
    except Exception as e:
        raise ItuImportError(
            "Could not connect via the Microsoft Access ODBC driver. Install the free "
            "\"Microsoft Access Database Engine Redistributable\" (pick the version — 32/64-bit — "
            f"that matches your Python installation's bitness): {e}"
        ) from e

    try:
        cursor = conn.cursor()
        available_names = [r.table_name for r in cursor.tables(tableType="TABLE")]
        tables = {}
        for wanted in _WANTED_TABLES:
            real = _resolve_table_name(available_names, wanted)
            if real is None:
                tables[wanted] = None
                continue
            try:
                tables[wanted] = pd.read_sql(f"SELECT * FROM [{real}]", conn)
            except Exception:
                tables[wanted] = None
        return tables
    finally:
        conn.close()


_BACKENDS = (
    ("access-parser (pure Python)", _read_via_access_parser, lambda: AccessParser is not None),
    ("mdbtools (mdb-export)", _read_via_mdbtools, lambda: shutil.which("mdb-export") is not None),
    ("pyodbc (Microsoft Access driver)", _read_via_pyodbc, lambda: pyodbc is not None),
)


def list_available_backends():
    """
    Names of the .mdb-reading backends that are actually usable right now
    (module importable / binary on PATH) — informational only, for the UI
    to show something like "Ready via: access-parser" or, if empty, a
    prompt to install one, instead of hard-blocking the uploader the way
    a single-backend "is mdbtools installed?" check would.
    """
    return [label for label, _fn, is_available in _BACKENDS if is_available()]


# ---------------------------------------------------------------------------
# Column helpers
# ---------------------------------------------------------------------------

def _get_col(df, *candidates):
    """
    Case-insensitive lookup of the first matching column name present in
    df, out of a list of candidate spellings. Returns the actual column
    name as it appears in df, or None if none of the candidates exist.
    """
    if df is None or len(df.columns) == 0:
        return None
    lower_map = {str(c).lower(): c for c in df.columns}
    for cand in candidates:
        real = lower_map.get(cand.lower())
        if real is not None:
            return real
    return None


def _normalize_sat_name(name):
    """
    Case/whitespace-insensitive key for matching a satellite name across
    e_stn/geo/non_geo — administrations aren't perfectly consistent
    about case or spacing between an earth station filing and the
    satellite's own filing, so this join is inherently best-effort (see
    classify_satellite()'s docstring). Returns None for a missing/blank
    name.
    """
    if name is None:
        return None
    if pd is not None and isinstance(name, float) and pd.isna(name):
        return None
    s = str(name).strip().upper()
    return s or None


# ---------------------------------------------------------------------------
# File loading — tries each backend in _BACKENDS order, keeps the first
# one that both runs without error AND actually finds a non-empty e_stn
# table (a backend that opens the file but sees zero of our tables is
# more likely the wrong parser for this file than a truly empty database,
# so we keep trying rather than returning an all-None result silently).
# ---------------------------------------------------------------------------

def load_mdb(file_bytes, filename_hint="upload.mdb"):
    """
    Parse an uploaded .mdb file's raw bytes (e.g. from
    st.file_uploader(...).getvalue()) and return its five tables of
    interest as a dict of DataFrames:

        {"e_stn": DataFrame or None, "geo": DataFrame or None,
         "non_geo": DataFrame or None, "grp": DataFrame or None,
         "e_ant": DataFrame or None}

    A None value for anything other than e_stn means that table simply
    isn't present under any of its known aliases in this file — not
    fatal by itself; classify_satellite(), extract_group_data(),
    extract_antenna_data(), and extract_satellite_orbit_data() all
    degrade gracefully when their table is missing. Only e_stn is
    required (see extract_earth_stations()).

    See the module docstring for the three backends this tries, in
    order, and why no single one is enough on every platform.

    Raises
    ------
    ItuImportError
        if pandas isn't installed, the upload is empty, or none of the
        three backends could read a usable e_stn table from this file —
        the exception message lists what was tried and why each one
        failed, plus install instructions for all three.
    """
    if pd is None:
        raise ItuImportError("ITU database import requires pandas (`pip install pandas`).")
    if not file_bytes:
        raise ItuImportError("Uploaded file is empty.")

    suffix = os.path.splitext(filename_hint)[1] or ".mdb"
    tmp_path = None
    attempts = []
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(file_bytes)
            tmp_path = tmp.name

        for backend_label, backend_fn, is_available in _BACKENDS:
            if not is_available():
                attempts.append(f"{backend_label}: not installed / not on PATH")
                continue
            try:
                tables = backend_fn(tmp_path)
            except Exception as e:
                attempts.append(f"{backend_label}: {e}")
                continue

            e_stn = tables.get("e_stn")
            if e_stn is not None and len(e_stn) > 0:
                return tables
            attempts.append(f"{backend_label}: opened the file, but found no usable 'e_stn' table")

        raise ItuImportError(
            f"Could not read an earth-station table from {filename_hint!r} with any available backend:\n"
            + "\n".join(f"  - {a}" for a in attempts)
            + "\n\nInstall one of:\n"
              "  - `pip install access-parser`  (pure Python, works on Windows/Linux/macOS — recommended)\n"
              "  - mdbtools  (Linux: `sudo apt install mdbtools`, macOS: `brew install mdbtools`)\n"
              "  - `pip install pyodbc` + the Microsoft Access Database Engine Redistributable (Windows)"
        )
    finally:
        if tmp_path is not None:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


# ---------------------------------------------------------------------------
# Coordinate conversion
# ---------------------------------------------------------------------------

def dms_to_decimal(deg, minute, sec, hemisphere):
    """
    Convert a degrees/minutes/seconds + hemisphere-letter coordinate to
    signed decimal degrees. South and West are negative, matching the
    +N/+E, -S/-W sign convention used everywhere else in this app
    (e.g. calculations.py's zeta, Step 1/5's latitude/longitude fields).

    minute/sec of None are treated as 0 (some ITU records omit seconds,
    occasionally even minutes) — only a missing `deg` or an unrecognised
    hemisphere letter is an error, since those leave nothing usable to
    convert.

    Raises
    ------
    ValueError
        if deg is None, or hemisphere isn't one of N/S/E/W
        (case-insensitive, surrounding whitespace ignored).
    """
    if deg is None or (isinstance(deg, float) and pd is not None and pd.isna(deg)):
        raise ValueError("Missing degrees value — cannot convert to decimal.")

    def _num(v):
        if v is None:
            return 0.0
        if pd is not None and isinstance(v, float) and pd.isna(v):
            return 0.0
        return float(v)

    hemi = (str(hemisphere) if hemisphere is not None else "").strip().upper()
    if hemi not in ("N", "S", "E", "W"):
        raise ValueError(f"Unrecognised hemisphere indicator: {hemisphere!r} (expected one of N/S/E/W)")

    decimal = abs(_num(deg)) + _num(minute) / 60.0 + _num(sec) / 3600.0
    if hemi in ("S", "W"):
        decimal = -decimal
    return decimal


# ---------------------------------------------------------------------------
# e_stn -> station list
# ---------------------------------------------------------------------------

_NAME_COLUMNS = ("stn_name", "e_stn_name", "name", "station_name")
_NTC_ID_COLUMNS = ("ntc_id",)
_LAT_DEG_COLUMNS = ("lat_deg",)
_LAT_MIN_COLUMNS = ("lat_min",)
_LAT_SEC_COLUMNS = ("lat_sec",)
_LAT_NS_COLUMNS = ("lat_ns",)
_LON_DEG_COLUMNS = ("long_deg", "lon_deg")
_LON_MIN_COLUMNS = ("long_min", "lon_min")
_LON_SEC_COLUMNS = ("long_sec", "lon_sec")
_LON_EW_COLUMNS = ("long_ew", "lon_ew")
_SAT_NAME_COLUMNS = ("sat_name",)
_SAT_LONG_NOM_COLUMNS = ("long_nom",)
# e_stn operating azimuth sector (AP4 item A.7.c): the ITU SNS column names are
# azm_fr / azm_to (clockwise from true north, 9(3).9). "az_to" etc. are accepted as aliases.
# e_stn antenna altitude (AP4 item A.7.d, S9(5), metres above sea level).
_ANT_ALT_COLUMNS = ("ant_alt", "ant_alt_m", "altitude", "height_msl")
_AZM_FR_COLUMNS = ("azm_fr", "az_fr", "azimuth_from")
_AZM_TO_COLUMNS = ("azm_to", "az_to", "azimuth_to")


def extract_earth_stations(e_stn_df):
    """
    Convert the e_stn table into a list of per-station dicts:

        {"label": str,               # display name for the dropdown
         "ntc_id": <value>,          # raw ntc_id, whatever dtype it is
         "lat": float or None,       # decimal degrees, +N/-S
         "lon": float or None,       # decimal degrees, +E/-W
         "lat_error": str or None,   # set instead of `lat` on failure
         "lon_error": str or None,   # set instead of `lon` on failure
         "sat_name": str or None,    # associated satellite's name
                                      # (e_stn.sat_name) — the join key
                                      # classify_satellite() uses; NOT
                                      # the same thing as ntc_id
         "sat_long_nom": float or None,  # associated satellite's
                                          # nominal longitude, deg,
                                          # +E/-W (e_stn.long_nom) —
                                          # read straight off this row,
                                          # no join needed
         "azm_fr": float or None,    # e_stn.azm_fr, operating azimuth sector start (deg)
         "az_to": float or None,     # e_stn.azm_to, sector end (deg). NaN/blank -> None
         "row_index": <index>}

    A per-row coordinate-conversion failure (missing degrees, an
    unrecognised hemisphere letter, ...) is captured in lat_error/
    lon_error for that station rather than raising, so one bad row in a
    large export doesn't block every other station from being usable —
    the caller (app.py) is expected to show that station as
    non-importable rather than crashing the whole import. sat_name/
    sat_long_nom simply come back None if those columns aren't present
    or don't parse — neither is required for the coordinate import to
    work.

    Raises
    ------
    ItuImportError
        if e_stn_df is None or empty, or is missing a column needed for
        every row (ntc_id, or the degrees columns) — i.e. a structural
        problem, not a per-row data problem. e_stn is the one table this
        module treats as required (see the module docstring).
    """
    if e_stn_df is None:
        raise ItuImportError("The 'e_stn' table was not found in this database file.")
    if len(e_stn_df) == 0:
        raise ItuImportError("The 'e_stn' table is empty — no earth stations to import.")

    name_col = _get_col(e_stn_df, *_NAME_COLUMNS)
    ntc_col = _get_col(e_stn_df, *_NTC_ID_COLUMNS)
    lat_deg_col = _get_col(e_stn_df, *_LAT_DEG_COLUMNS)
    lat_min_col = _get_col(e_stn_df, *_LAT_MIN_COLUMNS)
    lat_sec_col = _get_col(e_stn_df, *_LAT_SEC_COLUMNS)
    lat_ns_col = _get_col(e_stn_df, *_LAT_NS_COLUMNS)
    lon_deg_col = _get_col(e_stn_df, *_LON_DEG_COLUMNS)
    lon_min_col = _get_col(e_stn_df, *_LON_MIN_COLUMNS)
    lon_sec_col = _get_col(e_stn_df, *_LON_SEC_COLUMNS)
    lon_ew_col = _get_col(e_stn_df, *_LON_EW_COLUMNS)
    sat_name_col = _get_col(e_stn_df, *_SAT_NAME_COLUMNS)
    sat_long_nom_col = _get_col(e_stn_df, *_SAT_LONG_NOM_COLUMNS)
    azm_fr_col = _get_col(e_stn_df, *_AZM_FR_COLUMNS)
    azm_to_col = _get_col(e_stn_df, *_AZM_TO_COLUMNS)
    ant_alt_col = _get_col(e_stn_df, *_ANT_ALT_COLUMNS)

    if ntc_col is None:
        raise ItuImportError(
            "The 'e_stn' table has no 'ntc_id' column — cannot match it against "
            "the 'grp'/'e_ant' tables."
        )
    if lat_deg_col is None or lon_deg_col is None or lat_ns_col is None or lon_ew_col is None:
        raise ItuImportError(
            "The 'e_stn' table is missing the expected coordinate columns "
            "(lat_deg/lat_ns and long_deg/long_ew, ...) — cannot convert coordinates."
        )

    stations = []
    for idx, row in e_stn_df.iterrows():
        ntc_id = row.get(ntc_col)

        raw_name = row.get(name_col) if name_col else None
        name = str(raw_name).strip() if raw_name not in (None, "") else ""
        if not name or (pd is not None and pd.isna(raw_name) if raw_name is not None else False):
            name = f"Station #{idx} (ntc_id={ntc_id})"

        lat, lat_error = None, None
        try:
            lat = dms_to_decimal(
                row.get(lat_deg_col),
                row.get(lat_min_col) if lat_min_col else None,
                row.get(lat_sec_col) if lat_sec_col else None,
                row.get(lat_ns_col),
            )
        except Exception as e:
            lat_error = str(e)

        lon, lon_error = None, None
        try:
            lon = dms_to_decimal(
                row.get(lon_deg_col),
                row.get(lon_min_col) if lon_min_col else None,
                row.get(lon_sec_col) if lon_sec_col else None,
                row.get(lon_ew_col),
            )
        except Exception as e:
            lon_error = str(e)

        sat_name = None
        if sat_name_col:
            raw_sat_name = row.get(sat_name_col)
            sat_name = _normalize_sat_name(raw_sat_name)
            # Keep the original (non-uppercased) form for display purposes;
            # _normalize_sat_name is what classify_satellite() actually
            # matches on internally, but the label shown to the user should
            # look like the source data, not a shouty all-caps version.
            if sat_name is not None and raw_sat_name is not None:
                sat_name = str(raw_sat_name).strip()

        sat_long_nom = None
        if sat_long_nom_col:
            try:
                val = row.get(sat_long_nom_col)
                sat_long_nom = None if (pd is not None and pd.isna(val)) else float(val)
            except (TypeError, ValueError):
                sat_long_nom = None

        azm_fr = _to_float_or_none(row.get(azm_fr_col)) if azm_fr_col else None
        az_to = _to_float_or_none(row.get(azm_to_col)) if azm_to_col else None
        ant_alt = _to_float_or_none(row.get(ant_alt_col)) if ant_alt_col else None  # m above MSL, NaN/blank -> None

        stations.append({
            "label": name,
            "ntc_id": ntc_id,
            "lat": lat, "lon": lon,
            "lat_error": lat_error, "lon_error": lon_error,
            "sat_name": sat_name,
            "sat_long_nom": sat_long_nom,
            "azm_fr": azm_fr,   # operating azimuth sector start (deg from true north, clockwise) or None
            "az_to": az_to,     # operating azimuth sector end (e_stn.azm_to) or None
            "ant_alt": ant_alt, # antenna altitude, m above mean sea level (e_stn.ant_alt) or None
            "row_index": idx,
        })

    return stations


# ---------------------------------------------------------------------------
# GSO / non-GSO classification — joins on satellite NAME, not ntc_id.
# See the module docstring: geo/non_geo hold the satellite's own,
# separate notice, in its own ntc_id space, so ntc_id is not a valid
# join key here. The only link the schema gives from an earth station to
# its satellite is e_stn.sat_name -> geo.sat_name / non_geo.sat_name.
# ---------------------------------------------------------------------------

def classify_satellite(sat_name, geo_df, non_geo_df):
    """
    Determine whether the satellite named sat_name (e.g. from an earth
    station's `sat_name` field — see extract_earth_stations()) is GSO
    ('geo') or non-GSO ('non_geo'), by looking its name up in the geo
    and non_geo tables' own `sat_name` columns.

    non_geo is checked first (matching the required behaviour: a hit in
    non_geo must block the import regardless of whether the same name
    also happens to appear in geo).

    This is a NAME match (case/whitespace-insensitive — see
    _normalize_sat_name()), not an ID match: geo.ntc_id and
    non_geo.ntc_id belong to the satellite's own notice, which is a
    completely different notice — and a different ID space — from the
    earth station's own ntc_id, so ntc_id can't be used to join earth
    stations to satellites in this schema. Name matching is inherently
    best-effort (administrations aren't always perfectly consistent
    about case/spacing between an earth station filing and the
    satellite's own filing).

    Returns "non_geo", "geo", or "unknown" — "unknown" means sat_name is
    missing/blank, or wasn't found in either table (missing table under
    any of its known aliases — see _TABLE_ALIASES — or the notice simply
    isn't present in this extract); the caller should treat that as "GSO
    status could not be verified" rather than either a pass or a hard
    failure. A missing non_geo table specifically (e.g. a GSO-only
    export) is completely normal here and never itself raises or warns
    — this function just quietly can't confirm the satellite ISN'T
    non-GSO from that table.
    """
    norm = _normalize_sat_name(sat_name)
    if norm is None:
        return "unknown"

    def _has_name(df):
        if df is None:
            return False
        col = _get_col(df, *_SAT_NAME_COLUMNS)
        if col is None:
            return False
        try:
            return bool(df[col].map(_normalize_sat_name).eq(norm).any())
        except Exception:
            return False

    if _has_name(non_geo_df):
        return "non_geo"
    if _has_name(geo_df):
        return "geo"
    return "unknown"


# ---------------------------------------------------------------------------
# Best-effort satellite orbital data (geo -> inclination), by name
# ---------------------------------------------------------------------------

_INCLIN_EXC_COLUMNS = ("inclin_exc",)


def extract_satellite_orbit_data(sat_name, geo_df):
    """
    Best-effort extra orbital data for the satellite named sat_name,
    looked up (by name — see classify_satellite()'s docstring) in the
    geo table.

    Currently just inclin_exc ("inclination excursion", degrees) — per
    the ITU SNS Preface this is a Plans/AP30B-specific field, NOT a
    general orbital inclination angle, and is frequently blank for an
    ordinary Article 9/11 GSO earth station filing outside a Plan. A
    value returned here is a hint worth a quick look, not something to
    treat with the same confidence as coordinates/frequency/power —
    app.py surfaces it as an informational note rather than silently
    pre-filling a calculation field with it.

    Note that a satellite's nominal orbital LONGITUDE does NOT need this
    lookup: it's already available directly on the earth station's own
    e_stn row (see extract_earth_stations()'s "sat_long_nom"), since
    e_stn carries its own copy (e_stn.long_nom) of the associated
    satellite's nominal longitude.

    Returns
    -------
    dict {"inclin_exc_deg": float or None}
    or None if geo_df is missing, has no sat_name/inclin_exc columns, or
    no row matches sat_name.
    """
    norm = _normalize_sat_name(sat_name)
    if norm is None or geo_df is None:
        return None

    name_col = _get_col(geo_df, *_SAT_NAME_COLUMNS)
    inc_col = _get_col(geo_df, *_INCLIN_EXC_COLUMNS)
    if name_col is None or inc_col is None:
        return None

    try:
        matches = geo_df[geo_df[name_col].map(_normalize_sat_name) == norm]
    except Exception:
        return None
    if len(matches) == 0:
        return None

    row = matches.iloc[0]
    try:
        val = row[inc_col]
        inc = None if (pd is not None and pd.isna(val)) else float(val)
    except (TypeError, ValueError):
        inc = None

    return {"inclin_exc_deg": inc}


# ---------------------------------------------------------------------------
# Antenna extraction (e_ant -> gain, diameter) — e_ant's real key is
# (ntc_id, emi_rcp, beam_name): emi_rcp is 'E' (transmit) or 'R'
# (receive). An optional emi_rcp filter lets the caller prefer the
# transmit-direction row for Gt and the receive-direction row for Gr,
# rather than an arbitrary "whichever row comes first" pick.
# ---------------------------------------------------------------------------

_GAIN_COLUMNS = ("gain", "ant_gain", "gmax", "antenna_gain")
_ANT_DIAM_COLUMNS = ("ant_diam", "diameter", "ant_diameter", "diam", "antenna_diameter")
_EMI_RCP_COLUMNS = ("emi_rcp",)
_BEAM_NAME_COLUMNS = ("beam_name",)


def _to_float_or_none(value):
    """
    float(value), or None for anything that isn't a usable finite number:
    None, NaN (what pandas yields for an empty cell, e.g. via mdbtools'
    CSV export), empty/whitespace strings, non-numeric text, +/-inf.
    (Plain float(nan) does NOT raise, so without this a blank cell would
    leak through as NaN and look like a real value to the caller.)
    """
    if value is None:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if f != f or f in (float("inf"), float("-inf")):
        return None
    return f


def extract_antenna_data(ntc_id, e_ant_df, emi_rcp=None, beam_name=None):
    """
    Extract antenna parameters from the e_ant table for a given ntc_id.

    IMPORTANT — what e_ant.gain IS: per the ITU SNS Preface (e_ant, item
    B.5.a) it is the antenna's "maximum isotropic gain", i.e. the MAIN-BEAM
    AXIS gain Gmax. It is NOT the horizon gain Gt/Gr = G(phi) used in the
    link budget (AP7 Annex 3, Eq. 84-97); that has to be calculated from
    Gmax. This function therefore returns it under "gain_dbi" purely as an
    input for that calculation — callers must never store it as Gt/Gr.

    Parameters
    ----------
    ntc_id : the notice ID to look up (the EARTH STATION's own ntc_id).
    e_ant_df : the e_ant DataFrame (or None).
    emi_rcp : optional filter — 'E' for transmit beams, 'R' for receive
              beams (case-insensitive). If None, the first row matching
              ntc_id is used regardless of direction.
              If given and e_ant has an emi_rcp column, ONLY rows of that
              direction are considered; if none exist for this ntc_id the
              result is None (a transmit row is never silently returned as
              the receive antenna). If e_ant has no emi_rcp column at all
              the direction can't be told apart, so the first row is used.

    Returns
    -------
    dict with keys:
        "gain_dbi"    : float or None  (main-beam axis gain Gmax)
        "ant_diam_m"  : float or None  (antenna diameter in meters)
        "beam_name"   : str or None
        "match_count" : int  (how many rows matched this ntc_id — after the
                              emi_rcp filter, when one was applied)
    or None if e_ant_df is missing/empty, lacks both a gain and a diameter
    column, or no rows match.

    Column names are looked up case-insensitively (gain/ant_gain/gmax,
    ant_diam/diameter/..., beam_name, emi_rcp). Empty/NaN/unparseable cells
    become None individually — a missing gain doesn't withhold the diameter.
    """
    if e_ant_df is None or len(e_ant_df) == 0:
        return None

    ntc_col = _get_col(e_ant_df, "ntc_id")
    gain_col = _get_col(e_ant_df, *_GAIN_COLUMNS)
    diam_col = _get_col(e_ant_df, *_ANT_DIAM_COLUMNS)
    beam_col = _get_col(e_ant_df, *_BEAM_NAME_COLUMNS)
    emi_col = _get_col(e_ant_df, *_EMI_RCP_COLUMNS)
    if ntc_col is None or (gain_col is None and diam_col is None):
        return None

    try:
        matches = e_ant_df[e_ant_df[ntc_col] == ntc_id]
    except Exception:
        return None

    if emi_rcp and emi_col is not None and len(matches) > 0:
        try:
            wanted = str(emi_rcp).strip().upper()
            matches = matches[matches[emi_col].astype(str).str.strip().str.upper() == wanted]
        except Exception:
            return None

    if beam_name and beam_col is not None and len(matches) > 0:
        try:
            wanted_beam = str(beam_name).strip().upper()
            matches = matches[matches[beam_col].astype(str).str.strip().str.upper() == wanted_beam]
        except Exception:
            return None

    if len(matches) == 0:
        return None

    row = matches.iloc[0]

    gain_dbi = _to_float_or_none(row[gain_col]) if gain_col is not None else None
    ant_diam_m = _to_float_or_none(row[diam_col]) if diam_col is not None else None

    beam_name = None
    if beam_col is not None:
        raw = row[beam_col]
        if raw is not None and not (isinstance(raw, float) and raw != raw):
            beam_name = str(raw).strip() or None

    return {
        "gain_dbi": gain_dbi,
        "ant_diam_m": ant_diam_m,
        "beam_name": beam_name,
        "match_count": int(len(matches)),
    }


# ---------------------------------------------------------------------------
# Frequency / power extraction (grp -> freq_min/freq_max, pwr_max)
# ---------------------------------------------------------------------------

_PWR_MAX_COLUMNS = ("pwr_max", "power_max", "max_power", "p_max")


def extract_group_data(ntc_id, grp_df, freq_mode="average"):
    """
    Find the grp table row(s) matching ntc_id and return frequency (GHz,
    converted from freq_min/freq_max in MHz) AND maximum transmit power
    (dBW, from pwr_max) together, from a single lookup/row.

    freq_mode: "average" (default — mean of freq_min and freq_max) or
    "min" (just freq_min).

    If more than one grp row matches the same ntc_id (multiple frequency
    groups on the same notice), the first is used; match_count tells the
    caller how many total matches there were.

    Returns
    -------
    dict {"freq_ghz", "freq_min_mhz", "freq_max_mhz",
          "pwr_max_dbw", "match_count"}
        pwr_max_dbw is None (just that one field, not the whole dict) if
        no pwr_max-like column is present, or it doesn't parse for the
        matched row — a missing power figure doesn't withhold the
        frequency that WAS found.
    or None if grp_df is missing, has neither freq_min nor freq_max, or
    has no row for this ntc_id.
    """
    if grp_df is None:
        return None

    ntc_col = _get_col(grp_df, "ntc_id")
    fmin_col = _get_col(grp_df, "freq_min")
    fmax_col = _get_col(grp_df, "freq_max")
    if ntc_col is None or fmin_col is None or fmax_col is None:
        return None

    try:
        matches = grp_df[grp_df[ntc_col] == ntc_id]
    except Exception:
        return None
    if len(matches) == 0:
        return None

    row = matches.iloc[0]
    try:
        fmin = float(row[fmin_col])
        fmax = float(row[fmax_col])
    except (TypeError, ValueError):
        return None

    freq_mhz = fmin if freq_mode == "min" else (fmin + fmax) / 2.0

    pwr_col = _get_col(grp_df, *_PWR_MAX_COLUMNS)
    pwr_max_dbw = None
    if pwr_col is not None:
        try:
            pwr_max_dbw = float(row[pwr_col])
        except (TypeError, ValueError):
            pwr_max_dbw = None

    return {
        "freq_ghz": freq_mhz / 1000.0,
        "freq_min_mhz": fmin,
        "freq_max_mhz": fmax,
        "pwr_max_dbw": pwr_max_dbw,
        "match_count": len(matches),
    }


def extract_frequency_ghz(ntc_id, grp_df, mode="average"):
    """
    Back-compat wrapper around extract_group_data(): same frequency
    lookup, returned without the pwr_max_dbw key. Kept for any existing
    caller that only wants frequency and doesn't expect a power field in
    the result.
    """
    result = extract_group_data(ntc_id, grp_df, freq_mode=mode)
    if result is None:
        return None
    return {k: v for k, v in result.items() if k != "pwr_max_dbw"}


# ---------------------------------------------------------------------------
# Multi-frequency extraction (grp -> ALL frequency groups of a notice)
# ---------------------------------------------------------------------------

_GRP_ID_COLUMNS = ("grp_id",)
_NOISE_T_COLUMNS = ("noise_t",)


def _norm_direction(value):
    """'E' / 'R' (case/space-insensitive) -> that letter; anything else -> None."""
    if value is None or (isinstance(value, float) and value != value):
        return None
    v = str(value).strip().upper()
    return v if v in ("E", "R") else None


def extract_all_frequencies(ntc_id, grp_df, e_ant_df=None):
    """
    Extract ALL frequency groups (grp rows) for a given ntc_id, with
    transmit/receive classification and, when e_ant_df is given, the matching
    antenna data.

    ntc_id is the EARTH STATION's own notice id (grp.ntc_id is a genuine FK
    to it — see the module docstring).

    Returns
    -------
    list of dicts, one per usable grp row, in table order:
        {"grp_id": int or None,
         "freq_min_mhz": float, "freq_max_mhz": float,
         "freq_center_ghz": float,          # (min + max) / 2 / 1000
         "emi_rcp": 'E' | 'R' | None,       # None = missing/unrecognised
         "beam_name": str or None,
         "noise_t": float or None,          # grp.noise_t (K) — the receiving
                                            # system noise temperature of THIS
                                            # station's own receive chain
         "pwr_max_dbw": float or None,      # grp.pwr_max
         "ant_gain_dbi": float or None,     # e_ant.gain = Gmax, NOT Gt/Gr
         "ant_diam_m": float or None}       # e_ant.ant_diam
    Rows without a parseable freq_min AND freq_max are skipped (a warning is
    logged). Returns [] if grp_df is missing/empty, has no ntc_id/freq_min/
    freq_max column, or no row matches ntc_id.

    Antenna matching: (ntc_id, emi_rcp, beam_name) first; if that finds
    nothing, (ntc_id, emi_rcp) only; if the group has no direction, the
    first e_ant row of the notice. Antenna values are None when e_ant_df is
    missing or nothing matches.
    """
    if grp_df is None or len(grp_df) == 0:
        return []

    ntc_col = _get_col(grp_df, "ntc_id")
    fmin_col = _get_col(grp_df, "freq_min")
    fmax_col = _get_col(grp_df, "freq_max")
    if ntc_col is None or fmin_col is None or fmax_col is None:
        return []
    grp_id_col = _get_col(grp_df, *_GRP_ID_COLUMNS)
    emi_col = _get_col(grp_df, *_EMI_RCP_COLUMNS)
    beam_col = _get_col(grp_df, *_BEAM_NAME_COLUMNS)
    noise_col = _get_col(grp_df, *_NOISE_T_COLUMNS)
    pwr_col = _get_col(grp_df, *_PWR_MAX_COLUMNS)

    try:
        matches = grp_df[grp_df[ntc_col] == ntc_id]
    except Exception:
        return []

    out = []
    for idx, row in matches.iterrows():
        fmin = _to_float_or_none(row[fmin_col])
        fmax = _to_float_or_none(row[fmax_col])
        if fmin is None or fmax is None:
            _log.warning("grp row %r (ntc_id=%r) skipped: missing freq_min/freq_max", idx, ntc_id)
            continue
        lo, hi = min(fmin, fmax), max(fmin, fmax)

        grp_id = None
        if grp_id_col is not None:
            g = _to_float_or_none(row[grp_id_col])
            grp_id = int(g) if g is not None else None

        emi = _norm_direction(row[emi_col]) if emi_col is not None else None

        beam = None
        if beam_col is not None:
            raw = row[beam_col]
            if raw is not None and not (isinstance(raw, float) and raw != raw):
                beam = str(raw).strip() or None

        ant = None
        if e_ant_df is not None:
            if beam:
                ant = extract_antenna_data(ntc_id, e_ant_df, emi_rcp=emi, beam_name=beam)
            if ant is None:
                ant = extract_antenna_data(ntc_id, e_ant_df, emi_rcp=emi)

        out.append({
            "grp_id": grp_id,
            "freq_min_mhz": lo,
            "freq_max_mhz": hi,
            "freq_center_ghz": (lo + hi) / 2.0 / 1000.0,
            "emi_rcp": emi,
            "beam_name": beam,
            "noise_t": _to_float_or_none(row[noise_col]) if noise_col is not None else None,
            "pwr_max_dbw": _to_float_or_none(row[pwr_col]) if pwr_col is not None else None,
            "ant_gain_dbi": (ant or {}).get("gain_dbi"),
            "ant_diam_m": (ant or {}).get("ant_diam_m"),
        })
    return out


def classify_frequencies_by_direction(frequencies):
    """
    Split extract_all_frequencies() output by direction:
        {"transmit": [...],   # emi_rcp == 'E'
         "receive":  [...],   # emi_rcp == 'R'
         "unknown":  [...]}   # emi_rcp None / unrecognised
    Order within each list is preserved. None/[] gives three empty lists.
    """
    result = {"transmit": [], "receive": [], "unknown": []}
    for f in frequencies or []:
        d = _norm_direction(f.get("emi_rcp"))
        result["transmit" if d == "E" else "receive" if d == "R" else "unknown"].append(f)
    return result


# ---------------------------------------------------------------------------
# Operating azimuth sector (e_stn.azm_fr / azm_to) helpers
# ---------------------------------------------------------------------------

def normalize_azimuth_sector(azm_fr, az_to):
    """
    Interpret an (azm_fr, az_to) pair (degrees clockwise from true north).

    Returns a dict:
        {"defined":  bool,   # both values present and in [0, 360]
         "restrict": bool,   # True only for a real, partial sector
         "azm_fr": float|None, "az_to": float|None,
         "wraps": bool,      # sector crosses 0/360 (e.g. 350 -> 10)
         "width_deg": float|None,
         "note": str|None}   # human-readable reason when restrict is False
    Missing values, out-of-range values, azm_fr == az_to and 0 -> 360 are all
    treated as "no restriction" (full 360 degrees) with an explanatory note.
    """
    fr, to = _to_float_or_none(azm_fr), _to_float_or_none(az_to)
    base = {"defined": False, "restrict": False, "azm_fr": fr, "az_to": to,
            "wraps": False, "width_deg": None, "note": None}
    if fr is None or to is None:
        base["note"] = "No azimuth range specified in the MDB file (full 360\u00b0)."
        base["azm_fr"] = base["az_to"] = None if (fr is None and to is None) else base["azm_fr"]
        return base
    if not (0.0 <= fr <= 360.0 and 0.0 <= to <= 360.0):
        base["note"] = f"Azimuth range {fr:g}\u00b0\u2013{to:g}\u00b0 is outside 0\u2013360\u00b0 (ignored; full 360\u00b0)."
        return base
    base["defined"] = True
    width = (to - fr) % 360.0
    if width < 1e-9:
        base["note"] = (f"Azimuth range {fr:g}\u00b0 to {to:g}\u00b0 spans the full circle (full 360\u00b0).")
        base["width_deg"] = 360.0
        return base
    base.update(restrict=True, wraps=(to < fr), width_deg=width)
    return base


def azimuth_in_sector(az, azm_fr, az_to, tol=1e-9):
    """True if azimuth az lies in the clockwise sector azm_fr -> az_to (inclusive; handles wrap)."""
    return ((az - azm_fr) % 360.0) <= ((az_to - azm_fr) % 360.0) + tol


def filter_profile_to_sector(profile, sector):
    """
    Restrict a horizon profile [(az, eps_h, dh), ...] to a sector from
    normalize_azimuth_sector(). The result is ordered clockwise starting at
    azm_fr (so a 350 -> 10 sector reads 350, 355, 0, 5, 10 — contiguous for
    plotting). If sector["restrict"] is False the profile is returned
    unchanged (original order).

    Returns (filtered_profile, info) where info = {"filtered": bool,
    "n_in": int, "n_total": int, "warnings": [str, ...]}.
    Warnings cover: a sector narrower than the grid spacing (nearest grid
    azimuth used), grid azimuths expected inside the sector but absent from
    the profile (gaps), and sector ends falling between grid azimuths.
    """
    profile = list(profile or [])
    info = {"filtered": False, "n_in": len(profile), "n_total": len(profile), "warnings": []}
    if not sector or not sector.get("restrict") or not profile:
        return profile, info
    fr, to, width = sector["azm_fr"], sector["az_to"], sector["width_deg"]

    inside = [p for p in profile if azimuth_in_sector(p[0], fr, to)]
    azs = sorted({p[0] for p in profile})
    incr = min((b - a for a, b in zip(azs, azs[1:]) if b > a), default=None)

    if not inside:
        centre = (fr + width / 2.0) % 360.0
        nearest = min(profile, key=lambda p: min((p[0] - centre) % 360.0, (centre - p[0]) % 360.0))
        inside = [nearest]
        spacing = " (%g\u00b0)" % incr if incr else ""
        info["warnings"].append(
            "The sector (%g\u00b0\u2013%g\u00b0) is narrower than the profile's azimuth spacing%s and "
            "contains no grid azimuth; the nearest one (%g\u00b0) is used." % (fr, to, spacing, nearest[0]))
    else:
        if incr:
            have = set(azs)
            expected = [a * incr for a in range(int(round(360.0 / incr)))
                        if azimuth_in_sector(a * incr, fr, to)]
            missing = [a for a in expected if a not in have]
            if missing:
                info["warnings"].append(
                    "The horizon profile has no data for azimuth(s) "
                    + ", ".join(f"{a:g}\u00b0" for a in missing[:12])
                    + ("\u2026" if len(missing) > 12 else "") + " inside the sector.")
            off_grid = [x for x in (fr, to) if abs(x / incr - round(x / incr)) > 1e-6]
            if off_grid:
                off_txt = ", ".join("%g\u00b0" % x for x in off_grid)
                info["warnings"].append(
                    "Sector limit(s) %s fall between %g\u00b0 grid azimuths; the contour spans the grid "
                    "azimuths inside the sector only." % (off_txt, incr))

    inside.sort(key=lambda p: (p[0] - fr) % 360.0)
    info.update(filtered=True, n_in=len(inside))
    return inside, info