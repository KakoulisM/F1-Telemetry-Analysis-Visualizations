"""
plot_coasting_clipping.py
──────────────────────────────────────────────────────────────────────────────
Per-driver coasting + super-clipping zone visualisations.

For every driver in a session two sets of files are written:

  visualizations/{year}/{event}/{session_type}/coasting_clipping/
    {DRIVER}_zones.png        ← circuit map + speed/throttle profile
    session_summary.png       ← bar chart: zone counts for all drivers

The circuit map draws the track outline from the X/Y telemetry coordinates
and highlights:
  • Lift-and-coast zones  (cyan  #00e5ff)
  • Super-clipping zones  (orange #ff7700)

The right-hand panels show the best flying lap's Speed and Throttle traces
with zone spans shaded in the same colours.

Usage
─────
  python plot_coasting_clipping.py --year 2026 --event Chinese_Grand_Prix --session Qualifying
  python plot_coasting_clipping.py --year 2026 --event Chinese_Grand_Prix --session Qualifying --driver LEC
  python plot_coasting_clipping.py --year 2026 --event Chinese_Grand_Prix --session Qualifying --force

Called from the pipeline:
  from plot_coasting_clipping import run
  run(year="2026", event="Chinese_Grand_Prix", session_type="Qualifying")
"""

import argparse
import re
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from coasting_analysis import extract_all_laps, extract_best_lap, extract_t1_lap

# ── Paths ─────────────────────────────────────────────────────────────────────
TELEMETRY_ROOT = Path("telemetry_out")
COASTING_ROOT  = Path("coasting_zones")
SC_ROOT        = Path("super_clipping_zones")
VIZ_ROOT       = Path("visualizations")

# ── Colours / style ──────────────────────────────────────────────────────────
BG          = "#0d0d0d"
TRACK_C     = "#2a2a2a"
COAST_C     = "#00e5ff"   # cyan
SC_C        = "#ff7700"   # orange
SPEED_C     = "#e0e0e0"   # light grey
THROTTLE_C  = "#00ff88"   # green
BRAKE_C     = "#ff4444"   # red

_DRIVER_RE = re.compile(r"_([A-Z]{2,4})_telemetry\.csv$")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _load_laps(tfile: Path, session_type: str) -> tuple[list[tuple[int, pd.DataFrame]], pd.DataFrame]:
    """Load a telemetry file and return (laps, raw_df).

    *laps* is the result of ``extract_all_laps`` (gap-based segmentation).
    *raw_df* is the full unsegmented DataFrame, kept so that ``_best_lap``
    can fall back to ``extract_best_lap``'s time-window scan when needed.
    """
    try:
        df = pd.read_csv(tfile)
        if "SessionTime" in df.columns:
            df["SessionTime"] = pd.to_timedelta(df["SessionTime"], errors="coerce")
        laps = extract_all_laps(df, session_type=session_type)
        if not laps:
            best = extract_best_lap(df, session_type=session_type)
            laps = [(1, best)]
        return laps, df
    except Exception as exc:
        print(f"  [WARN] {tfile.name}: {exc}", file=sys.stderr)
        return [], pd.DataFrame()


def _plot_track_outline(
    ax,
    laps: list[tuple[int, pd.DataFrame]],
    color: str = "#3a3a3a",
    lw: float = 5.0,
    jump_thresh: float = 3000.0,
) -> None:
    """Draw the track as continuous grey lines, breaking where there is a large
    X/Y discontinuity (pit-entry/pit-exit teleportation in OpenF1 data)."""
    parts = [lap[["X", "Y"]] for _, lap in laps
             if "X" in lap.columns and "Y" in lap.columns]
    if not parts:
        return
    track_xy = pd.concat(parts, ignore_index=True)
    x = track_xy["X"].values.astype(float)
    y = track_xy["Y"].values.astype(float)
    _plot_xy_with_breaks(ax, x, y, color=color, lw=lw, alpha=0.55, zorder=1)


