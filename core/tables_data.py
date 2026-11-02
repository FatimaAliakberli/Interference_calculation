#!/usr/bin/env python3
"""
Data-loading module for ITU_Coordination_Distance_Parameters.xlsx.

That workbook is a transcription of RR Appendix 7 Tables 7a-7c, 8a-8d and
9a-9b (coordination-distance link-budget parameters), organised as three
sheets:

    "Transmitting Earth Station"   -> Tables 7a, 7b, 7c
    "Receiving Earth Station"      -> Tables 8a, 8b, 8c, 8d
    "Bidirectional Transmitting ES"-> Tables 9a, 9b

All three sheets share the same 26 columns (see the workbook's README sheet
for the full column glossary). This module replaces the small hand-picked
example rows previously hardcoded in VictimDialog.java's buildLookupData()
with a pandas-backed loader over the FULL digitized dataset, plus a set of
lookup helpers (get_gx, get_pr_p, lookup_entries, ...) that filter it.

Values are returned as plain dicts (JSON/Streamlit friendly), not DataFrame
rows, so callers don't need a pandas dependency mindset to use them.

Usage:
    from core.tables_data import lookup_entries, get_gx

    rows = lookup_entries("Transmitting Earth Station", frequency_ghz=1.6)
    gx_values = get_gx("Transmitting Earth Station", frequency_ghz=1.6)

This module has no `__main__` CLI of its own (it is a pure data-access
layer for the other core/ modules and the Streamlit app), but every public
function is a plain importable function per the project's function-based
refactor convention.
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Optional

import pandas as pd

# ---------------------------------------------------------------------------
# Paths / sheet names
# ---------------------------------------------------------------------------

# core/tables_data.py -> core/ -> link_budget_app/ -> data_sources/
DATA_SOURCES_DIR = Path(__file__).resolve().parent.parent / "data_sources"
DEFAULT_XLSX_PATH = DATA_SOURCES_DIR / "ITU_Coordination_Distance_Parameters.xlsx"

SHEET_TABLE_7 = "Transmitting Earth Station"     # Tables 7a-7c
SHEET_TABLE_8 = "Receiving Earth Station"        # Tables 8a-8d
SHEET_TABLE_9 = "Bidirectional Transmitting ES"  # Tables 9a-9b

ALL_SHEETS = (SHEET_TABLE_7, SHEET_TABLE_8, SHEET_TABLE_9)

# Short aliases so callers can say "7", "8", "9" instead of the full sheet name.
_SHEET_ALIASES = {
    "7": SHEET_TABLE_7, "table 7": SHEET_TABLE_7, "tx": SHEET_TABLE_7,
    "8": SHEET_TABLE_8, "table 8": SHEET_TABLE_8, "rx": SHEET_TABLE_8,
    "9": SHEET_TABLE_9, "table 9": SHEET_TABLE_9, "bidirectional": SHEET_TABLE_9,
}


def resolve_sheet_name(sheet_name: str) -> str:
    """Accepts either the exact sheet name or a short alias ('7', 'table 8',
    'rx', ...) and returns the exact sheet name. Raises ValueError if it
    doesn't match anything."""
    if sheet_name in ALL_SHEETS:
        return sheet_name
    key = sheet_name.strip().lower()
    if key in _SHEET_ALIASES:
        return _SHEET_ALIASES[key]
    raise ValueError(
        f"Unknown sheet/table {sheet_name!r}. Expected one of {ALL_SHEETS} "
        f"or an alias like '7', '8', '9', 'tx', 'rx', 'bidirectional'."
    )


# ---------------------------------------------------------------------------
# Workbook loading (cached so the xlsx is only read from disk once)
# ---------------------------------------------------------------------------

@lru_cache(maxsize=4)
def _load_workbook(xlsx_path: Optional[str] = None) -> dict:
    """Reads all three sheets into a {sheet_name: DataFrame} dict. Cached by
    path so repeated lookups (e.g. from Streamlit reruns) don't re-parse the
    xlsx every time."""
    path = Path(xlsx_path) if xlsx_path else DEFAULT_XLSX_PATH
    if not path.exists():
        raise FileNotFoundError(
            f"ITU coordination-distance parameter workbook not found at {path}. "
            f"See DATA_SETUP.md."
        )
    sheets = pd.read_excel(path, sheet_name=list(ALL_SHEETS), engine="openpyxl")
    return sheets


