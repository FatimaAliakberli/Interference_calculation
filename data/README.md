# data/

This folder is for **derived / app-owned data** — anything the app reads
or writes as part of normal operation, as opposed to `data_sources/`
(raw, third-party reference files the user has to go download).

Nothing in this repo writes here yet (backend-only phase). Expected future
uses, once the Streamlit pages exist:

- Cached/exported lookup results (e.g. a CSV export of a `tables_data.py`
  query) for the user to download.
- Session presets / saved link-budget scenarios, if that gets added.
- Any small app-generated config that shouldn't live under `data_sources/`
  (which is meant to stay a clean drop-in target for the raw reference
  files described in `DATA_SETUP.md`).

Kept empty (via `.gitkeep`) for now so the folder exists in version control.
