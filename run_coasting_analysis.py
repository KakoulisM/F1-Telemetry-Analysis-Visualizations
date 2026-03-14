"""
run_coasting_analysis.py
──────────────────────────────────────────────────────────────────────────────
Runs lift-and-coast and super-clipping detection for every session and every
driver found in telemetry_out/, processing ALL flying laps per driver.

Output layout
─────────────
  coasting_zones/
    {year}/{event}/{session_type}/{driver}_zones.csv
    all_zones.csv               ← master, all years / sessions / drivers / laps

  super_clipping_zones/
    {year}/{event}/{session_type}/{driver}_sc_zones.csv
    all_super_clipping.csv      ← master super-clipping zones

Each CSV row is one zone with columns:
  year, event, session_type, driver, lap_number,
  start_dist, end_dist, zone_length,
  entry_speed, exit_speed, speed_loss,
  start_time_s, end_time_s, duration_s,
  nearest_corner, dist_to_corner

Usage
─────
  # Process everything new (skips drivers whose output files already exist)
  python run_coasting_analysis.py

  # Force re-process a specific session
  python run_coasting_analysis.py --session Chinese_Grand_Prix/Sprint_Qualifying

  # Force re-process everything
  python run_coasting_analysis.py --force

  # Read results later
  import pandas as pd
  zones = pd.read_csv("coasting_zones/all_zones.csv")
  sc    = pd.read_csv("super_clipping_zones/all_super_clipping.csv")
  lec_q = zones[(zones.driver == "LEC") & (zones.session_type == "Qualifying")]
"""

import argparse
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from coasting_analysis import (
    detect_lift_coast_zones,
    detect_super_clipping_zones,
    extract_all_laps,
    extract_best_lap,
    tag_nearest_corner,
)

TELEMETRY_ROOT = Path("telemetry_out")
OUT_ROOT       = Path("coasting_zones")
MASTER_CSV     = OUT_ROOT / "all_zones.csv"
SC_OUT_ROOT    = Path("super_clipping_zones")
SC_MASTER_CSV  = SC_OUT_ROOT / "all_super_clipping.csv"

# Shared column schema for empty-marker CSVs
_ZONE_COLS = [
    "year", "event", "session_type", "driver", "lap_number",
    "start_dist", "end_dist", "zone_length",
    "entry_speed", "exit_speed", "speed_loss",
    "start_time_s", "end_time_s", "duration_s",
    "nearest_corner", "dist_to_corner",
]

# Regex to extract driver code from filename:
#   {Event}_{Session}_{DRIVER}_telemetry.csv
_DRIVER_RE = re.compile(r"_([A-Z]{2,4})_telemetry\.csv$")


def _parse_driver(filename: str) -> str | None:
    m = _DRIVER_RE.search(filename)
    return m.group(1) if m else None


def _discover_sessions(root: Path) -> list[tuple[str, str, str, Path]]:
    """Return list of (year, event, session_type, session_dir)."""
    sessions = []
    for year_dir in sorted(root.iterdir()):
        if not year_dir.is_dir() or not year_dir.name.isdigit():
            continue
        year = year_dir.name
        for event_dir in sorted(year_dir.iterdir()):
            if not event_dir.is_dir():
                continue
            event = event_dir.name
            for session_dir in sorted(event_dir.iterdir()):
                if not session_dir.is_dir():
                    continue
                telemetry_files = list(session_dir.glob("*_telemetry.csv"))
                if telemetry_files:
                    sessions.append((year, event, session_dir.name, session_dir))
    return sessions