def clear_cache() -> None:
    """Drop the cached workbook (e.g. after the user replaces the xlsx file
    on disk mid-session)."""
    _load_workbook.cache_clear()


def load_table(sheet_name: str, xlsx_path: Optional[str] = None) -> pd.DataFrame:
    """Returns a full copy of one sheet as a DataFrame, for callers that
    want to work with pandas directly (e.g. a future Streamlit data
    explorer page)."""
    sheet_name = resolve_sheet_name(sheet_name)
    sheets = _load_workbook(xlsx_path)
    return sheets[sheet_name].copy()


# ---------------------------------------------------------------------------
# Frequency-band parsing
# ---------------------------------------------------------------------------

# Matches tokens like "148.0-149.9 MHz", "0.272-0.273 GHz", "401-403 MHz".
# The source "Frequency Band" column sometimes lists several comma-separated
# ranges in one cell (e.g. "2025-2110 MHz, 2110-2120 MHz (Deep space)"), so
# this is applied with re.finditer to pull out every range in the cell.
_BAND_RE = re.compile(
    r"(?P<lo>\d+(?:\.\d+)?)\s*-\s*(?P<hi>\d+(?:\.\d+)?)\s*(?P<unit>MHz|GHz)",
    re.IGNORECASE,
)

# Several numeric-looking columns in the source workbook aren't plain
# numbers: reference bandwidths are written in engineering notation
# ("4x10^3", "10^6", "150x10^3"), and several Gx/Te cells carry an inline
# footnote reference ("49 (Note 2)", "500 (Note 2)"). A blank/not-applicable
# cell is written as a literal "-". This regex handles the "<mantissa>x10^
# <exp>" and bare "10^<exp>" forms (after footnote text has been stripped).
_ENG_NUM_RE = re.compile(
    r"^(?:(?P<mantissa>\d+(?:\.\d+)?)\s*[x\u00d7]\s*)?10\s*\^\s*(?P<exp>-?\d+)$",
    re.IGNORECASE,
)


def parse_engineering_number(val):
    """Parses one of this workbook's numeric-ish cell values into a float,
    or returns None if it's blank/'-'/genuinely unparseable.

    Handles:
      * plain numbers (already numeric, or a numeric string)
      * engineering notation: "4x10^3" -> 4000.0, "10^6" -> 1e6,
        "150x10^3" -> 150000.0
      * inline footnotes: "49 (Note 2)" -> 49.0, "500 (Note 2)" -> 500.0
      * not-applicable markers: "-" -> None

    This is applied to Gx/Gm/Gr (dBi), Te (K), B (Hz), and Pr(p) (dBW) by
    get_gx()/get_pr_p()/get_victim_parameters() below so callers (e.g.
    app.py's Step 3 victim-parameter dialog) get clean floats/None instead
    of having to separately handle each of these string formats themselves.
    """
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return None if pd.isna(val) else float(val)
    s = str(val).strip()
    if not s or s == "-":
        return None
    # Strip inline footnote references, e.g. "49 (Note 2)" -> "49"
    s = re.sub(r"\(.*?\)", "", s).strip()
    if not s or s == "-":
        return None
    m = _ENG_NUM_RE.match(s)
    if m:
        mantissa = float(m.group("mantissa")) if m.group("mantissa") else 1.0
        return mantissa * (10.0 ** int(m.group("exp")))
    try:
        return float(s)
    except ValueError:
        return None


def parse_frequency_ranges_ghz(freq_band_str) -> list[tuple[float, float]]:
    """Parses a "Frequency Band" cell into a list of (lo_ghz, hi_ghz)
    tuples. A cell may contain multiple ranges (comma-separated); every one
    found is returned. Returns an empty list if the cell is blank/unparseable
    (this happens for a handful of rows where the source table's "Notes"
    column is used instead, e.g. "No numeric parameters listed in source
    table")."""
    if freq_band_str is None or (isinstance(freq_band_str, float) and pd.isna(freq_band_str)):
        return []
    ranges = []
    for m in _BAND_RE.finditer(str(freq_band_str)):
        lo, hi, unit = float(m.group("lo")), float(m.group("hi")), m.group("unit").upper()
        if unit == "MHZ":
            lo, hi = lo / 1000.0, hi / 1000.0
        ranges.append((lo, hi))
    return ranges


def _frequency_matches(freq_band_str, frequency_ghz: float) -> bool:
    for lo, hi in parse_frequency_ranges_ghz(freq_band_str):
        if lo <= frequency_ghz <= hi:
            return True
    return False