def _plot_xy_with_breaks(
    ax,
    x: np.ndarray,
    y: np.ndarray,
    color: str,
    lw: float,
    alpha: float = 1.0,
    zorder: int = 2,
    jump_thresh: float = 3000.0,
) -> None:
    """Plot x/y as a line, inserting NaN breaks where consecutive distance jumps
    exceed *jump_thresh* so matplotlib never draws the connector line."""
    if len(x) < 2:
        return
    dx = np.abs(np.diff(x))
    dy = np.abs(np.diff(y))
    jump_idx = np.where((dx > jump_thresh) | (dy > jump_thresh))[0] + 1  # rows AFTER the jump
    if jump_idx.size:
        x = x.copy().astype(float)
        y = y.copy().astype(float)
        x = np.insert(x, jump_idx, np.nan)
        y = np.insert(y, jump_idx, np.nan)
    ax.plot(x, y, color=color, lw=lw, alpha=alpha,
            solid_capstyle="round", zorder=zorder)


def _draw_corners(ax, corners_path: Path) -> None:
    """Annotate corner numbers on the circuit map.

    Corners CSV already carries ``X`` and ``Y`` in the same OpenF1 coordinate
    space as the telemetry, so no scaling is needed.
    """
    if not corners_path.exists():
        return
    try:
        corners = pd.read_csv(corners_path)
    except Exception:
        return
    if corners.empty or "X" not in corners.columns or "Number" not in corners.columns:
        return
    for _, c in corners.iterrows():
        cx, cy = float(c["X"]), float(c["Y"])
        number = str(int(c["Number"])) if pd.notna(c.get("Number")) else ""
        letter = str(c.get("Letter", "")).strip()
        label  = f"{number}{letter}" if letter and letter.lower() != "nan" else number
        ax.scatter(cx, cy, color="#ffffff", s=18, zorder=6, linewidths=0)
        ax.annotate(
            label,
            xy=(cx, cy),
            xytext=(0, 7),
            textcoords="offset points",
            color="#ffffff",
            fontsize=6.5,
            fontweight="bold",
            ha="center",
            zorder=7,
            bbox=dict(boxstyle="round,pad=0.15", facecolor="#000000",
                      edgecolor="none", alpha=0.55),
        )


