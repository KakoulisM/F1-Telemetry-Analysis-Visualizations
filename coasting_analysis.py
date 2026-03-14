"""
coasting_analysis.py
──────────────────────────────────────────────────────────────────────────────
Detects lift-and-coast zones in F1 telemetry: stretches where the driver
releases the throttle and does NOT press the brake — slowing the car through
engine braking and aerodynamic drag alone, usually at the turn-in point.

Telemetry column expectations (OpenF1 / pipeline output):
  Speed      int   km/h  (0–340)
  Throttle   int   %     (0–104)
  Brake      int   %     (0–104; in practice 0 / 100 / 104)
  Distance   float metres from lap start
  SessionTime       timedelta or numeric seconds

Usage
──────────────────────────────────────────────────────────────────────────────
  from coasting_analysis import detect_lift_coast_zones, tag_nearest_corner

  zones = detect_lift_coast_zones(lap_df)
  zones = tag_nearest_corner(zones, corners_df)   # optional
  print(zones)

Standalone (runs on LEC Chinese GP SQ data as a demo):
  python coasting_analysis.py
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from pathlib import Path

# ── Thresholds ────────────────────────────────────────────────────────────────
THROTTLE_LIFTED  = 5      # % — anything at or below this counts as "lifted"
MIN_SPEED_LOSS   = 10     # km/h — zone must shed at least this much speed
MIN_DISTANCE_M   = 15     # metres — zone must span at least this far
MAX_DISTANCE_GAP = 60     # metres — gap to merge two nearby segments

# Rolling window lengths per session type (seconds).
# Slightly longer than the longest expected lap time so the window always
# captures a complete flying lap.
_WINDOW_BY_SESSION: dict[str, float] = {
    "practice_1":        110.0,
    "practice_2":        110.0,
    "practice_3":        110.0,
    "qualifying":        105.0,
    "sprint_qualifying": 100.0,
    "sprint":            105.0,
    "race":              115.0,
}
_DEFAULT_WINDOW = 110.0


# ── Lap extraction ────────────────────────────────────────────────────────────

def extract_best_lap(
    df: pd.DataFrame,
    session_type: str = "",
    window_s: float | None = None,
) -> pd.DataFrame:
    """Extract the best flying lap from full-session telemetry.

    Works for every session type (Practice, Qualifying, Sprint Qualifying,
    Race) without needing FastF1 lap boundaries.

    Strategy
    --------
    1. Parse ``SessionTime`` to seconds.
    2. Identify the first long zero-speed run (> 15 s) — this marks the end
       of the out-lap / formation lap.  Scanning starts just after it.
    3. Within the remaining data find the rolling ``window_s``-second window
       with the highest average speed.  That window is the best flying lap.
    4. Return the slice with ``Distance`` reset to zero from the lap start
       (recomputed from X/Y when available, otherwise offset).

    Falls back to the full ``df`` on any error so callers never crash.

    Parameters
    ----------
    df : pd.DataFrame
        Full session telemetry for a single driver.  Must contain
        ``Speed`` and a time column (``SessionTime``, ``Time``, or
        ``Timestamp``).
    session_type : str
        Lowercase session name, e.g. ``"qualifying"``, ``"race"``.
        Used only to select the default rolling-window width when
        ``window_s`` is *None*.
    window_s : float, optional
        Rolling-window width in seconds.  Overrides the session-type
        default when provided.

    Returns
    -------
    pd.DataFrame
        Single-lap slice with ``Distance`` reset to zero, or the full
        ``df`` as a fallback.
    """
    try:
        if "Speed" not in df.columns:
            return df

        # Resolve time column to seconds array
        st_col = next(
            (c for c in df.columns if c.lower() in ("sessiontime", "time", "timestamp")),
            None,
        )
        if st_col is None:
            return df

        raw = df[st_col]
        if pd.api.types.is_timedelta64_dtype(raw.dtype):
            t_sec = raw.dt.total_seconds().values
        else:
            td = pd.to_timedelta(raw, errors="coerce")
            if not td.isna().all():
                t_sec = td.dt.total_seconds().values
            else:
                t_sec = pd.to_numeric(raw, errors="coerce").values

        speeds = df["Speed"].values.astype(float)
        if len(speeds) < 20:
            return df

        # Determine window width
        _st_key = session_type.lower().replace(" ", "_")
        win = window_s if window_s is not None else _WINDOW_BY_SESSION.get(_st_key, _DEFAULT_WINDOW)

        # Find all zero-speed runs > 15 s
        zero_mask = pd.Series(speeds < 5)
        run_id = (zero_mask != zero_mask.shift()).cumsum()
        t_series = pd.Series(t_sec)
        zero_runs = t_series[zero_mask].groupby(run_id[zero_mask]).agg(["min", "max"])
        zero_runs["dur"] = zero_runs["max"] - zero_runs["min"]
        long_gaps = zero_runs[zero_runs["dur"] >= 15.0]

        # Start scanning after the first long zero-speed gap
        search_start = long_gaps.iloc[0]["max"] if not long_gaps.empty else t_sec[0]
        mask = t_sec >= search_start
        s_t   = t_sec[mask]
        s_spd = speeds[mask]

        if len(s_t) < 5 or float(np.nanmax(s_spd)) < 80:
            return df

        # Rolling window scan
        best_mean  = 0.0
        best_start = s_t[0]
        best_end   = s_t[0] + win

        for t in s_t:
            seg_mask = (s_t >= t) & (s_t <= t + win)
            seg = s_spd[seg_mask]
            if len(seg) < 5:
                continue
            m = float(np.nanmean(seg))
            if m > best_mean:
                best_mean  = m
                best_start = t
                best_end   = t + win

        lap_df = df[(t_series >= best_start) & (t_series <= best_end)].copy()
        if lap_df.empty:
            return df

        lap_df = lap_df.reset_index(drop=True)

        # Reset Distance to zero from lap start
        if "X" in lap_df.columns and "Y" in lap_df.columns:
            lap_df["Distance"] = (
                np.sqrt(lap_df["X"].diff() ** 2 + lap_df["Y"].diff() ** 2)
                .fillna(0)
                .cumsum()
            )
        elif "Distance" in lap_df.columns:
            lap_df["Distance"] = pd.to_numeric(lap_df["Distance"], errors="coerce")
            first_d = lap_df["Distance"].iloc[0]
            if pd.notna(first_d):
                lap_df["Distance"] = lap_df["Distance"] - first_d

        return lap_df

    except Exception:
        return df


# ── T1-crossing lap extraction ───────────────────────────────────────────────

def extract_t1_lap(
    df: pd.DataFrame,
    t1_x: float,
    t1_y: float,
    near_thresh: float = 350.0,
    gap_rows: int = 40,
    tight_thresh: float = 200.0,
    min_lap_s: float = 75.0,
    max_lap_s: float = 140.0,
) -> pd.DataFrame:
    """Return the fastest clean T1→T1 flying lap from full-session telemetry.

    Scans the raw ``X``/``Y`` positions to find every passage through the
    start/finish line (T1) and extracts the segment between the two closest
    consecutive clean crossings that looks like a single flying lap.

    Parameters
    ----------
    df : pd.DataFrame
        Full-session telemetry.  Must contain ``X``, ``Y``, ``Speed`` and a
        time column (``SessionTime``/``Time``).
    t1_x, t1_y : float
        XY coordinates of T1 in the same space as the telemetry data.
    near_thresh : float
        Rows within this distance of T1 are considered "near T1".
    gap_rows : int
        Consecutive near-T1 rows separated by more than this many rows are
        treated as separate crossing events.
    tight_thresh : float
        A crossing event must include at least one row within this distance
        to count as a proper line crossing (filters out near-misses on
        parallel straights).
    min_lap_s / max_lap_s : float
        Duration bounds for a single flying lap (seconds).

    Returns
    -------
    pd.DataFrame
        The fastest qualifying T1→T1 lap segment with ``Distance`` reset to
        zero, or an empty DataFrame if no valid lap is found.
    """
    try:
        if "X" not in df.columns or "Y" not in df.columns or "Speed" not in df.columns:
            return pd.DataFrame()

        x = pd.to_numeric(df["X"], errors="coerce").fillna(0).values
        y = pd.to_numeric(df["Y"], errors="coerce").fillna(0).values
        spd = pd.to_numeric(df["Speed"], errors="coerce").fillna(0).values

        # Resolve time column to seconds
        st_col = next(
            (c for c in df.columns if c.lower() in ("sessiontime", "time", "timestamp")),
            None,
        )
        if st_col is None:
            return pd.DataFrame()
        raw = df[st_col]
        if pd.api.types.is_timedelta64_dtype(raw.dtype):
            t_sec = raw.dt.total_seconds().values.astype(float)
        else:
            td = pd.to_timedelta(raw, errors="coerce")
            if not td.isna().all():
                t_sec = td.dt.total_seconds().values.astype(float)
            else:
                t_sec = pd.to_numeric(raw, errors="coerce").values.astype(float)

        d2t1 = np.sqrt((x - t1_x) ** 2 + (y - t1_y) ** 2)
        near = np.where(d2t1 < near_thresh)[0]
        if len(near) == 0:
            return pd.DataFrame()

        # Group consecutive near rows into crossing events
        gaps = np.where(np.diff(near) > gap_rows)[0]
        crossing_groups = np.split(near, gaps + 1)

        # Keep only "tight" crossings (close enough to be genuine S/F line passes)
        tight_crossings: list[int] = []
        for grp in crossing_groups:
            best = grp[int(np.argmin(d2t1[grp]))]
            if d2t1[best] <= tight_thresh:
                tight_crossings.append(int(best))

        if len(tight_crossings) < 2:
            return pd.DataFrame()

        # Evaluate consecutive pairs that fall within flying-lap duration range
        candidates: list[tuple[float, float, pd.DataFrame]] = []  # (arc, mean_spd, seg)

        for i in range(len(tight_crossings) - 1):
            r0, r1 = tight_crossings[i], tight_crossings[i + 1]
            if r1 <= r0:
                continue
            dur = float(t_sec[r1]) - float(t_sec[r0])
            if not (min_lap_s <= dur <= max_lap_s):
                continue
            seg = df.iloc[r0:r1].copy().reset_index(drop=True)
            seg_spd = pd.to_numeric(seg["Speed"], errors="coerce")
            if seg_spd.max() < 80:
                continue
            sx = pd.to_numeric(seg.get("X", pd.Series(dtype=float)), errors="coerce").values.astype(float)
            sy = pd.to_numeric(seg.get("Y", pd.Series(dtype=float)), errors="coerce").values.astype(float)
            arc = float(np.sum(np.sqrt(np.diff(sx) ** 2 + np.diff(sy) ** 2)))
            candidates.append((arc, float(seg_spd.mean()), seg))

        if not candidates:
            return pd.DataFrame()

        # Filter out sub-lap segments: keep only those with dist >= 65% of the
        # longest candidate's distance (eliminates short pit-exit stubs).
        max_arc = max(c[0] for c in candidates)
        full_cands = [c for c in candidates if c[0] >= max_arc * 0.65]
        if not full_cands:
            full_cands = candidates

        # Pick the fastest (highest mean speed) full-length candidate
        best_arc, _, best_seg = max(full_cands, key=lambda c: c[1])
        sx = pd.to_numeric(best_seg["X"], errors="coerce").values.astype(float)
        sy = pd.to_numeric(best_seg["Y"], errors="coerce").values.astype(float)
        best_seg = best_seg.copy()
        best_seg["Distance"] = np.concatenate(
            ([0.0], np.cumsum(np.sqrt(np.diff(sx) ** 2 + np.diff(sy) ** 2)))
        )
        return best_seg

    except Exception:
        return pd.DataFrame()


# ── All-laps extraction ───────────────────────────────────────────────────────

_GAP_SPEED_KMH   = 60.0   # speed threshold for "slow" boundary row
_GAP_MIN_SECS    = 8.0    # slow section must last this long to be a lap delimiter
_LAP_MIN_SECS    = 55.0   # reject segments shorter than this
_LAP_MIN_AVG_KMH = 110.0  # reject segments with mean speed below this


def extract_all_laps(
    df: pd.DataFrame,
    session_type: str = "",
    gap_speed_kmh: float = _GAP_SPEED_KMH,
    gap_min_s: float = _GAP_MIN_SECS,
    lap_min_s: float = _LAP_MIN_SECS,
    lap_min_avg_kmh: float = _LAP_MIN_AVG_KMH,
) -> list[tuple[int, pd.DataFrame]]:
    """Segment full-session telemetry into individual flying laps.

    Works without FastF1 lap boundaries.  Uses sustained slow-speed sections
    (pit entry/exit, formation laps, cool-down laps) as lap delimiters.

    Strategy
    --------
    1. Build a seconds timeline from ``SessionTime`` (or index fallback).
    2. Identify "boundary" sections where speed stays below *gap_speed_kmh*
       for at least *gap_min_s* consecutive seconds.
    3. Extract segments between adjacent boundaries.
    4. Reject segments shorter than *lap_min_s* or with average speed below
       *lap_min_avg_kmh* (filters out formation / out / in-laps).
    5. Reset ``Distance`` to zero from the start of each kept segment.
    6. Returns empty list on total failure — caller should fall back to
       ``extract_best_lap``.

    Parameters
    ----------
    df : pd.DataFrame
        Full session telemetry for a single driver.
    session_type : str
        Reserved for future per-session-type overrides; not used yet.
    gap_speed_kmh : float
        Speed threshold below which a row is "slow" (default 60 km/h).
    gap_min_s : float
        A slow section must last at least this long to count as a lap
        boundary (default 8 s).  Filters out brief hairpin exits.
    lap_min_s : float
        Minimum lap duration accepted (default 55 s).  Rejects install laps.
    lap_min_avg_kmh : float
        Minimum average speed for a segment to be kept (default 110 km/h).

    Returns
    -------
    list[tuple[int, pd.DataFrame]]
        ``(lap_number, lap_df)`` pairs, lap numbers starting at 1.
        Empty list on total failure — caller should fall back to
        ``extract_best_lap``.
    """
    try:
        if df.empty or "Speed" not in df.columns:
            return []

        df = df.copy().reset_index(drop=True)
        speed = pd.to_numeric(df["Speed"], errors="coerce").fillna(0).values

        # Build a seconds timeline
        st_col = next(
            (c for c in df.columns if c.lower() in ("sessiontime", "time", "timestamp")),
            None,
        )
        if st_col is not None:
            raw = df[st_col]
            if pd.api.types.is_timedelta64_dtype(raw.dtype):
                t_sec = raw.dt.total_seconds().values.astype(float)
            else:
                td = pd.to_timedelta(raw, errors="coerce")
                if not td.isna().all():
                    t_sec = td.dt.total_seconds().values.astype(float)
                else:
                    t_sec = pd.to_numeric(raw, errors="coerce").values.astype(float)
        else:
            t_sec = np.arange(len(df), dtype=float) * 0.24  # ~4.2 Hz fallback

        t_sec = pd.Series(t_sec).ffill().bfill().values

        # Label each row as slow/fast and group into contiguous runs
        slow   = pd.Series(speed < gap_speed_kmh, index=df.index)
        run_id = (slow != slow.shift()).cumsum()

        # Collect boundary intervals from slow runs >= gap_min_s
        boundary_edges: list[tuple[int, int]] = []
        for gid in run_id[slow].unique():
            grp_idx = df.index[run_id == gid].to_numpy()
            if len(grp_idx) < 2:
                continue
            duration = float(t_sec[grp_idx[-1]]) - float(t_sec[grp_idx[0]])
            if duration >= gap_min_s:
                boundary_edges.append((int(grp_idx[0]), int(grp_idx[-1])))

        # Segments are the fast regions between adjacent boundaries
        all_bounds = [(-1, -1)] + sorted(boundary_edges) + [(len(df), len(df))]
        segments: list[tuple[int, int]] = []
        for i in range(len(all_bounds) - 1):
            seg_start = all_bounds[i][1] + 1
            seg_end   = all_bounds[i + 1][0] - 1
            if seg_end > seg_start:
                segments.append((seg_start, seg_end))

        laps: list[tuple[int, pd.DataFrame]] = []
        lap_num = 0
        for seg_start, seg_end in segments:
            seg = df.iloc[seg_start : seg_end + 1]
            if seg.empty:
                continue

            t_end_idx = min(seg_end, len(t_sec) - 1)
            duration  = float(t_sec[t_end_idx]) - float(t_sec[seg_start])
            if duration < lap_min_s:
                continue

            seg_speed = pd.to_numeric(seg["Speed"], errors="coerce")
            if seg_speed.mean() < lap_min_avg_kmh:
                continue

            lap_num += 1
            seg = seg.copy().reset_index(drop=True)

            # Reset Distance to zero from lap start
            if "X" in seg.columns and "Y" in seg.columns:
                seg["Distance"] = (
                    np.sqrt(seg["X"].diff() ** 2 + seg["Y"].diff() ** 2)
                    .fillna(0)
                    .cumsum()
                )
            elif "Distance" in seg.columns:
                seg["Distance"] = pd.to_numeric(seg["Distance"], errors="coerce")
                first_d = seg["Distance"].iloc[0]
                if pd.notna(first_d):
                    seg["Distance"] = seg["Distance"] - first_d

            laps.append((lap_num, seg))

        return laps

    except Exception:
        return []


# ── Core detection ────────────────────────────────────────────────────────────

def detect_lift_coast_zones(
    lap: pd.DataFrame,
    throttle_lifted_pct: float = THROTTLE_LIFTED,
    min_speed_loss_kmh: float = MIN_SPEED_LOSS,
    min_distance_m: float = MIN_DISTANCE_M,
    merge_gap_m: float = MAX_DISTANCE_GAP,
) -> pd.DataFrame:
    """Detect lift-and-coast zones in a single lap's telemetry.

    A *lift-and-coast* zone is a contiguous stretch where:
      - Throttle ≤ ``throttle_lifted_pct`` (driver has released the throttle)
      - Brake == 0  (no brake pressure applied)
      - Speed is decreasing (car is slowing via engine braking / drag)

    Parameters
    ----------
    lap : pd.DataFrame
        Single-lap telemetry sorted by distance.  Must contain columns
        ``Speed``, ``Throttle``, ``Brake``, ``Distance``.
        ``SessionTime`` (timedelta or seconds) is used for duration if present.
    throttle_lifted_pct : float
        Throttle percentage below which the driver is considered to be coasting.
    min_speed_loss_kmh : float
        Minimum speed drop across the zone to be reported.
    min_distance_m : float
        Minimum track length the zone must cover.
    merge_gap_m : float
        Adjacent segments separated by less than this are merged into one zone
        (handles brief throttle blips or data noise between two coast segments).

    Returns
    -------
    pd.DataFrame
        One row per zone with columns:
        ``start_dist``, ``end_dist``, ``zone_length``,
        ``entry_speed``, ``exit_speed``, ``speed_loss``,
        ``start_time_s``, ``end_time_s``, ``duration_s``.

        Distance values are in **the same units as the input Distance column**.
        OpenF1 X/Y-derived distances are in internal track coordinates
        (~10× real metres for most circuits). Use ``DistNorm`` (normalised to
        circuit length) if you need SI metres.

        Returns an empty DataFrame with these columns if none are found.
    """
    _EMPTY = pd.DataFrame(columns=[
        "start_dist", "end_dist", "zone_length",
        "entry_speed", "exit_speed", "speed_loss",
        "start_time_s", "end_time_s", "duration_s",
    ])

    required = {"Speed", "Throttle", "Brake", "Distance"}
    if not required.issubset(lap.columns):
        missing = required - set(lap.columns)
        raise ValueError(f"Telemetry is missing columns: {missing}")

    df = lap.reset_index(drop=True).copy()
    df["Speed"]    = pd.to_numeric(df["Speed"],    errors="coerce")
    df["Throttle"] = pd.to_numeric(df["Throttle"], errors="coerce")
    df["Brake"]    = pd.to_numeric(df["Brake"],    errors="coerce")
    df["Distance"] = pd.to_numeric(df["Distance"], errors="coerce")

    # Resolve time in seconds (optional — used for duration only)
    t_sec = None
    if "SessionTime" in df.columns:
        raw = df["SessionTime"]
        if pd.api.types.is_timedelta64_dtype(raw.dtype):
            t_sec = raw.dt.total_seconds().values
        else:
            td = pd.to_timedelta(raw, errors="coerce")
            if not td.isna().all():
                t_sec = td.dt.total_seconds().values
            else:
                t_sec_num = pd.to_numeric(raw, errors="coerce")
                if not t_sec_num.isna().all():
                    t_sec = t_sec_num.values

    dist   = df["Distance"].values
    speed  = df["Speed"].values
    throt  = df["Throttle"].fillna(100).values
    brake  = df["Brake"].fillna(0).values

    # Speed derivative: use simple forward difference to avoid divide-by-zero
    # when consecutive Distance values are identical (stationary / data noise).
    # dspeed[i] < 0  →  next sample is slower than this one  →  decelerating.
    dspeed = np.empty_like(speed, dtype=float)
    dspeed[:-1] = np.diff(speed.astype(float))
    dspeed[-1]  = dspeed[-2] if len(dspeed) > 1 else 0.0

    # Boolean mask: lifted throttle AND no brake AND decelerating
    coast_mask = (
        (throt <= throttle_lifted_pct) &
        (brake == 0) &
        (dspeed < 0)
    )

    # ── Find contiguous runs ─────────────────────────────────────────────────
    changes = np.diff(coast_mask.astype(int), prepend=0, append=0)
    starts  = np.where(changes ==  1)[0]
    ends    = np.where(changes == -1)[0]  # exclusive

    if len(starts) == 0:
        return _EMPTY

    segments = list(zip(starts, ends))

    # ── Merge nearby segments ────────────────────────────────────────────────
    merged = [segments[0]]
    for seg_start, seg_end in segments[1:]:
        prev_end = merged[-1][1] - 1
        if prev_end < len(dist) and seg_start < len(dist):
            gap = float(dist[seg_start]) - float(dist[prev_end])
            if gap <= merge_gap_m:
                merged[-1] = (merged[-1][0], seg_end)
                continue
        merged.append((seg_start, seg_end))

    # ── Build result rows ────────────────────────────────────────────────────
    rows = []
    for s_idx, e_idx in merged:
        e_idx_inc = min(e_idx, len(dist) - 1)  # inclusive end for value look-up

        d_start = float(dist[s_idx])
        d_end   = float(dist[e_idx_inc])
        length  = d_end - d_start
        if length < min_distance_m:
            continue

        v_entry = float(speed[s_idx])
        v_exit  = float(speed[e_idx_inc])
        loss    = v_entry - v_exit
        if loss < min_speed_loss_kmh:
            continue

        t_start = float(t_sec[s_idx])  if t_sec is not None else np.nan
        t_end   = float(t_sec[e_idx_inc]) if t_sec is not None else np.nan
        dur     = (t_end - t_start) if (t_sec is not None and not np.isnan(t_start)) else np.nan

        rows.append({
            "start_dist":  round(d_start, 1),
            "end_dist":    round(d_end,   1),
            "zone_length": round(length,  1),
            "entry_speed":  round(v_entry,  1),
            "exit_speed":   round(v_exit,   1),
            "speed_loss":   round(loss,     1),
            "start_time_s": round(t_start,  3) if not np.isnan(t_start) else np.nan,
            "end_time_s":   round(t_end,    3) if not np.isnan(t_end)   else np.nan,
            "duration_s":   round(dur,      3) if not np.isnan(dur)     else np.nan,
        })

    if not rows:
        return _EMPTY

    return pd.DataFrame(rows).reset_index(drop=True)


# ── Corner tagging (optional enrichment) ──────────────────────────────────────

def tag_nearest_corner(
    zones: pd.DataFrame,
    corners: pd.DataFrame,
    corner_dist_col: str = "Distance",
    corner_num_col:  str = "Number",
    lap_dist_max: float | None = None,
    window: float = 0.08,
) -> pd.DataFrame:
    """Tag each zone with the nearest upcoming corner.

    Handles the case where the lap's ``Distance`` column and the corners'
    distance column use different scales (e.g. OpenF1 X/Y-derived coordinates
    vs FastF1 circuit-length values) by proportionally scaling zone distances
    into the corners' distance space.

    Parameters
    ----------
    zones : pd.DataFrame
        Output of :func:`detect_lift_coast_zones`.
    corners : pd.DataFrame
        Circuit corner table with at least a distance and number column.
    corner_dist_col : str
        Column in ``corners`` containing the distance values.
    corner_num_col : str
        Column in ``corners`` containing the corner/turn number.
    lap_dist_max : float, optional
        Maximum distance value in the lap (total lap circumference in whatever
        unit the telemetry Distance column uses).  Used to scale zone distances
        into the corners' distance range.  If *None*, derived from
        ``zones['end_dist'].max()``.
    window : float
        Fraction of the circuit length to look ahead from the scaled zone exit.
        Default 0.08 = 8 % of the circuit (~430 m on Shanghai).

    Returns
    -------
    pd.DataFrame
        ``zones`` with two new columns: ``nearest_corner`` and
        ``dist_to_corner`` (in the corners' distance units).
    """
    if zones.empty:
        zones["nearest_corner"] = pd.Series(dtype="Int64")
        zones["dist_to_corner"] = pd.Series(dtype="float64")
        return zones

    c_dists = corners[corner_dist_col].astype(float).values
    c_nums  = corners[corner_num_col].values
    circuit_max = float(c_dists.max())

    # Scale zone distances into the corners' distance range
    lap_max = lap_dist_max if lap_dist_max is not None else float(zones["end_dist"].max())
    scale   = (circuit_max / lap_max) if lap_max > 0 else 1.0
    window_abs = window * circuit_max

    nearest_nums  = []
    nearest_dists = []

    for _, row in zones.iterrows():
        zone_end_scaled = row["end_dist"] * scale
        ahead = c_dists[
            (c_dists >= zone_end_scaled) &
            (c_dists <= zone_end_scaled + window_abs)
        ]
        if len(ahead) == 0:
            nearest_nums.append(pd.NA)
            nearest_dists.append(np.nan)
        else:
            nearest_c = ahead.min()
            idx       = np.where(c_dists == nearest_c)[0][0]
            nearest_nums.append(int(c_nums[idx]))
            nearest_dists.append(round(float(nearest_c - zone_end_scaled), 1))

    zones = zones.copy()
    zones["nearest_corner"] = pd.array(nearest_nums, dtype="Int64")
    zones["dist_to_corner"] = nearest_dists
    return zones


# ── Super-clipping detection ──────────────────────────────────────────────────

def detect_super_clipping_zones(
    lap: pd.DataFrame,
    throttle_full_pct: float = 95,
    min_speed_kmh: float = 200,
    min_speed_loss_kmh: float = 5,
    min_distance: float = 10,
    merge_gap: float = 50,
) -> pd.DataFrame:
    """Detect potential 'super clipping' zones in a single lap's telemetry.

    Super clipping is a 2026-era energy harvesting technique: the driver stays
    on **full throttle** while the ECU closes the active aero wings to harvest
    kinetic energy into the battery.  The wings produce more drag in closed
    (downforce) mode, causing the car to decelerate even though the driver has
    not lifted.

    Telemetry signature
    -------------------
    - Throttle >= ``throttle_full_pct`` % (driver is on full throttle)
    - Brake == 0 (no mechanical braking)
    - Speed >= ``min_speed_kmh`` km/h (only relevant on high-speed sections)
    - Speed decreasing sample-to-sample (car is decelerating despite full throttle)

    This distinguishes super clipping from:
    - **Normal acceleration** (full throttle + speed rising)
    - **Lift and coast** (throttle lifted + speed falling)
    - **Braking zone** (brake applied + speed falling)

    Parameters
    ----------
    lap : pd.DataFrame
        Single-lap telemetry.  Must contain ``Speed``, ``Throttle``, ``Brake``,
        ``Distance``.
    throttle_full_pct : float
        Minimum throttle % to count as "full throttle".  Default 95 allows for
        small sensor noise around 100%.
    min_speed_kmh : float
        Minimum speed for a zone to be reported.  Filters out slow-speed areas
        (pit lane, hairpins) where deceleration on throttle has other causes.
        Default 200 km/h targets high-speed straights and fast corners.
    min_speed_loss_kmh : float
        Minimum speed drop across the zone to be reported.  Super clipping
        produces a subtler deceleration than braking (5--15 km/h typical).
    min_distance : float
        Minimum track length the zone must cover (same units as Distance column).
    merge_gap : float
        Adjacent segments closer than this are merged.

    Returns
    -------
    pd.DataFrame
        One row per zone with columns:
        ``start_dist``, ``end_dist``, ``zone_length``,
        ``entry_speed``, ``exit_speed``, ``speed_loss``,
        ``start_time_s``, ``end_time_s``, ``duration_s``.
        Returns an empty DataFrame with these columns if none are found.

    Notes
    -----
    *Caution*: This detects *candidates*, not confirmed super clipping events.
    Deceleration on full throttle can also occur on uphill sections or when a
    driver hits the rev limiter.  Cross-reference with circuit elevation data
    and RPM to reduce false positives.  DRS state would be the ideal
    discriminator but is not reliably available in OpenF1 data.
    """
    _EMPTY = pd.DataFrame(columns=[
        "start_dist", "end_dist", "zone_length",
        "entry_speed", "exit_speed", "speed_loss",
        "start_time_s", "end_time_s", "duration_s",
    ])

    required = {"Speed", "Throttle", "Brake", "Distance"}
    if not required.issubset(lap.columns):
        missing = required - set(lap.columns)
        raise ValueError(f"Telemetry is missing columns: {missing}")

    df = lap.reset_index(drop=True).copy()
    df["Speed"]    = pd.to_numeric(df["Speed"],    errors="coerce")
    df["Throttle"] = pd.to_numeric(df["Throttle"], errors="coerce")
    df["Brake"]    = pd.to_numeric(df["Brake"],    errors="coerce")
    df["Distance"] = pd.to_numeric(df["Distance"], errors="coerce")

    # Resolve time in seconds (optional)
    t_sec = None
    if "SessionTime" in df.columns:
        raw = df["SessionTime"]
        if pd.api.types.is_timedelta64_dtype(raw.dtype):
            t_sec = raw.dt.total_seconds().values
        else:
            td = pd.to_timedelta(raw, errors="coerce")
            if not td.isna().all():
                t_sec = td.dt.total_seconds().values
            else:
                t_sec_num = pd.to_numeric(raw, errors="coerce")
                if not t_sec_num.isna().all():
                    t_sec = t_sec_num.values

    dist  = df["Distance"].values
    speed = df["Speed"].values
    throt = df["Throttle"].fillna(0).values
    brake = df["Brake"].fillna(0).values

    # Forward-difference speed derivative: negative = decelerating
    dspeed = np.empty_like(speed, dtype=float)
    dspeed[:-1] = np.diff(speed.astype(float))
    dspeed[-1]  = dspeed[-2] if len(dspeed) > 1 else 0.0

    # Mask: full throttle AND no brake AND high speed AND decelerating
    sc_mask = (
        (throt >= throttle_full_pct) &
        (brake == 0) &
        (speed >= min_speed_kmh) &
        (dspeed < 0)
    )

    # Find contiguous runs
    changes = np.diff(sc_mask.astype(int), prepend=0, append=0)
    starts  = np.where(changes ==  1)[0]
    ends    = np.where(changes == -1)[0]

    if len(starts) == 0:
        return _EMPTY

    segments = list(zip(starts, ends))

    # Merge nearby segments
    merged = [segments[0]]
    for seg_start, seg_end in segments[1:]:
        prev_end = merged[-1][1] - 1
        if prev_end < len(dist) and seg_start < len(dist):
            gap = float(dist[seg_start]) - float(dist[prev_end])
            if gap <= merge_gap:
                merged[-1] = (merged[-1][0], seg_end)
                continue
        merged.append((seg_start, seg_end))

    rows = []
    for s_idx, e_idx in merged:
        e_idx_inc = min(e_idx, len(dist) - 1)

        d_start = float(dist[s_idx])
        d_end   = float(dist[e_idx_inc])
        length  = d_end - d_start
        if length < min_distance:
            continue

        v_entry = float(speed[s_idx])
        v_exit  = float(speed[e_idx_inc])
        loss    = v_entry - v_exit
        if loss < min_speed_loss_kmh:
            continue

        t_start = float(t_sec[s_idx])     if t_sec is not None else np.nan
        t_end   = float(t_sec[e_idx_inc]) if t_sec is not None else np.nan
        dur     = (t_end - t_start) if (t_sec is not None and not np.isnan(t_start)) else np.nan

        rows.append({
            "start_dist":  round(d_start, 1),
            "end_dist":    round(d_end,   1),
            "zone_length": round(length,  1),
            "entry_speed": round(v_entry,  1),
            "exit_speed":  round(v_exit,   1),
            "speed_loss":  round(loss,     1),
            "start_time_s": round(t_start, 3) if not np.isnan(t_start) else np.nan,
            "end_time_s":   round(t_end,   3) if not np.isnan(t_end)   else np.nan,
            "duration_s":   round(dur,     3) if not np.isnan(dur)     else np.nan,
        })

    if not rows:
        return _EMPTY

    return pd.DataFrame(rows).reset_index(drop=True)


# ── Comparison helper ────────────────────────────────────────────────────────

def compare_coasting(
    zones_a: pd.DataFrame,
    zones_b: pd.DataFrame,
    label_a: str = "Driver A",
    label_b: str = "Driver B",
    match_window: float = 3000.0,
) -> pd.DataFrame:
    """Compare lift-and-coast zones between two drivers.

    Matches zones that start within ``match_window`` (in the same distance
    units as the input zones' ``start_dist`` column) of each other and reports
    differences in entry speed, exit speed, speed loss, and duration.

    Returns a DataFrame with one row per matched pair, plus unmatched zones
    flagged with the absent driver's columns as NaN.
    """
    if zones_a.empty and zones_b.empty:
        return pd.DataFrame()

    rows = []
    used_b = set()

    for i, za in zones_a.iterrows():
        best_j, best_dist = None, np.inf
        for j, zb in zones_b.iterrows():
            if j in used_b:
                continue
            d = abs(za["start_dist"] - zb["start_dist"])
            if d < best_dist and d <= match_window:
                best_dist, best_j = d, j

        if best_j is not None:
            zb = zones_b.loc[best_j]
            used_b.add(best_j)
            rows.append({
                "corner":                   za.get("nearest_corner", pd.NA),
                f"{label_a}_start_dist":    za["start_dist"],
                f"{label_b}_start_dist":    zb["start_dist"],
                "start_dist_delta_m":       round(zb["start_dist"] - za["start_dist"], 1),
                f"{label_a}_entry_speed":   za["entry_speed"],
                f"{label_b}_entry_speed":   zb["entry_speed"],
                "entry_speed_delta":        round(zb["entry_speed"] - za["entry_speed"], 1),
                f"{label_a}_speed_loss":    za["speed_loss"],
                f"{label_b}_speed_loss":    zb["speed_loss"],
                "speed_loss_delta":         round(zb["speed_loss"] - za["speed_loss"], 1),
                f"{label_a}_duration_s":    za.get("duration_s", np.nan),
                f"{label_b}_duration_s":    zb.get("duration_s", np.nan),
            })
        else:
            rows.append({
                "corner":                   za.get("nearest_corner", pd.NA),
                f"{label_a}_start_dist":    za["start_dist"],
                f"{label_b}_start_dist":    np.nan,
                "start_dist_delta_m":       np.nan,
                f"{label_a}_entry_speed":   za["entry_speed"],
                f"{label_b}_entry_speed":   np.nan,
                "entry_speed_delta":        np.nan,
                f"{label_a}_speed_loss":    za["speed_loss"],
                f"{label_b}_speed_loss":    np.nan,
                "speed_loss_delta":         np.nan,
                f"{label_a}_duration_s":    za.get("duration_s", np.nan),
                f"{label_b}_duration_s":    np.nan,
            })

    # Unmatched zones from B
    for j, zb in zones_b.iterrows():
        if j not in used_b:
            rows.append({
                "corner":                   zb.get("nearest_corner", pd.NA),
                f"{label_a}_start_dist":    np.nan,
                f"{label_b}_start_dist":    zb["start_dist"],
                "start_dist_delta_m":       np.nan,
                f"{label_a}_entry_speed":   np.nan,
                f"{label_b}_entry_speed":   zb["entry_speed"],
                "entry_speed_delta":        np.nan,
                f"{label_a}_speed_loss":    np.nan,
                f"{label_b}_speed_loss":    zb["speed_loss"],
                "speed_loss_delta":         np.nan,
                f"{label_a}_duration_s":    np.nan,
                f"{label_b}_duration_s":    zb.get("duration_s", np.nan),
            })

    return pd.DataFrame(rows)


# ── CLI demo ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    SQ_DIR      = Path("telemetry_out/2026/Chinese_Grand_Prix/Sprint_Qualifying")
    CORNERS_CSV = SQ_DIR / "circuit_corners.csv"
    DRIVERS     = ["LEC", "HAM"]

    # Reuse the lap-detection logic from plot_sq3_brake_throttle
    sys.path.insert(0, str(Path(__file__).parent))
    from plot_sq3_brake_throttle import load_full_telemetry, find_sq3_best_lap

    corners = pd.read_csv(CORNERS_CSV) if CORNERS_CSV.exists() else None

    all_zones: dict[str, pd.DataFrame] = {}

    for drv in DRIVERS:
        print(f"\n== {drv} ==================================================")
        full = load_full_telemetry(drv)
        lap  = find_sq3_best_lap(full, drv)

        lap_dist_max = float(lap["Distance"].max()) if "Distance" in lap.columns else None
        zones = detect_lift_coast_zones(lap)
        if corners is not None:
            zones = tag_nearest_corner(zones, corners, lap_dist_max=lap_dist_max)

        all_zones[drv] = zones
        pd.set_option("display.max_columns", None)
        pd.set_option("display.width", 140)
        print(zones.to_string(index=False))

    if len(all_zones) == 2:
        a, b = DRIVERS
        print(f"\n== Comparison: {a} vs {b} ==================================")
        cmp = compare_coasting(all_zones[a], all_zones[b], label_a=a, label_b=b)
        print(cmp.to_string(index=False))
