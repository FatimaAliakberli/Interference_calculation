"""
core/ - calculation and data-access modules for the Link Budget Calculator.

Ported from the original Java Swing desktop tool's Python backend scripts
(previously invoked via subprocess + stdout "key=value" parsing from
UIUtils.runProcess). Every module here is now directly importable and
returns plain Python dicts, while keeping an `if __name__ == "__main__":`
CLI wrapper for backward-compatible command-line use.

Modules:
    calculations.py            AP7 Annex 3 (Eq. 84-97): off-axis angle & horizon antenna gain
    Pr_calculations.py         Rec. ITU-R S.1430 Eq. 3: threshold interference level Pr(p)
    propagation_calculator.py  AP7 main body S4 + Annex 1/2: coordination distance (mode 1 & 2)
    radio_climatic_zone.py     Automated radio-climatic zone / distance-to-coast lookup
    horizon_core.py            Horizon-profile calculation primitives (DEM-backed)
    build_horizon_grid.py      Batch precompute a horizon-profile grid over a bounding box
    query_horizon.py           Nearest-neighbour lookup against a precomputed horizon grid
    tables_data.py             Loader/lookup helpers over Tables 7a-9b (Gx, Pr(p), ...)
"""