def _best_lap(laps: list[tuple[int, pd.DataFrame]],
              raw_df: pd.DataFrame | None = None,
              session_type: str = "",
              corners_path: "Path | None" = None,
              ) -> tuple[int, pd.DataFrame] | None:
    """Return the fastest *single-circuit* flying lap.

    Strategy
    --------
    0. If *corners_path* is provided, try ``extract_t1_lap`` first.  This
       finds proper T1→T1 lap boundaries from the raw XY data and returns a
       segment that starts exactly at T1 — no rolling needed downstream.
    1. Collect all speed-valid laps from gap segmentation.
    2. Discard marathon segments (sprint/race blobs) via a >4× dist_max jump.
    3. Try ``extract_best_lap`` time-window scan when *raw_df* is available.
       If its result is significantly shorter than the best gap-segmented
       candidate (< 70 % of its dist_max), it found a true single flying lap
       inside a multi-circuit qualifying run → prefer it.
    4. Otherwise use the gap-segmented best candidate by mean speed.
    """
    # ── Step 0: T1-crossing approach (most accurate) ──────────────────────────
    if corners_path is not None and raw_df is not None and not raw_df.empty:
        try:
            from pathlib import Path as _Path
            cp = corners_path if isinstance(corners_path, _Path) else _Path(corners_path)
            if cp.exists():
                cdf = pd.read_csv(cp)
                t1 = cdf[cdf["Number"] == 1].iloc[0]
                t1_x, t1_y = float(t1["X"]), float(t1["Y"])
                t1_lap = extract_t1_lap(raw_df, t1_x=t1_x, t1_y=t1_y)
                if not t1_lap.empty:
                    t1_spd = pd.to_numeric(t1_lap.get("Speed", pd.Series()), errors="coerce")
                    if t1_spd.max() >= 80:
                        return 0, t1_lap
        except Exception:
            pass  # fall through to other strategies

    valid: list[tuple[int, pd.DataFrame, float, float]] = []
    for lap_num, lap in laps:
        if "Speed" not in lap.columns or "Distance" not in lap.columns:
            continue
        spd = pd.to_numeric(lap["Speed"],    errors="coerce")
        d   = pd.to_numeric(lap["Distance"], errors="coerce")
        if spd.max() < 80:
            continue
        valid.append((lap_num, lap, float(d.max()), float(spd.mean())))
    if not valid:
        # No gap-segmented candidates at all — fall straight to extract_best_lap
        if raw_df is not None:
            fb = extract_best_lap(raw_df, session_type=session_type)
            if not fb.empty and pd.to_numeric(fb.get("Speed", pd.Series()), errors="coerce").max() >= 80:
                return 0, fb
        return None

    # Step 2 – discard sprint/race marathon blobs (ratio >4×)
    dists = sorted(v[2] for v in valid)
    single_max = dists[-1]
    for i in range(len(dists) - 1):
        if dists[i] > 0 and dists[i + 1] / dists[i] > 4.0:
            single_max = dists[i]
            break
    candidates = [v for v in valid if v[2] <= single_max * 1.2] or valid
    best_gap = max(candidates, key=lambda x: x[3])

    # Step 3 – try extract_best_lap time-window scan.
    # If it finds a lap with dist_max < 70 % of the gap-selected candidate,
    # it has isolated a single flying lap inside a multi-circuit run
    # (typical for qualifying where 3–4 circuits appear in one segment).
    if raw_df is not None:
        try:
            fb = extract_best_lap(raw_df, session_type=session_type)
            if not fb.empty:
                fb_spd = pd.to_numeric(fb.get("Speed", pd.Series()), errors="coerce")
                fb_dist = float(pd.to_numeric(fb.get("Distance", pd.Series()), errors="coerce").max())
                if fb_spd.max() >= 80 and fb_dist < best_gap[2] * 0.70:
                    return 0, fb   # single flying lap found inside multi-circuit run
        except Exception:
            pass

    return best_gap[0], best_gap[1]


# ── FastF1-primary lap extraction ─────────────────────────────────────────────

_TYPE_TO_F1: dict[str, str] = {
    "qualifying":        "Q",
    "sprint":            "S",
    "sprint_qualifying": "SQ",
    "race":              "R",
    "practice_1":        "FP1",
    "practice_2":        "FP2",
    "practice_3":        "FP3",
}

# Cache FastF1 session objects keyed by (year, event, session_key) so we only
# call sess.load() once per session even when processing 20+ drivers in a loop.
_f1_session_cache: dict[tuple, object] = {}


def _get_f1_session(year: int, event: str, f1_key: str):
    """Return a loaded FastF1 session, using a module-level cache."""
    import fastf1  # noqa: PLC0415
    cache_key = (int(year), str(event), f1_key)
    if cache_key not in _f1_session_cache:
        fastf1.Cache.enable_cache("f1_cache")
        sess = fastf1.get_session(int(year), str(event).replace("_", " "), f1_key)
        sess.load(telemetry=False, weather=False, messages=False)
        _f1_session_cache[cache_key] = sess
    return _f1_session_cache[cache_key]