# ---------------------------------------------------------------------------
# Generic filtered lookup
# ---------------------------------------------------------------------------

def _row_to_dict(row: pd.Series) -> dict:
    d = row.to_dict()
    # Convert NaN -> None for clean JSON/Streamlit consumption.
    for k, v in list(d.items()):
        if isinstance(v, float) and pd.isna(v):
            d[k] = None
    return d


def lookup_entries(
    sheet_name: str,
    frequency_ghz: Optional[float] = None,
    table_id: Optional[str] = None,
    service_contains: Optional[str] = None,
    terrestrial_service_contains: Optional[str] = None,
    modulation: Optional[str] = None,
    orbit: Optional[str] = None,
    xlsx_path: Optional[str] = None,
) -> list[dict]:
    """General-purpose filtered lookup over one sheet. Every filter argument
    is optional and AND-combined; omit a filter to not constrain on it.

        sheet_name                  "7"/"8"/"9", an alias, or the exact sheet name
        frequency_ghz                matches rows whose parsed frequency band contains this value
        table_id                     exact match on the 'Table' column (e.g. "7a", "9b"), case-insensitive
        service_contains             case-insensitive substring match on 'Space Service Designation'
        terrestrial_service_contains case-insensitive substring match on 'Terrestrial Service Designation'
        modulation                   exact match on 'Modulation' ("A", "N", "A and N", ...)
        orbit                        exact match on 'Orbit' ("GSO", "Non-GSO")

    Returns a list of row dicts (NaN cells become None).
    """
    df = load_table(sheet_name, xlsx_path)

    if frequency_ghz is not None:
        mask = df["Frequency Band"].apply(lambda s: _frequency_matches(s, frequency_ghz))
        df = df[mask]

    if table_id is not None:
        df = df[df["Table"].astype(str).str.lower() == table_id.strip().lower()]

    if service_contains is not None:
        needle = service_contains.strip().lower()
        df = df[df["Space Service Designation"].astype(str).str.lower().str.contains(needle, na=False)]

    if terrestrial_service_contains is not None:
        needle = terrestrial_service_contains.strip().lower()
        df = df[df["Terrestrial Service Designation"].astype(str).str.lower().str.contains(needle, na=False)]

    if modulation is not None:
        df = df[df["Modulation"].astype(str).str.lower() == modulation.strip().lower()]

    if orbit is not None:
        df = df[df["Orbit"].astype(str).str.lower() == orbit.strip().lower()]

    return [_row_to_dict(row) for _, row in df.iterrows()]


def list_bands(sheet_name: str, xlsx_path: Optional[str] = None) -> list[str]:
    """Distinct, source-order 'Frequency Band' strings for a sheet - handy
    for populating a Streamlit selectbox."""
    df = load_table(sheet_name, xlsx_path)
    seen = []
    for v in df["Frequency Band"]:
        if isinstance(v, str) and v not in seen:
            seen.append(v)
    return seen


def get_all_distinct_bands(sheet_name: str, xlsx_path: Optional[str] = None) -> list[str]:
    """Returns every unique 'Frequency Band' string present in a sheet,
    sorted by the band's parsed lowest frequency (GHz) so the list reads in
    ascending frequency order for a selectbox - not alphabetically, which
    would scatter '2025-2110 MHz' away from '0.272-0.273 GHz'. Bands whose
    cell text couldn't be parsed into a numeric range (e.g. rows where the
    'Notes' column is used instead) are sorted to the end, in their
    original source-sheet order among themselves.
    """
    df = load_table(sheet_name, xlsx_path)
    seen = []
    for v in df["Frequency Band"]:
        if isinstance(v, str) and v not in seen:
            seen.append(v)

    def _sort_key(band_str):
        ranges = parse_frequency_ranges_ghz(band_str)
        return ranges[0][0] if ranges else float("inf")

    return sorted(seen, key=_sort_key)


def get_rows_by_band(sheet_name: str, frequency_band_str: str,
                      xlsx_path: Optional[str] = None) -> list[dict]:
    """Returns every row in a sheet whose 'Frequency Band' column is an
    exact (case-sensitive) match for `frequency_band_str` - the counterpart
    to get_all_distinct_bands(), for when the user has already picked one
    of the exact band strings that function returned (e.g. from a
    Streamlit selectbox) rather than an arbitrary frequency value.
    """
    df = load_table(sheet_name, xlsx_path)
    matched = df[df["Frequency Band"].astype(str) == frequency_band_str]
    return [_row_to_dict(row) for _, row in matched.iterrows()]