def process_driver(
    tfile: Path,
    driver: str,
    session_type: str,
    corners: pd.DataFrame | None,
    lap_dist_max_override: float | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Load telemetry, segment into all flying laps, run both detectors.

    Returns
    -------
    tuple[pd.DataFrame, pd.DataFrame]
        (lift_and_coast_zones, super_clipping_zones)
        Each row includes a ``lap_number`` column.
    """
    try:
        df = pd.read_csv(tfile)
        if "SessionTime" in df.columns:
            df["SessionTime"] = pd.to_timedelta(df["SessionTime"], errors="coerce")

        # Segment into individual laps; fall back to single best lap
        laps = extract_all_laps(df, session_type=session_type)
        if not laps:
            best = extract_best_lap(df, session_type=session_type)
            laps = [(1, best)]

        coast_parts: list[pd.DataFrame] = []
        sc_parts:    list[pd.DataFrame] = []

        for lap_num, lap in laps:
            lap_dist_max = lap_dist_max_override
            if lap_dist_max is None and "Distance" in lap.columns:
                lap_dist_max = float(pd.to_numeric(lap["Distance"], errors="coerce").max())

            # ── Lift-and-coast
            zones = detect_lift_coast_zones(lap)
            if not zones.empty:
                if corners is not None:
                    zones = tag_nearest_corner(zones, corners, lap_dist_max=lap_dist_max)
                zones.insert(0, "lap_number", lap_num)
                coast_parts.append(zones)

            # ── Super clipping
            sc_zones = detect_super_clipping_zones(lap)
            if not sc_zones.empty:
                if corners is not None:
                    sc_zones = tag_nearest_corner(sc_zones, corners, lap_dist_max=lap_dist_max)
                sc_zones.insert(0, "lap_number", lap_num)
                sc_parts.append(sc_zones)

        coast_df = pd.concat(coast_parts, ignore_index=True) if coast_parts else pd.DataFrame()
        sc_df    = pd.concat(sc_parts,    ignore_index=True) if sc_parts    else pd.DataFrame()
        return coast_df, sc_df

    except Exception as exc:
        print(f"    ERROR processing {driver}: {exc}", file=sys.stderr)
        return pd.DataFrame(), pd.DataFrame()


def run(
    filter_session: str | None = None,
    force: bool = False,
) -> None:
    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    SC_OUT_ROOT.mkdir(parents=True, exist_ok=True)

    sessions = _discover_sessions(TELEMETRY_ROOT)
    if not sessions:
        print("No telemetry data found under", TELEMETRY_ROOT)
        return

    # Filter to a specific session path if requested
    if filter_session:
        norm = filter_session.replace("\\", "/").strip("/")
        sessions = [
            s for s in sessions
            if norm in f"{s[1]}/{s[2]}"
        ]
        if not sessions:
            print(f"No sessions matched filter '{filter_session}'")
            return

    all_rows:    list[pd.DataFrame] = []
    sc_all_rows: list[pd.DataFrame] = []

    # Load existing master files so we don't lose previously stored rows
    if MASTER_CSV.exists() and not force:
        try:
            existing = pd.read_csv(MASTER_CSV)
            # Migrate older files that predate the lap_number column
            if "lap_number" not in existing.columns:
                existing.insert(4, "lap_number", 1)
            all_rows.append(existing)
            print(f"Loaded {len(existing):,} existing rows from {MASTER_CSV}")
        except Exception:
            pass

    if SC_MASTER_CSV.exists() and not force:
        try:
            sc_existing = pd.read_csv(SC_MASTER_CSV)
            sc_all_rows.append(sc_existing)
            print(f"Loaded {len(sc_existing):,} existing rows from {SC_MASTER_CSV}")
        except Exception:
            pass

    total_sessions = len(sessions)
    for s_idx, (year, event, session_type, session_dir) in enumerate(sessions, 1):
        print(f"\n[{s_idx}/{total_sessions}] {year} / {event} / {session_type}")

        # Skip Practice 1 — installation laps only, no representative flying laps
        if session_type.upper().replace(" ", "_") in ("PRACTICE_1", "FP1"):
            print("  (skipped — FP1 excluded from coasting analysis)")
            continue

        out_dir    = OUT_ROOT    / year / event / session_type
        sc_out_dir = SC_OUT_ROOT / year / event / session_type
        out_dir.mkdir(parents=True, exist_ok=True)
        sc_out_dir.mkdir(parents=True, exist_ok=True)

        # Load corners if available in this session dir
        corners_path = session_dir / "circuit_corners.csv"
        corners = None
        if corners_path.exists():
            try:
                corners = pd.read_csv(corners_path)
            except Exception:
                pass

        telemetry_files = sorted(session_dir.glob("*_telemetry.csv"))
        session_rows:    list[pd.DataFrame] = []
        sc_session_rows: list[pd.DataFrame] = []

        for tfile in telemetry_files:
            driver = _parse_driver(tfile.name)
            if not driver:
                continue

            per_driver_out    = out_dir    / f"{driver}_zones.csv"
            sc_per_driver_out = sc_out_dir / f"{driver}_sc_zones.csv"
            coast_cached = per_driver_out.exists() and not force
            sc_cached    = sc_per_driver_out.exists() and not force

            # Read from on-disk cache when available
            if coast_cached:
                try:
                    cached = pd.read_csv(per_driver_out)
                    if not cached.empty:
                        if "lap_number" not in cached.columns:
                            cached.insert(4, "lap_number", 1)
                        session_rows.append(cached)
                except Exception:
                    pass

            if sc_cached:
                try:
                    sc_cached_df = pd.read_csv(sc_per_driver_out)
                    if not sc_cached_df.empty:
                        sc_session_rows.append(sc_cached_df)
                except Exception:
                    pass

            if coast_cached and sc_cached:
                print(f"  {driver:>4}  (cached)")
                continue

            # (Re)process — at least one output file is missing
            coast_zones, sc_zones = process_driver(tfile, driver, session_type, corners)
            n_coast = len(coast_zones)
            n_sc    = len(sc_zones)

            # ── Write lift-and-coast output
            if not coast_cached:
                if coast_zones.empty:
                    pd.DataFrame(columns=_ZONE_COLS).to_csv(per_driver_out, index=False)
                else:
                    coast_zones.insert(0, "driver",       driver)
                    coast_zones.insert(0, "session_type", session_type)
                    coast_zones.insert(0, "event",        event)
                    coast_zones.insert(0, "year",         int(year))
                    coast_zones.to_csv(per_driver_out, index=False)
                    session_rows.append(coast_zones)

            # ── Write super-clipping output
            if not sc_cached:
                if sc_zones.empty:
                    pd.DataFrame(columns=_ZONE_COLS).to_csv(sc_per_driver_out, index=False)
                else:
                    sc_zones.insert(0, "driver",       driver)
                    sc_zones.insert(0, "session_type", session_type)
                    sc_zones.insert(0, "event",        event)
                    sc_zones.insert(0, "year",         int(year))
                    sc_zones.to_csv(sc_per_driver_out, index=False)
                    sc_session_rows.append(sc_zones)

            laps_str = f"{n_coast} coast  |  {n_sc} SC"
            print(f"  {driver:>4}  {laps_str}")

        all_rows.extend(session_rows)
        sc_all_rows.extend(sc_session_rows)

    # ── Rebuild master files
    def _rebuild_master(rows: list, master_path: Path, label: str) -> None:
        if not rows:
            print(f"\n{label}: no zones to write.")
            return
        master = pd.concat(rows, ignore_index=True)
        dedup_cols = [c for c in
                      ["year", "event", "session_type", "driver", "lap_number", "start_dist"]
                      if c in master.columns]
        if dedup_cols:
            master = master.drop_duplicates(subset=dedup_cols, keep="last")
        sort_cols = [c for c in
                     ["year", "event", "session_type", "driver", "lap_number", "start_dist"]
                     if c in master.columns]
        master = master.sort_values(sort_cols, ignore_index=True)
        master.to_csv(master_path, index=False)
        print(f"\n{label} master: {master_path}  ({len(master):,} total zones)")

    _rebuild_master(all_rows,    MASTER_CSV,    "Lift-and-coast")
    _rebuild_master(sc_all_rows, SC_MASTER_CSV, "Super-clipping")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Run coasting analysis for all sessions/drivers"
    )
    parser.add_argument(
        "--session",
        default=None,
        help="Process only a session matching this path fragment, "
             "e.g. 'Chinese_Grand_Prix/Sprint_Qualifying'",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-process and overwrite existing per-driver output files",
    )
    args = parser.parse_args()
    run(filter_session=args.session, force=args.force)