def _fastf1_best_lap(
    raw_df: pd.DataFrame,
    session_type: str,
    driver: str,
    event: str,
    year: "str | int",
) -> "tuple[pd.DataFrame, str] | None":
    """Return ``(lap_df, label)`` for the official fastest lap via FastF1.

    Strategy
    --------
    1. Use FastF1 to retrieve the driver's best lap duration (``LapTime``).
    2. Call ``extract_best_lap`` with that exact window size.  Because the
       window is sized to the official lap time, it finds the fastest *single*
       flying lap in the raw telemetry without needing a shared time origin
       between FastF1 and OpenF1 (which differs by ~600 s per session).
    3. Return the extracted segment along with an official label
       (e.g. ``"HAM  1:32.415"``) that has no ``~`` prefix.

    Returns ``None`` if FastF1 is unavailable or the driver has no timed lap.
    """
    f1_key = _TYPE_TO_F1.get(session_type.lower().replace(" ", "_"), session_type)
    try:
        sess = _get_f1_session(int(year), str(event), f1_key)
        drv_laps = sess.laps.pick_driver(driver)
        if drv_laps.empty or "LapTime" not in drv_laps.columns:
            return None

        lt_td = drv_laps["LapTime"].apply(
            lambda x: pd.to_timedelta(x, errors="coerce") if pd.notna(x) else pd.NaT
        )
        valid = drv_laps[lt_td.notna()].copy()
        if valid.empty:
            return None
        valid["_lt_s"] = lt_td[lt_td.notna()].dt.total_seconds()
        best_row = valid.loc[valid["_lt_s"].idxmin()]
        lt_s = float(best_row["_lt_s"])
        if lt_s < 30:
            return None   # implausible — guard against data corruption

        # Extract the matching segment using the exact FastF1 window size.
        # This avoids any need to reconcile the ~600 s time-origin difference
        # between FastF1 session clocks and OpenF1 SessionTime.
        lap_slice = extract_best_lap(raw_df, session_type=session_type,
                                     window_s=lt_s + 2.0)
        if lap_slice.empty:
            return None

        spd = pd.to_numeric(lap_slice.get("Speed", pd.Series()), errors="coerce")
        if spd.max() < 80:
            return None

        m = int(lt_s // 60)
        s = lt_s % 60
        label = f"{driver}  {m}:{s:06.3f}"
        return lap_slice, label

    except Exception:
        return None


def load_best_lap_for_plot(
    tfile: Path,
    session_type: str,
    driver: str,
    event: str,
    year: "str | int",
    corners_path: "Path | None" = None,
) -> "tuple[pd.DataFrame, str]":
    """Load the best flying lap, preferring FastF1 boundaries over heuristics.

    Returns ``(lap_df, label_str)`` where *label_str* is suitable for a chart
    legend (e.g. ``"HAM  1:32.415"`` from FastF1, or ``"HAM  ~1:34.200"`` if
    FastF1 is unavailable and we fall back to the T1-crossing estimate).
    """
    # Load raw CSV once for all strategies
    try:
        raw_df = pd.read_csv(tfile)
        if "SessionTime" in raw_df.columns:
            raw_df["SessionTime"] = pd.to_timedelta(raw_df["SessionTime"], errors="coerce")
    except Exception:
        return pd.DataFrame(), driver

    # ── Priority 1: FastF1 official time-window extraction ────────────────────
    result = _fastf1_best_lap(raw_df, session_type, driver, event, year)
    if result is not None:
        return result

    # ── Priority 2: T1-crossing / gap-segmentation fallback ───────────────────
    laps, _ = _load_laps(tfile, session_type)
    best = _best_lap(laps, raw_df=raw_df, session_type=session_type,
                     corners_path=corners_path)
    if best is None:
        return pd.DataFrame(), driver
    _, lap = best

    # Estimate label from SessionTime span (approximate — no S/F timing line)
    label = driver
    if "SessionTime" in lap.columns:
        try:
            st = lap["SessionTime"].dt.total_seconds()
            elapsed = abs(float(st.iloc[-1]) - float(st.iloc[0]))
            m = int(elapsed // 60)
            s = elapsed % 60
            label = f"{driver}  ~{m}:{s:06.3f}"
        except Exception:
            pass
    return lap, label


def _zone_segments(
    zones: pd.DataFrame,
    laps_dict: dict[int, pd.DataFrame],
    color: str,
    ax,
    lw: float = 2.5,
) -> int:
    """Plot each zone as a coloured line segment on the circuit map axes.

    Handles teleportation artefacts (large X/Y jumps between consecutive rows)
    by inserting NaN breaks so matplotlib never draws the connector line.
    """
    n_plotted = 0
    for _, row in zones.iterrows():
        lap_num = int(row.get("lap_number", 1))
        lap_df  = laps_dict.get(lap_num)
        if lap_df is None or "X" not in lap_df.columns or "Distance" not in lap_df.columns:
            continue
        dist = pd.to_numeric(lap_df["Distance"], errors="coerce")
        mask = (dist >= row["start_dist"]) & (dist <= row["end_dist"])
        seg  = lap_df[mask]
        if seg.empty:
            continue
        _plot_xy_with_breaks(
            ax,
            seg["X"].values.astype(float),
            seg["Y"].values.astype(float),
            color=color, lw=lw, alpha=0.85, zorder=4,
        )
        n_plotted += 1
    return n_plotted


def _shade_zones(zones: pd.DataFrame, lap_num: int, ax, color: str, alpha: float = 0.20):
    """Shade axvspan for zones belonging to *lap_num* on a distance-axis subplot."""
    if zones.empty or "lap_number" not in zones.columns:
        return
    for _, row in zones[zones["lap_number"] == lap_num].iterrows():
        ax.axvspan(row["start_dist"], row["end_dist"],
                   color=color, alpha=alpha, zorder=1, linewidth=0)


# ── Per-driver plot ───────────────────────────────────────────────────────────

def plot_driver(
    driver: str,
    year: str,
    event: str,
    session_type: str,
    out_dir: Path,
    force: bool = False,
) -> tuple[int, int]:
    """
    Build and save a per-driver zones PNG.

    Returns
    -------
    (n_coast_zones, n_sc_zones)  — both 0 on failure.
    """
    out_path = out_dir / f"{driver}_zones.png"
    coast_csv = COASTING_ROOT / year / event / session_type / f"{driver}_zones.csv"
    sc_csv    = SC_ROOT        / year / event / session_type / f"{driver}_sc_zones.csv"

    if not coast_csv.exists() and not sc_csv.exists():
        return 0, 0

    if out_path.exists() and not force:
        print(f"  {driver:>4}  (cached)")
        return 0, 0

    coast_zones = pd.read_csv(coast_csv) if coast_csv.exists() else pd.DataFrame()
    sc_zones    = pd.read_csv(sc_csv)    if sc_csv.exists()    else pd.DataFrame()

    # Drop empty-marker rows (files written when no zones were found)
    if not coast_zones.empty and "start_dist" in coast_zones.columns:
        coast_zones = coast_zones.dropna(subset=["start_dist"])
    if not sc_zones.empty and "start_dist" in sc_zones.columns:
        sc_zones = sc_zones.dropna(subset=["start_dist"])

    # Load telemetry
    tele_dir  = TELEMETRY_ROOT / year / event / session_type
    tele_hits = list(tele_dir.glob(f"*_{driver}_telemetry.csv"))
    if not tele_hits:
        print(f"  {driver:>4}  no telemetry — skip", file=sys.stderr)
        return 0, 0

    laps, raw_df = _load_laps(tele_hits[0], session_type)
    if not laps:
        return 0, 0

    laps_dict   = dict(laps)
    best_result = _best_lap(laps, raw_df=raw_df, session_type=session_type)
    if best_result is None:
        return 0, 0

    best_lap_num, best_lap_df = best_result
    dist = pd.to_numeric(best_lap_df.get("Distance", pd.Series(dtype=float)), errors="coerce")
    spd  = pd.to_numeric(best_lap_df.get("Speed",    pd.Series(dtype=float)), errors="coerce")
    thr  = pd.to_numeric(best_lap_df.get("Throttle", pd.Series(dtype=float)), errors="coerce")
    dist_max = float(dist.max()) if not dist.isna().all() else 1.0

    # Corners CSV lives alongside the telemetry files
    corners_path = tele_dir / "circuit_corners.csv"

    # ── One-lap x-axis ────────────────────────────────────────────────────────
    # The extracted "best lap" may span many circuit laps (e.g. full Sprint race).
    # We compute a single-circuit-lap length and clamp the x-axis to it so:
    #   • Sprint shows one representative lap (not ×10^6 scientific ticks)
    #   • Qualifying shows the flying lap cleanly
    # Scale: corners CSV is in FastF1 metres; telemetry Distance is in OpenF1 units.
    corner_xs:    list[float] = []
    corner_labels: list[str]  = []
    one_lap_len = dist_max   # fallback: show raw distance if no corners
    if corners_path.exists():
        try:
            _cdf = pd.read_csv(corners_path)
            _corners_dist_max = float(_cdf["Distance"].max())
            if _corners_dist_max > 0 and dist_max > 0:
                _n_laps = max(1, round(dist_max / _corners_dist_max))
                one_lap_len = dist_max / _n_laps
                _scale = one_lap_len / _corners_dist_max
                for _, _c in _cdf.iterrows():
                    corner_xs.append(float(_c["Distance"]) * _scale)
                    _lbl = str(int(_c["Number"]))
                    _let = str(_c.get("Letter", "")).strip()
                    corner_labels.append(f"T{_lbl}{_let}" if _let and _let.lower() != "nan" else f"T{_lbl}")
        except Exception:
            pass

    # ── Build figure ──────────────────────────────────────────────────────────
    fig = plt.figure(figsize=(21, 9), facecolor=BG)
    # Left: circuit map
    ax_map = fig.add_axes([0.01, 0.04, 0.52, 0.90])
    # Top-right: speed
    ax_spd = fig.add_axes([0.57, 0.54, 0.40, 0.38])
    # Bottom-right: throttle
    ax_thr = fig.add_axes([0.57, 0.08, 0.40, 0.38])

    for ax in (ax_map, ax_spd, ax_thr):
        ax.set_facecolor(BG)
        ax.tick_params(colors="#aaaaaa", labelsize=8)
        for spine in ax.spines.values():
            spine.set_edgecolor("#333333")

    # ── Circuit map – track outline (continuous line, breaks at pit teleports) ─
    _plot_track_outline(ax_map, laps, color="#3a3a3a", lw=5.0)

    # Circuit map – zone overlays
    n_coast = _zone_segments(coast_zones, laps_dict, COAST_C, ax_map, lw=3.0)
    n_sc    = _zone_segments(sc_zones,    laps_dict, SC_C,    ax_map, lw=3.0)

    # Corner numbers
    _draw_corners(ax_map, corners_path)

    ax_map.set_aspect("equal")
    ax_map.axis("off")
    title_txt = (
        f"{driver}   |   {event.replace('_', ' ')}   {session_type.replace('_', ' ')}"
    )
    ax_map.set_title(title_txt, color="white", fontsize=12, fontweight="bold", pad=6)

    # Legend on map
    legend_patches = [
        mpatches.Patch(facecolor=COAST_C, label=f"Lift & Coast  ({n_coast} zones across all laps)"),
        mpatches.Patch(facecolor=SC_C,    label=f"Super Clipping  ({n_sc} zones across all laps)"),
    ]
    ax_map.legend(
        handles=legend_patches, loc="lower left", fontsize=8.5,
        facecolor="#1a1a1a", edgecolor="#444444", labelcolor="white",
    )

    # ── Speed profile ─────────────────────────────────────────────────────────
    _shade_zones(coast_zones, best_lap_num, ax_spd, COAST_C, alpha=0.18)
    _shade_zones(sc_zones,    best_lap_num, ax_spd, SC_C,    alpha=0.18)
    ax_spd.plot(dist, spd, color=SPEED_C, lw=1.0, zorder=2)
    ax_spd.set_ylabel("Speed (km/h)", color="#aaaaaa", fontsize=8)
    ax_spd.set_xlim(0, one_lap_len)
    ax_spd.set_ylim(bottom=0)
    ax_spd.set_title(
        f"Best lap: #{best_lap_num}  (highest avg speed)", color="#aaaaaa", fontsize=8
    )
    ax_spd.tick_params(labelbottom=False)
    if corner_xs:
        ax_spd.set_xticks(corner_xs)
        for cx in corner_xs:
            ax_spd.axvline(cx, color="#333355", lw=0.5, ls=":", zorder=0)

    # ── Throttle profile ──────────────────────────────────────────────────────
    _shade_zones(coast_zones, best_lap_num, ax_thr, COAST_C, alpha=0.18)
    _shade_zones(sc_zones,    best_lap_num, ax_thr, SC_C,    alpha=0.18)
    if not thr.isna().all():
        ax_thr.plot(dist, thr, color=THROTTLE_C, lw=1.0, zorder=2)
    ax_thr.set_ylabel("Throttle (%)", color="#aaaaaa", fontsize=8)
    ax_thr.set_xlim(0, one_lap_len)
    ax_thr.set_ylim(-5, 110)
    if corner_xs:
        ax_thr.set_xticks(corner_xs)
        ax_thr.set_xticklabels(corner_labels, fontsize=7, color="#aaaacc")
        for cx in corner_xs:
            ax_thr.axvline(cx, color="#333355", lw=0.5, ls=":", zorder=0)
    else:
        ax_thr.set_xlabel("Distance (track units)", color="#aaaaaa", fontsize=8)

    # ── Save ──────────────────────────────────────────────────────────────────
    plt.savefig(out_path, dpi=150, bbox_inches="tight", facecolor=BG)
    plt.close(fig)
    print(f"  {driver:>4}  coast={n_coast}  sc={n_sc}  ->  {out_path.name}")
    return n_coast, n_sc


# ── Session summary chart ──────────────────────────────────────────────────────

def plot_session_summary(
    summary: list[tuple[str, int, int]],   # [(driver, n_coast, n_sc), ...]
    year: str,
    event: str,
    session_type: str,
    out_dir: Path,
    force: bool = False,
) -> None:
    """Bar chart comparing lift-and-coast vs super-clipping counts per driver."""
    out_path = out_dir / "session_summary.png"
    if out_path.exists() and not force:
        print(f"  session_summary (cached)")
        return

    # Sort by total zones descending
    summary = sorted(summary, key=lambda x: x[1] + x[2], reverse=True)
    drivers  = [s[0] for s in summary]
    n_coast  = [s[1] for s in summary]
    n_sc     = [s[2] for s in summary]

    x       = np.arange(len(drivers))
    bar_w   = 0.38

    fig, ax = plt.subplots(figsize=(max(12, len(drivers) * 0.9 + 2), 6), facecolor=BG)
    ax.set_facecolor(BG)

    bars_c = ax.bar(x - bar_w / 2, n_coast, bar_w, color=COAST_C, alpha=0.85,
                    label="Lift & Coast", zorder=2)
    bars_s = ax.bar(x + bar_w / 2, n_sc,    bar_w, color=SC_C,    alpha=0.85,
                    label="Super Clipping", zorder=2)

    # Value labels
    for bar in (*bars_c, *bars_s):
        h = bar.get_height()
        if h > 0:
            ax.text(
                bar.get_x() + bar.get_width() / 2, h + 0.5,
                str(int(h)), ha="center", va="bottom",
                color="white", fontsize=7.5, fontweight="bold",
            )

    ax.set_xticks(x)
    ax.set_xticklabels(drivers, color="white", fontsize=9, fontweight="bold")
    ax.set_ylabel("Zone count (all laps)", color="#aaaaaa", fontsize=9)
    ax.tick_params(colors="#aaaaaa")
    for spine in ax.spines.values():
        spine.set_edgecolor("#333333")
    ax.yaxis.grid(True, color="#222222", zorder=0)
    ax.set_axisbelow(True)

    ax.set_title(
        f"{event.replace('_', ' ')} — {session_type.replace('_', ' ')}  |  Zone counts by driver",
        color="white", fontsize=12, fontweight="bold", pad=10,
    )
    ax.legend(
        facecolor="#1a1a1a", edgecolor="#444444",
        labelcolor="white", fontsize=9,
    )

    plt.tight_layout()
    plt.savefig(out_path, dpi=150, bbox_inches="tight", facecolor=BG)
    plt.close(fig)
    print(f"  session_summary  ->  {out_path.name}")


# ── Entry points ──────────────────────────────────────────────────────────────

def run(
    year: str,
    event: str,
    session_type: str,
    driver_filter: str | None = None,
    force: bool = False,
) -> None:
    """
    Programmatic entry point — called from master_pipeline.py.

    Parameters
    ----------
    year, event, session_type : str
        Session identifiers matching the directory tree.
    driver_filter : str, optional
        If set, only process this driver code.
    force : bool
        Overwrite existing PNGs.
    """
    out_dir = VIZ_ROOT / year / event / session_type / "coasting_clipping"
    out_dir.mkdir(parents=True, exist_ok=True)

    # Discover drivers that have at least one zone CSV
    coast_session_dir = COASTING_ROOT / year / event / session_type
    sc_session_dir    = SC_ROOT        / year / event / session_type

    drivers: set[str] = set()
    for d in (coast_session_dir, sc_session_dir):
        if d.exists():
            for f in d.glob("*_zones.csv"):
                m = re.search(r"^([A-Z]{2,4})_", f.name)
                if m:
                    drivers.add(m.group(1))
            for f in d.glob("*_sc_zones.csv"):
                m = re.search(r"^([A-Z]{2,4})_", f.name)
                if m:
                    drivers.add(m.group(1))

    if not drivers:
        print(f"[coasting_clipping] No zone files found for {year}/{event}/{session_type}")
        return

    if driver_filter:
        drivers = {d for d in drivers if d == driver_filter.upper()}
        if not drivers:
            print(f"[coasting_clipping] Driver '{driver_filter}' not found in zone files")
            return

    print(f"\n[coasting_clipping] {year} / {event} / {session_type}  ({len(drivers)} drivers)")

    summary: list[tuple[str, int, int]] = []
    for driver in sorted(drivers):
        n_coast, n_sc = plot_driver(driver, year, event, session_type, out_dir, force=force)
        summary.append((driver, n_coast, n_sc))

    # Session summary only when we actually processed (not all-cached)
    non_zero = [(d, c, s) for d, c, s in summary if c > 0 or s > 0]
    if non_zero or force:
        # Reload totals from CSV for cached drivers too
        full_summary: list[tuple[str, int, int]] = []
        for driver in sorted(drivers):
            coast_csv = coast_session_dir / f"{driver}_zones.csv"
            sc_csv    = sc_session_dir    / f"{driver}_sc_zones.csv"
            nc = len(pd.read_csv(coast_csv).dropna(subset=["start_dist"])) if coast_csv.exists() else 0
            ns = len(pd.read_csv(sc_csv).dropna(subset=["start_dist"]))    if sc_csv.exists()    else 0
            full_summary.append((driver, nc, ns))
        plot_session_summary(full_summary, year, event, session_type, out_dir, force=force)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Plot coasting and super-clipping zone visualisations",
    )
    parser.add_argument("--year",    required=True, help="e.g. 2026")
    parser.add_argument("--event",   required=True, help="e.g. Chinese_Grand_Prix")
    parser.add_argument("--session", required=True, help="e.g. Qualifying")
    parser.add_argument("--driver",  default=None,  help="Single driver code, e.g. LEC")
    parser.add_argument("--force",   action="store_true", help="Overwrite existing PNGs")
    args = parser.parse_args()

    run(
        year=args.year,
        event=args.event,
        session_type=args.session,
        driver_filter=args.driver,
        force=args.force,
    )