def list_services(sheet_name: str, frequency_ghz: Optional[float] = None,
                   xlsx_path: Optional[str] = None) -> list[str]:
    """Distinct 'Space Service Designation' values, optionally narrowed to
    rows matching a given frequency - handy for a dependent Streamlit
    selectbox (pick a band, then pick a service within that band)."""
    entries = lookup_entries(sheet_name, frequency_ghz=frequency_ghz, xlsx_path=xlsx_path)
    seen = []
    for e in entries:
        v = e.get("Space Service Designation")
        if v and v not in seen:
            seen.append(v)
    return seen


# ---------------------------------------------------------------------------
# Narrow convenience lookups for the specific values the Java UI needed
# (Gx for GainWorkflowDialog/PropagationParamsDialog, Pr(p) for VictimDialog)
# ---------------------------------------------------------------------------

def get_gx(sheet_name: str, frequency_ghz: float, **filters) -> list[dict]:
    """Returns matching rows' Gx (dBi) values plus enough context (Table,
    Frequency Band, Space/Terrestrial Service, Modulation, Orbit) to
    disambiguate when a frequency band maps to more than one row. Any of
    lookup_entries()'s optional filter kwargs (table_id, service_contains,
    modulation, orbit, ...) can be passed through to narrow the match.

    Rows where Gx is blank in the source table ('-' or empty cell) are
    dropped, since there is nothing to return.
    """
    entries = lookup_entries(sheet_name, frequency_ghz=frequency_ghz, **filters)
    keep_cols = ["Table", "Frequency Band", "Space Service Designation",
                 "Terrestrial Service Designation", "Modulation", "Orbit", "Gx (dBi)", "Notes"]
    out = []
    for e in entries:
        gx = parse_engineering_number(e.get("Gx (dBi)"))
        if gx is None:  # blank/'-'/unparseable in the source table
            continue
        row = {k: e.get(k) for k in keep_cols}
        row["Gx (dBi)"] = gx
        out.append(row)
    return out


def get_pr_p(sheet_name: str, frequency_ghz: float, **filters) -> list[dict]:
    """Same idea as get_gx(), but for the permissible interference power
    Pr(p) (dBW) column, plus the reference bandwidth B (Hz) it's defined in."""
    entries = lookup_entries(sheet_name, frequency_ghz=frequency_ghz, **filters)
    keep_cols = ["Table", "Frequency Band", "Space Service Designation",
                 "Terrestrial Service Designation", "Modulation", "Orbit",
                 "B (Hz)", "Pr(p) (dBW)", "Notes"]
    out = []
    for e in entries:
        pr = parse_engineering_number(e.get("Pr(p) (dBW)"))
        if pr is None:  # blank/'-'/unparseable in the source table
            continue
        row = {k: e.get(k) for k in keep_cols}
        row["Pr(p) (dBW)"] = pr
        row["B (Hz)"] = parse_engineering_number(e.get("B (Hz)"))
        out.append(row)
    return out


def get_victim_parameters(sheet_name: str, frequency_ghz: float, **filters) -> list[dict]:
    """Convenience combining get_gx()-style Gx (or Gm/Gr for Table 9) with
    Pr(p) in one row set, matching what VictimDialog.java's auto-lookup
    (Choice 1) used to hand back as (Gr, Pr(p)). For Table 9 rows, 'Gm'
    (on-axis) and 'Gr' (horizon) are both included since which one is
    "the" horizon gain depends on the Annex 5 off-axis procedure, not on
    this lookup alone - the caller decides which column applies.
    """
    entries = lookup_entries(sheet_name, frequency_ghz=frequency_ghz, **filters)
    keep_cols = ["Table", "Frequency Band", "Space Service Designation",
                 "Terrestrial Service Designation", "Modulation", "Orbit",
                 "Gx (dBi)", "Gm (dBi)", "Gr (dBi)", "Emin (deg)",
                 "Te (K)", "B (Hz)", "Pr(p) (dBW)", "Notes"]
    numeric_cols = ("Gx (dBi)", "Gm (dBi)", "Gr (dBi)", "Emin (deg)",
                     "Te (K)", "B (Hz)", "Pr(p) (dBW)")
    out = []
    for e in entries:
        row = {k: e.get(k) for k in keep_cols}
        for k in numeric_cols:
            row[k] = parse_engineering_number(row.get(k))
        out.append(row)
    return out