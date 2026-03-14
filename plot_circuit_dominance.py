"""
plot_circuit_dominance.py
──────────────────────────────────────────────────────────────────────────────
Sector-dominance circuit map for a pair of drivers.

Each segment of the track is coloured by which driver carries more speed
there (their colour) — the F1 mini-sector concept in map form.
A speed-overlay panel and a speed-delta fill panel complete the picture.

Output
──────
  visualizations/{year}/{event}/{session_type}/intra/{Team}/circuit_dominance.png
  visualizations/{year}/{event}/{session_type}/02_circuit_dominance.png

Usage
─────
  python plot_circuit_dominance.py --year 2026 --event Chinese_Grand_Prix \\
      --session Qualifying

  python plot_circuit_dominance.py --year 2026 --event Chinese_Grand_Prix \\
      --session Qualifying --force

Called from pipeline:
  from plot_circuit_dominance import run
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
from matplotlib.collections import LineCollection
import numpy as np
import pandas as pd

from plot_coasting_clipping import (
    _load_laps,
    _best_lap,
    load_best_lap_for_plot,
    _plot_xy_with_breaks,
    _draw_corners,
    TELEMETRY_ROOT,
    VIZ_ROOT,
)
from plot_intra_brake_throttle import (
    _TEAM_DRIVER_COLORS,
    _DRIVER_COLORS,
    _sanitize,
    _get_team_pairs,
)

_BG        = "#0d0d0d"
_DRIVER_RE = re.compile(r"_([A-Z]{2,4})_telemetry\.csv$")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _best_lap_from_file(tfile: Path, session_type: str,
                        corners_path: Path | None = None,
                        driver: str = "",
                        event: str = "",
                        year: "str | int" = 0):
    """(lap_num, lap_df) for the best *single-circuit* flying lap in *tfile*.

    Uses ``load_best_lap_for_plot`` which tries FastF1 official time-window
    first, then falls back to the T1-crossing / gap-segmentation heuristics.
    """
    if driver and event and year:
        lap, _label = load_best_lap_for_plot(
            tfile, session_type, driver, str(event), year, corners_path=corners_path,
        )
        if not lap.empty:
            return 0, lap

    # Fallback when driver/event/year not provided
    laps, raw_df = _load_laps(tfile, session_type)
    if not laps:
        return None, None
    result = _best_lap(laps, raw_df=raw_df, session_type=session_type,
                       corners_path=corners_path)
    return result if result is not None else (None, None)


def _smooth(dist_raw, vals_raw, xi, window_m=1500.0):
    """Constant-edge-extrapolating moving average onto distance grid *xi*.

    *window_m* is expressed in the same coordinate units as *xi*.
    Shanghai circuit ~54 000 coord-units ~5 500 m  -> 1 unit ~0.1 m.
    A window_m of 1500 units corresponds to ~150 m of real track,
    giving a clean speed profile without over-smoothing corner apexes.
    """
    d = np.asarray(dist_raw, dtype=float)
    v = np.asarray(vals_raw, dtype=float)
    ok = np.isfinite(d) & np.isfinite(v)
    if ok.sum() < 10:
        return np.full_like(xi, np.nan, dtype=float)
    d, v = d[ok], v[ok]
    idx = np.argsort(d)
    d, v = d[idx], v[idx]
    uniq = np.concatenate(([True], np.diff(d) > 1e-6))
    d, v = d[uniq], v[uniq]
    yi = np.interp(xi, d, v)   # constant edge extrapolation — no NaN fade
    dx = (xi[-1] - xi[0]) / max(len(xi) - 1, 1)
    win = max(3, min(int(window_m / dx), len(xi) // 5))
    return np.convolve(yi, np.ones(win, dtype=float) / win, mode="same")


def _dominance_collection(
    ref_lap: pd.DataFrame,
    xi: np.ndarray,
    spd1: np.ndarray,
    spd2: np.ndarray,
    color1: str,
    color2: str,
    lw: float = 5.0,
    jump_thresh: float = 3000.0,
) -> LineCollection | None:
    """
    LineCollection where each segment is coloured by which driver is faster.

    *ref_lap* provides the X/Y track geometry.
    *spd1* and *spd2* must already be interpolated onto *xi*.
    """
    ref_d = pd.to_numeric(ref_lap.get("Distance", pd.Series()), errors="coerce")
    ref_x = pd.to_numeric(ref_lap.get("X",        pd.Series()), errors="coerce")
    ref_y = pd.to_numeric(ref_lap.get("Y",        pd.Series()), errors="coerce")
    ok = ref_d.notna() & ref_x.notna() & ref_y.notna()
    if ok.sum() < 20:
        return None

    rd = ref_d[ok].values.astype(float)
    rx = ref_x[ok].values.astype(float)
    ry = ref_y[ok].values.astype(float)

    # Remove teleportation rows before interpolating
    ddx = np.abs(np.diff(rx))
    ddy = np.abs(np.diff(ry))
    bad = np.where((ddx > jump_thresh) | (ddy > jump_thresh))[0]
    if bad.size:
        bad_rows = np.unique(np.concatenate([bad, bad + 1]))
        bad_rows = bad_rows[bad_rows < len(rd)]
        keep = np.ones(len(rd), dtype=bool)
        keep[bad_rows] = False
        rd, rx, ry = rd[keep], rx[keep], ry[keep]

    if len(rd) < 10:
        return None

    # Recompute arc-length from the cleaned XY positions.
    arc = np.concatenate(([0.0], np.cumsum(np.sqrt(np.diff(rx)**2 + np.diff(ry)**2))))

    # After removing the direct jump rows, a pit-lane fragment (e.g. rows 0-43
    # before the teleport) is still joined to the main circuit via a large arc
    # jump.  Detect any remaining arc jump > jump_thresh and keep only the
    # longest contiguous segment (the real circuit).
    arc_steps = np.diff(arc)
    indirect_bad = np.where(arc_steps > jump_thresh)[0]
    if indirect_bad.size:
        split_pts = np.concatenate([[0], indirect_bad + 1, [len(rx)]])
        seg_lens = np.diff(split_pts)
        best_seg = int(np.argmax(seg_lens))
        s0, s1 = int(split_pts[best_seg]), int(split_pts[best_seg + 1])
        rx, ry, arc = rx[s0:s1], ry[s0:s1], arc[s0:s1]
        arc = arc - arc[0]   # reset so the segment starts at 0

    # Scale arc to [0, circuit_len] so rd aligns perfectly with xi
    circuit_max = xi[-1] if len(xi) > 0 else 1.0
    arc_max = arc[-1] if arc[-1] > 0 else 1.0
    rd = arc * (circuit_max / arc_max)

    idx = np.argsort(rd)
    rd, rx, ry = rd[idx], rx[idx], ry[idx]
    uniq = np.concatenate(([True], np.diff(rd) > 1e-6))
    rd, rx, ry = rd[uniq], rx[uniq], ry[uniq]

    # gap_xi_skip: blank xi inside any residual distance gaps
    gap_xi_skip = np.zeros(len(xi), dtype=bool)
    if len(rd) > 1:
        rd_steps = np.diff(rd)
        med_rd = float(np.median(rd_steps[rd_steps > 0])) if (rd_steps > 0).any() else 1.0
        for gi in np.where(rd_steps > med_rd * 15)[0]:
            gap_xi_skip |= (xi >= rd[gi]) & (xi <= rd[gi + 1])

    # Map grid distance to X, Y
    grid_x = np.interp(xi, rd, rx)
    grid_y = np.interp(xi, rd, ry)

    # Detect remaining teleportation in interpolated grid
    seg_len = np.sqrt(np.diff(grid_x) ** 2 + np.diff(grid_y) ** 2)
    pos_len = seg_len[seg_len > 0]
    med = float(np.median(pos_len)) if pos_len.size else 1.0
    teleport = seg_len > med * 20

    # Per-segment speed comparison
    s1 = np.where(np.isfinite(spd1[:-1]), spd1[:-1], 0.0)
    s2 = np.where(np.isfinite(spd2[:-1]), spd2[:-1], 0.0)
    skip = teleport | ((s1 == 0) & (s2 == 0)) | gap_xi_skip[:-1]

    # Build segment array (N-1, 2, 2)
    pts = np.column_stack([grid_x, grid_y]).reshape(-1, 1, 2)
    all_segs = np.concatenate([pts[:-1], pts[1:]], axis=1)

    valid_idx = np.where(~skip)[0]
    if valid_idx.size == 0:
        return None

    valid_segs = all_segs[valid_idx]
    colors = [color1 if s1[i] >= s2[i] else color2 for i in valid_idx]

    return LineCollection(
        valid_segs, colors=colors, linewidth=lw,
        capstyle="round", joinstyle="round", zorder=3,
    )


def _roll_to_t1(lap: pd.DataFrame, corners_path: Path) -> pd.DataFrame:
    """Roll lap data so it starts at the T1 (start/finish) position.

    ``extract_best_lap`` and ``extract_all_laps`` both produce laps that begin
    wherever the driver happened to exit the pit lane or where the time-window
    started — typically mid-main-straight.  Rolling to T1 ensures the circuit
    map shows a complete loop with no gap on the main straight.

    If T1 cannot be located in *corners_path*, the lap is returned unchanged.
    """
    if not corners_path.exists():
        return lap
    try:
        cdf = pd.read_csv(corners_path)
        t1 = cdf[cdf["Number"] == 1].iloc[0]
        t1_x, t1_y = float(t1["X"]), float(t1["Y"])
    except Exception:
        return lap

    x = pd.to_numeric(lap.get("X", pd.Series(dtype=float)), errors="coerce").fillna(0).values
    y = pd.to_numeric(lap.get("Y", pd.Series(dtype=float)), errors="coerce").fillna(0).values
    d2t1 = np.sqrt((x - t1_x) ** 2 + (y - t1_y) ** 2)
    t1_row = int(np.argmin(d2t1))

    if t1_row == 0:
        return lap   # already starts at T1

    # Roll so T1 row becomes row 0
    rolled = pd.concat([lap.iloc[t1_row:], lap.iloc[:t1_row]]).reset_index(drop=True)

    # Recompute Distance as cumulative arc-length from T1, scaled to match
    # the original circuit length so the distance axis stays consistent.
    rx = pd.to_numeric(rolled.get("X", pd.Series(dtype=float)), errors="coerce").values.astype(float)
    ry = pd.to_numeric(rolled.get("Y", pd.Series(dtype=float)), errors="coerce").values.astype(float)
    arc = np.concatenate(([0.0], np.cumsum(np.sqrt(np.diff(rx) ** 2 + np.diff(ry) ** 2))))
    orig_max = float(pd.to_numeric(lap.get("Distance", pd.Series(dtype=float)), errors="coerce").max())
    if arc[-1] > 0 and orig_max > 0:
        rolled["Distance"] = arc * (orig_max / arc[-1])
    return rolled


def _corner_ticks(ax, corners_path: Path, circuit_len: float) -> None:
    """Add T1…Tn x-ticks and dotted corner lines to *ax*."""
    if not corners_path.exists():
        return
    try:
        corners = pd.read_csv(corners_path)
        raw_max = float(corners["Distance"].max())
        scale   = circuit_len / raw_max if raw_max > 0 else 1.0
        c_xs    = (corners["Distance"].astype(float) * scale).tolist()
        c_lbls  = [f"T{int(n)}" for n in corners["Number"]]
        ax.set_xticks(c_xs)
        ax.set_xticklabels(c_lbls, fontsize=7, color="#aaaacc")
        for cx in c_xs:
            ax.axvline(cx, color="#333355", lw=0.5, ls=":", zorder=0)
    except Exception:
        pass


# ── Core plot ─────────────────────────────────────────────────────────────────

def _plot_pair(
    driver1: str,
    driver2: str,
    color1: str,
    color2: str,
    year: str,
    event: str,
    session_type: str,
    tele_dir: Path,
    corners_path: Path,
    out_path: Path,
    title: str,
    force: bool = False,
) -> bool:
    """Build the circuit dominance figure for one driver pair.  Returns True if saved."""
    if out_path.exists() and not force:
        return False

    # Load best laps
    files: dict[str, Path] = {}
    for drv in (driver1, driver2):
        hits = list(tele_dir.glob(f"*_{drv}_telemetry.csv"))
        if not hits:
            print(f"  [{driver1}/{driver2}] no telemetry for {drv}", file=sys.stderr)
            return False
        files[drv] = hits[0]

    _, lap1 = _best_lap_from_file(files[driver1], session_type, corners_path,
                                   driver=driver1, event=event, year=year)
    _, lap2 = _best_lap_from_file(files[driver2], session_type, corners_path,
                                   driver=driver2, event=event, year=year)

    if lap1 is None or lap2 is None:
        print(f"  [{driver1}/{driver2}] lap extraction failed", file=sys.stderr)
        return False

    # If extract_t1_lap succeeded the lap already starts at T1; otherwise roll
    # it so the circuit map starts at T1 (handles the rare fallback paths).
    lap1 = _roll_to_t1(lap1, corners_path)
    lap2 = _roll_to_t1(lap2, corners_path)

    spd1_raw = pd.to_numeric(lap1.get("Speed", pd.Series()), errors="coerce")
    spd2_raw = pd.to_numeric(lap2.get("Speed", pd.Series()), errors="coerce")
    if spd1_raw.max() < 80 or spd2_raw.max() < 80:
        print(f"  [{driver1}/{driver2}] speed too low", file=sys.stderr)
        return False

    # Use the lap with higher max speed as the track-layout reference
    ref_lap = lap1.copy() if spd1_raw.max() >= spd2_raw.max() else lap2.copy()

    # Normalise both laps' Distance to a common circuit length
    d1 = pd.to_numeric(lap1.get("Distance", pd.Series()), errors="coerce").values.astype(float)
    d2 = pd.to_numeric(lap2.get("Distance", pd.Series()), errors="coerce").values.astype(float)
    circuit_len = float(max(np.nanmax(d1), np.nanmax(d2)))

    def norm(arr):
        mx = np.nanmax(arr)
        return arr / mx * circuit_len if mx > 0 else arr

    nd1, nd2 = norm(d1), norm(d2)

    # Normalise the reference lap's Distance column (for LineCollection)
    ref_d_col = pd.to_numeric(ref_lap.get("Distance", pd.Series()), errors="coerce").values.astype(float)
    ref_mx = np.nanmax(ref_d_col)
    if ref_mx > 0:
        ref_lap["Distance"] = ref_d_col / ref_mx * circuit_len

    xi = np.linspace(0, circuit_len, 600)

    # Smooth speeds onto common grid (window_m in coord-units; 1500 ≈ 150 m real)
    spd1_grid = _smooth(nd1, spd1_raw.values.astype(float), xi, window_m=1500.0)
    spd2_grid = _smooth(nd2, spd2_raw.values.astype(float), xi, window_m=1500.0)
    delta = spd1_grid - spd2_grid   # positive = driver1 faster

    # ── Figure ────────────────────────────────────────────────────────────────
    fig = plt.figure(figsize=(21, 9), facecolor=_BG)
    ax_map = fig.add_axes([0.01, 0.04, 0.52, 0.90])
    ax_spd = fig.add_axes([0.57, 0.52, 0.40, 0.40])
    ax_del = fig.add_axes([0.57, 0.06, 0.40, 0.40])

    for ax in (ax_map, ax_spd, ax_del):
        ax.set_facecolor(_BG)
        ax.tick_params(colors="#aaaaaa", labelsize=8)
        for spine in ax.spines.values():
            spine.set_edgecolor("#333333")

    # ── Circuit map ── grey base outline ─────────────────────────────────────
    _plot_xy_with_breaks(
        ax_map,
        ref_lap["X"].values.astype(float),
        ref_lap["Y"].values.astype(float),
        color="#2e2e2e", lw=9.0, alpha=0.8, zorder=1,
    )

    # Dominance-coloured segments on top
    lc = _dominance_collection(ref_lap, xi, spd1_grid, spd2_grid, color1, color2, lw=5.0)
    if lc is not None:
        ax_map.add_collection(lc)

    _draw_corners(ax_map, corners_path)
    ax_map.set_aspect("equal")
    ax_map.axis("off")
    ax_map.set_title(title, color="white", fontsize=11, fontweight="bold", pad=6)
    ax_map.legend(
        handles=[
            mpatches.Patch(facecolor=color1, label=f"{driver1} faster"),
            mpatches.Patch(facecolor=color2, label=f"{driver2} faster"),
        ],
        loc="lower left", fontsize=9,
        facecolor="#1a1a1a", edgecolor="#444444", labelcolor="white",
    )

    # ── Speed overlay ─────────────────────────────────────────────────────────
    ax_spd.plot(xi, spd1_grid, color=color1, lw=1.6, alpha=0.9, label=driver1)
    ax_spd.plot(xi, spd2_grid, color=color2, lw=1.6, alpha=0.9, label=driver2)
    ax_spd.set_ylabel("Speed (km/h)", color="#aaaaaa", fontsize=8)
    ax_spd.set_xlim(0, circuit_len)
    ax_spd.set_ylim(bottom=0)
    ax_spd.tick_params(labelbottom=False)
    ax_spd.legend(
        loc="lower right", fontsize=8,
        facecolor="#1a1a1a", edgecolor="#444444", labelcolor="white",
    )
    _corner_ticks(ax_spd, corners_path, circuit_len)

    # ── Speed delta (filled) ──────────────────────────────────────────────────
    ax_del.axhline(0, color="#555555", lw=0.8, zorder=1)
    ax_del.fill_between(xi, 0, delta, where=delta >= 0,
                        color=color1, alpha=0.55, zorder=2, label=f"{driver1} ahead")
    ax_del.fill_between(xi, 0, delta, where=delta < 0,
                        color=color2, alpha=0.55, zorder=2, label=f"{driver2} ahead")
    ax_del.plot(xi, delta, color="white", lw=0.6, alpha=0.4, zorder=3)
    ax_del.set_ylabel(
        f"{driver1} - {driver2}\nSpeed delta (km/h)",
        color="#aaaaaa", fontsize=7,
    )
    ax_del.set_xlim(0, circuit_len)
    ax_del.legend(
        loc="lower right", fontsize=7,
        facecolor="#1a1a1a", edgecolor="#444444", labelcolor="white",
    )
    _corner_ticks(ax_del, corners_path, circuit_len)

    plt.savefig(out_path, dpi=150, facecolor=_BG)
    plt.close(fig)
    return True


# ── Top-2 driver discovery ─────────────────────────────────────────────────────

def _get_top2(year, event, session_type, tele_dir: Path) -> list[str]:
    """Return [faster_driver, slower_driver] for the session.

    Priority:
    1. FastF1 session results  — finishing position for sprint/race;
       classified grid position for qualifying/sprint-qualifying.
    2. FastF1 best lap times (fallback when results unavailable).
    3. Best-lap average-speed heuristic from our own telemetry.
    """
    # ── 1 + 2: FastF1 ─────────────────────────────────────────────────────────
    _TYPE_TO_F1 = {
        "qualifying": "Q", "practice_1": "FP1", "practice_2": "FP2",
        "practice_3": "FP3", "sprint": "S", "sprint_qualifying": "SQ", "race": "R",
    }
    f1_label = _TYPE_TO_F1.get(session_type.lower().replace(" ", "_"), session_type)
    try:
        import fastf1
        fastf1.Cache.enable_cache("f1_cache")
        sess = fastf1.get_session(int(year), event.replace("_", " "), f1_label)
        sess.load(telemetry=False, weather=False, messages=False)

        # Try session results first (finishing/classified position)
        if hasattr(sess, "results") and sess.results is not None and not sess.results.empty:
            res = sess.results.copy()
            pos_col = next(
                (c for c in ("Position", "ClassifiedPosition", "GridPosition")
                 if c in res.columns),
                None,
            )
            abbr_col = next(
                (c for c in ("Abbreviation", "DriverNumber") if c in res.columns),
                None,
            )
            if pos_col and abbr_col:
                res[pos_col] = pd.to_numeric(res[pos_col], errors="coerce")
                top = (
                    res.dropna(subset=[pos_col])
                    .sort_values(pos_col)
                    [abbr_col].tolist()[:2]
                )
                if len(top) == 2:
                    return top
                # Position column is NaN (common for live/recent sessions).
                # FastF1 orders the results DataFrame by finishing position so
                # the first two rows give the correct P1 / P2 classification.
                top = res[abbr_col].dropna().tolist()[:2]
                if len(top) == 2:
                    return top

        # Fallback: fastest lap time per driver
        laps = sess.laps
        if not laps.empty and "LapTimeSec" in laps.columns:
            best = laps.groupby("Driver")["LapTimeSec"].min().dropna().sort_values()
            top  = best.index.tolist()[:2]
            if len(top) == 2:
                return top
    except Exception:
        pass

    # ── 3. Fallback: highest average speed on best lap ────────────────────────
    speed_map: dict[str, float] = {}
    corners_path_local = tele_dir / "circuit_corners.csv"
    for f in sorted(tele_dir.glob("*_telemetry.csv")):
        m = _DRIVER_RE.search(f.name)
        if not m:
            continue
        drv = m.group(1)
        _, lap = _best_lap_from_file(f, session_type, corners_path_local,
                                     driver=drv, event=event, year=year)
        if lap is None:
            continue
        spd = pd.to_numeric(lap.get("Speed", pd.Series()), errors="coerce")
        if spd.max() >= 80:
            speed_map[drv] = float(spd.mean())
    return sorted(speed_map, key=speed_map.get, reverse=True)[:2]  # type: ignore[arg-type]


# ── Entry points ───────────────────────────────────────────────────────────────

def run(year, event, session_type, force=False):
    """Run circuit dominance for all intra-team pairs + session top 2."""
    tele_dir     = TELEMETRY_ROOT / str(year) / event / session_type
    corners_path = tele_dir / "circuit_corners.csv"
    intra_root   = VIZ_ROOT / str(year) / event / session_type / "intra"
    sess_root    = VIZ_ROOT / str(year) / event / session_type

    if not tele_dir.exists():
        print(f"[circuit_dominance] Telemetry dir not found: {tele_dir}")
        return

    print(f"\n[circuit_dominance] {year} / {event} / {session_type}")

    # ── Intra-team ─────────────────────────────────────────────────────────────
    team_pairs = _get_team_pairs(year, event, session_type, tele_dir)
    for team, pair in sorted(team_pairs.items()):
        if len(pair) < 2:
            continue
        d1, d2 = pair[0], pair[1]
        colors   = _TEAM_DRIVER_COLORS.get(team, ("#FFFFFF", "#FFD700"))
        c1, c2   = colors[0], colors[1]
        team_tag = _sanitize(team)
        out_dir  = intra_root / team_tag
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / "circuit_dominance.png"
        title    = (
            f"{d1} vs {d2}  |  {team}  |  "
            f"{event.replace('_', ' ')} {session_type.replace('_', ' ')}"
        )
        ok     = _plot_pair(d1, d2, c1, c2, str(year), event, session_type,
                            tele_dir, corners_path, out_path, title, force=force)
        status = "saved" if ok else ("cached" if out_path.exists() else "failed")
        print(f"  {team}: {d1} vs {d2} -> {status}")

    # ── Session top 2 ──────────────────────────────────────────────────────────
    top2 = _get_top2(year, event, session_type, tele_dir)
    if len(top2) >= 2:
        d1, d2 = top2[0], top2[1]

        # Find which team each driver belongs to (from the team_pairs map)
        team_of: dict[str, str] = {}
        for team, pair in team_pairs.items():
            for drv in pair:
                team_of[drv] = team

        if team_of.get(d1) and team_of.get(d1) == team_of.get(d2):
            # Same team — use the already-contrasting team colour pair
            c1, c2 = _TEAM_DRIVER_COLORS.get(team_of[d1], ("#FFFFFF", "#FFD700"))
        else:
            # Different teams — use each driver's team primary colour, but
            # guarantee contrast by falling back to gold if they'd be similar
            c1 = _DRIVER_COLORS.get(d1, "#FFFFFF")
            c2 = _DRIVER_COLORS.get(d2, "#FFD700")
            # Simple similarity check: if both colours share the same dominant
            # channel (both are mainly cyan/teal, both mainly red, etc.) swap c2
            def _dominant(hex_c: str) -> int:
                h = hex_c.lstrip("#")
                r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
                return max((r, 0), (g, 1), (b, 2), key=lambda x: x[0])[1]
            if _dominant(c1) == _dominant(c2):
                c2 = "#FFD700"  # gold always contrasts with any team primary

        sess_root.mkdir(parents=True, exist_ok=True)
        out_path = sess_root / "02_circuit_dominance.png"
        title    = (
            f"{d1} vs {d2}  |  Session Top 2  |  "
            f"{event.replace('_', ' ')} {session_type.replace('_', ' ')}"
        )
        ok     = _plot_pair(d1, d2, c1, c2, str(year), event, session_type,
                            tele_dir, corners_path, out_path, title, force=force)
        status = "saved" if ok else ("cached" if out_path.exists() else "failed")
        print(f"  Top 2: {d1} ({c1}) vs {d2} ({c2}) -> {status}")
    else:
        print("  Top 2: could not determine drivers")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Circuit dominance map: coloured by who is faster where",
    )
    parser.add_argument("--year",    required=True, help="e.g. 2026")
    parser.add_argument("--event",   required=True, help="e.g. Chinese_Grand_Prix")
    parser.add_argument("--session", required=True, help="e.g. Qualifying")
    parser.add_argument("--force",   action="store_true", help="Overwrite existing PNGs")
    args = parser.parse_args()
    run(year=args.year, event=args.event, session_type=args.session, force=args.force)
