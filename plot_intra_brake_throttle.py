"""
plot_intra_brake_throttle.py
──────────────────────────────────────────────────────────────────────────────
Per-team intra-team Speed / Throttle / Brake overlay for the best lap of
each driver, for any session type.

Output
──────
  visualizations/{year}/{event}/{session_type}/intra/{Team}/brake_throttle.png

One PNG per team.  Corner numbers are shown on the x-axis.

Usage
─────
  python plot_intra_brake_throttle.py --year 2026 --event Chinese_Grand_Prix \\
      --session Qualifying

  python plot_intra_brake_throttle.py --year 2026 --event Chinese_Grand_Prix \\
      --session Qualifying --force

Called from pipeline:
  from plot_intra_brake_throttle import run
  run(year="2026", event="Chinese_Grand_Prix", session_type="Qualifying")
"""

import argparse
import re
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from plot_coasting_clipping import (
    _load_laps, _best_lap, load_best_lap_for_plot,
    _plot_xy_with_breaks, _draw_corners,
    TELEMETRY_ROOT, VIZ_ROOT,
)

# ── Paths ─────────────────────────────────────────────────────────────────────
TELEMETRY_ROOT = Path("telemetry_out")
VIZ_ROOT       = Path("visualizations")

# ── 2026 driver → team colour ─────────────────────────────────────────────────
# Primary team colour used for the faster teammate; lighter variant for the other.
_DRIVER_COLORS: dict[str, str] = {
    # Ferrari
    "LEC": "#E8002D", "HAM": "#B5001E",
    # McLaren
    "NOR": "#FF8000", "PIA": "#FFB347",
    # Mercedes
    "RUS": "#27F4D2", "ANT": "#16C0A5",
    # Red Bull Racing
    "VER": "#3671C6", "HAD": "#6A99D8",
    # Aston Martin
    "ALO": "#229971", "STR": "#55C49A",
    # Williams
    "ALB": "#64C4FF", "SAI": "#A4DAFF",
    # Alpine
    "GAS": "#FF87BC", "COL": "#FFC0D8",
    # Haas
    "BEA": "#C8CACA", "OCO": "#888B8D",
    # Racing Bulls
    "LAW": "#6692FF", "LIN": "#99AAFF",
    # Audi
    "HUL": "#2D826D", "BOR": "#5AB09A",
    # Cadillac
    "PER": "#FFFFFF", "BOT": "#AAAAAA",
}

_FALLBACK_COLORS = ["#FF6B6B", "#6BFFB8"]

# ── Distinct color pair per team (driver 1, driver 2) ─────────────────────────
# Colors chosen to be clearly separable on a dark background.
_TEAM_DRIVER_COLORS: dict[str, tuple[str, str]] = {
    "Ferrari":          ("#E8002D", "#FFFFFF"),   # red  + white
    "McLaren":          ("#FF8000", "#0090FF"),   # orange + electric blue
    "Mercedes":         ("#27F4D2", "#FFFFFF"),   # teal  + white
    "Red Bull Racing":  ("#3671C6", "#FFD700"),   # blue  + gold
    "Aston Martin":     ("#229971", "#FFD700"),   # green + gold
    "Williams":         ("#64C4FF", "#FF8C69"),   # sky blue + salmon
    "Alpine":           ("#FF87BC", "#0090FF"),   # pink  + blue
    "Haas F1 Team":     ("#C8CACA", "#FF4444"),   # silver + red
    "Racing Bulls":     ("#6692FF", "#FFD700"),   # blue  + gold
    "Audi":             ("#2D826D", "#FFD700"),   # teal  + gold
    "Cadillac":         ("#FFFFFF", "#FF6B6B"),   # white + red
}

# ── Hardcoded 2026 team → [d1, d2] fallback (used if FastF1 unavailable) ──────
_TEAM_PAIRS_2026: dict[str, list[str]] = {
    "Ferrari":          ["LEC", "HAM"],
    "McLaren":          ["NOR", "PIA"],
    "Mercedes":         ["RUS", "ANT"],
    "Red Bull Racing":  ["VER", "HAD"],
    "Aston Martin":     ["ALO", "STR"],
    "Williams":         ["ALB", "SAI"],
    "Alpine":           ["GAS", "COL"],
    "Haas F1 Team":     ["BEA", "OCO"],
    "Racing Bulls":     ["LAW", "LIN"],
    "Audi":             ["HUL", "BOR"],
    "Cadillac":         ["PER", "BOT"],
}


def _sanitize(s: str) -> str:
    return re.sub(r"[^\w]+", "_", s).strip("_")


def _get_team_pairs(
    year: str | int,
    event: str,
    session_type: str,
    tele_dir: Path,
) -> dict[str, list[str]]:
    """Try FastF1, then discover from telemetry files, then hardcoded fallback."""
    # ── FastF1 attempt ────────────────────────────────────────────────────────
    _TYPE_TO_F1 = {
        "qualifying": "Q", "practice_1": "FP1", "practice_2": "FP2",
        "practice_3": "FP3", "sprint": "S", "sprint_qualifying": "SQ",
        "race": "R",
    }
    f1_label = _TYPE_TO_F1.get(session_type.lower().replace(" ", "_"), session_type)
    event_human = event.replace("_", " ")
    try:
        import fastf1
        fastf1.Cache.enable_cache("f1_cache")
        sess = fastf1.get_session(int(year), event_human, f1_label)
        sess.load(telemetry=False, weather=False, messages=False)
        laps = sess.laps
        team_col = next(
            (c for c in laps.columns if c.lower() in ("team", "constructor", "teamname")),
            None,
        )
        if team_col and not laps.empty:
            pairs: dict[str, list[str]] = {}
            for team, grp in laps.groupby(team_col):
                drivers_ranked = (
                    grp.groupby("Driver")["LapTimeSec"]
                    .min()
                    .sort_values()
                    .index.tolist()[:2]
                )
                if len(drivers_ranked) == 2:
                    pairs[str(team)] = drivers_ranked
                    continue
                # Still need two drivers even without lap times
                all_drivers = grp["Driver"].dropna().unique().tolist()[:2]
                if len(all_drivers) >= 2:
                    pairs[str(team)] = all_drivers[:2]
            if pairs:
                return pairs
    except Exception:
        pass

    # ── Telemetry-file discovery fallback ─────────────────────────────────────
    _RE = re.compile(r"_([A-Z]{2,4})_telemetry\.csv$")
    present = {
        m.group(1) for f in tele_dir.glob("*_telemetry.csv")
        if (m := _RE.search(f.name))
    }
    if present:
        # Match found drivers against the hardcoded 2026 map
        result: dict[str, list[str]] = {}
        for team, pair in _TEAM_PAIRS_2026.items():
            matched = [d for d in pair if d in present]
            if len(matched) == 2:
                result[team] = matched
        if result:
            return result

    return {team: pair for team, pair in _TEAM_PAIRS_2026.items()}


def _load_best_lap(tfile: Path, session_type: str,
                   corners_path: Path | None = None) -> pd.DataFrame | None:
    """Load a telemetry CSV and extract the best flying lap.

    Uses ``extract_all_laps`` (gap-based segmentation) first, which is more
    reliable than the rolling-window heuristic in ``extract_best_lap``.  Falls
    back to ``extract_best_lap`` when no laps are segmented.
    """
    try:
        df = pd.read_csv(tfile)
        if "SessionTime" in df.columns:
            df["SessionTime"] = pd.to_timedelta(df["SessionTime"], errors="coerce")

        # Use the unified _best_lap helper which handles both sprint marathon
        # segments (gap-ratio test) and qualifying multi-circuit runs
        # (extract_t1_lap T1-crossing preferred, extract_best_lap fallback).
        from plot_coasting_clipping import _load_laps, _best_lap  # noqa: PLC0415
        laps, raw_df = _load_laps(tfile, session_type)
        result = _best_lap(laps, raw_df=raw_df, session_type=session_type,
                           corners_path=corners_path)
        if result is None:
            return None
        _, lap = result

        if lap.empty or "Distance" not in lap.columns:
            return None
        if pd.to_numeric(lap["Speed"], errors="coerce").max() < 80:
            return None
        return lap
    except Exception as exc:
        print(f"    [WARN] {tfile.name}: {exc}", file=sys.stderr)
        return None


def _smooth_on_grid(
    dist: np.ndarray,
    values: np.ndarray,
    xi: np.ndarray,
    window_m: float = 1500.0,
) -> np.ndarray:
    """Interpolate *values* onto *xi*, then apply a distance-based rolling mean.

    *window_m* is in the same coord-units as *xi*.  For Shanghai the circuit
    is ~54 000 coord-units (~5 500 m real), so 1 unit ~0.1 m.
    1500 units (~150 m) -- gives a smooth speed profile.
     500 units  (~50 m) -- used for Throttle/Brake to preserve on/off steps.
    """
    mask  = np.isfinite(dist) & np.isfinite(values) & (dist >= 0)
    if mask.sum() < 10:
        return np.full_like(xi, np.nan, dtype=float)
    d_c, v_c = dist[mask], values[mask]
    order = np.argsort(d_c)
    d_c, v_c = d_c[order], v_c[order]
    uniq = np.concatenate(([True], np.diff(d_c) > 1e-6))
    d_c, v_c = d_c[uniq], v_c[uniq]
    # Constant-edge extrapolation: avoids fade-to-zero when a driver's lap
    # ends slightly before circuit_length, which caused the flat-trace bug.
    yi = np.interp(xi, d_c, v_c)
    dx = xi[1] - xi[0] if len(xi) > 1 else 1.0
    win = max(3, min(int(window_m / dx), len(xi) // 4))
    return np.convolve(yi, np.ones(win, dtype=float) / win, mode="same")


# ── Per-team plot ──────────────────────────────────────────────────────────────

def plot_team(
    team: str,
    drivers: list[str],
    year: str,
    event: str,
    session_type: str,
    tele_dir: Path,
    corners_path: Path,
    out_dir: Path,
    force: bool = False,
) -> bool:
    """Plot Speed / Throttle / Brake overlay for two teammates' best laps.

    Returns True if a PNG was written.
    """
    out_path = out_dir / "brake_throttle.png"
    if out_path.exists() and not force:
        print(f"  {team}: (cached)")
        return False

    # Load laps — FastF1 time-window primary, T1-crossing fallback
    lap_data: dict[str, pd.DataFrame] = {}
    lap_labels: dict[str, str] = {}
    for drv in drivers:
        hits = list(tele_dir.glob(f"*_{drv}_telemetry.csv"))
        if not hits:
            continue
        lap, lbl = load_best_lap_for_plot(
            hits[0], session_type, drv, event, year, corners_path=corners_path,
        )
        if not lap.empty and lap.get("Speed") is not None:
            spd = pd.to_numeric(lap.get("Speed", pd.Series()), errors="coerce")
            if spd.max() >= 80 and "Distance" in lap.columns:
                lap_data[drv] = lap
                lap_labels[drv] = lbl

    if len(lap_data) < 2:
        print(f"  {team}: not enough telemetry ({list(lap_data)}) — skip", file=sys.stderr)
        return False

    # Build common distance grid from corners CSV
    lap_dist_max = max(
        float(pd.to_numeric(lap["Distance"], errors="coerce").max())
        for lap in lap_data.values()
    )

    circuit_length = lap_dist_max   # OpenF1 coordinate units
    corner_fracs: list[tuple[float, int]] = []
    if corners_path.exists():
        try:
            corners = pd.read_csv(corners_path)
            raw_max = float(corners["Distance"].max())
            # Scale corners Distance (FastF1 metres) to our OpenF1 coordinate space
            scale = lap_dist_max / raw_max if raw_max > 0 else 1.0
            for _, c in corners.iterrows():
                corner_fracs.append((float(c["Distance"]) * scale, int(c["Number"])))
        except Exception:
            pass

    xi = np.linspace(0, circuit_length, 1500)

    # ── Figure ─────────────────────────────────────────────────────────────────
    BG  = "#1a1a2e"
    AX  = "#16213e"
    fig, axes = plt.subplots(3, 1, figsize=(14, 13), sharex=True, facecolor=BG)
    for ax in axes:
        ax.set_facecolor(AX)
        ax.tick_params(colors="white")
        ax.yaxis.label.set_color("white")
        for spine in ax.spines.values():
            spine.set_edgecolor("#444466")

    channels: list[tuple[str, str, tuple[float, float], float]] = [
        ("Speed",    "Speed (km/h)",  (0.0, 360.0), 1500.0),
        ("Throttle", "Throttle (%)",  (0.0, 105.0),  500.0),
        ("Brake",    "Brake (%)",     (0.0, 110.0),  500.0),
    ]

    lap_time_labels: dict[str, str] = {}
    drv_color_pair = _TEAM_DRIVER_COLORS.get(team, ("#FFFFFF", "#FFD700"))

    for drv_idx, (drv, lap) in enumerate(lap_data.items()):
        dist_raw = pd.to_numeric(lap["Distance"], errors="coerce").values.astype(float)
        # Normalise driver Distance to the common circuit_length grid
        d_max = np.nanmax(dist_raw)
        if d_max > 0:
            dist_norm = dist_raw / d_max * circuit_length
        else:
            dist_norm = dist_raw

        # Label: FastF1 official time (no tilde) or estimated (with tilde)
        lap_time_labels[drv] = lap_labels.get(drv, drv)

        # Driver 0 = team primary colour, Driver 1 = high-contrast secondary
        color = drv_color_pair[min(drv_idx, 1)]

        for (channel, ylabel, ylim, win_m), ax in zip(channels, axes):
            if channel not in lap.columns:
                continue
            raw_vals = pd.to_numeric(lap[channel], errors="coerce").values.astype(float)
            smoothed = _smooth_on_grid(dist_norm, raw_vals, xi, window_m=win_m)
            ax.plot(xi, smoothed, color=color, linewidth=1.7, alpha=0.92,
                    label=lap_time_labels[drv])

    # Apply labels and styling per panel
    for (channel, ylabel, ylim, _), ax in zip(channels, axes):
        ax.set_ylabel(ylabel, fontsize=9, color="white")
        ax.set_ylim(ylim)
        ax.grid(axis="y", color="#2a2a4a", linewidth=0.5)
        # Vertical corner lines
        for cd, _ in corner_fracs:
            ax.axvline(cd, color="#555577", linewidth=0.6, linestyle=":", zorder=0)

    # Legend on Speed panel only
    axes[0].legend(
        loc="upper left", fontsize=9,
        facecolor="#1a1a2e", edgecolor="#444466", labelcolor="white",
    )

    # Corner numbers on ALL panels (x-axis ticks + vertical dotted lines)
    if corner_fracs:
        corner_xs   = [cd for cd, _ in corner_fracs]
        corner_lbls = [f"T{cn}" for _, cn in corner_fracs]
        for ax in axes:
            ax.set_xticks(corner_xs)
            ax.set_xticklabels(corner_lbls, fontsize=7, color="#aaaacc")
            ax.tick_params(axis="x", labelbottom=True, length=4, color="#555577")
    axes[-1].set_xlabel("", fontsize=9)

    team_display   = team.replace("_", " ")
    session_display = session_type.replace("_", " ")
    fig.suptitle(
        f"{event.replace('_', ' ')}  |  {session_display}  |  {team_display}\n"
        f"Speed · Throttle · Brake — best flying lap per driver",
        color="white", fontsize=11, y=0.99,
    )
    fig.tight_layout(rect=[0, 0, 1, 0.96])

    plt.savefig(out_path, dpi=150, facecolor=BG)
    plt.close(fig)
    print(f"  {team}: saved -> {out_path.name}")
    return True


# ── Entry points ───────────────────────────────────────────────────────────────

def run(
    year: str | int,
    event: str,
    session_type: str,
    force: bool = False,
) -> None:
    """Pipeline entry point.  Called from master_pipeline.stage_intra_brake_throttle()."""
    tele_dir = TELEMETRY_ROOT / str(year) / event / session_type
    if not tele_dir.exists():
        print(f"[intra_brake_throttle] Telemetry dir not found: {tele_dir}")
        return

    corners_path = tele_dir / "circuit_corners.csv"
    intra_root   = VIZ_ROOT / str(year) / event / session_type / "intra"
    intra_root.mkdir(parents=True, exist_ok=True)

    print(f"\n[intra_brake_throttle] {year} / {event} / {session_type}")

    team_pairs = _get_team_pairs(year, event, session_type, tele_dir)
    if not team_pairs:
        print("  No team pairs found — skipping")
        return

    written = 0
    for team, drivers in sorted(team_pairs.items()):
        team_tag = _sanitize(team)
        out_dir  = intra_root / team_tag
        out_dir.mkdir(parents=True, exist_ok=True)
        ok = plot_team(
            team, drivers, str(year), event, session_type,
            tele_dir, corners_path, out_dir, force=force,
        )
        if ok:
            written += 1

    print(f"  Done -- {written} new PNG(s) written")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Per-team intra-team Speed/Throttle/Brake best-lap overlay",
    )
    parser.add_argument("--year",    required=True, help="e.g. 2026")
    parser.add_argument("--event",   required=True, help="e.g. Chinese_Grand_Prix")
    parser.add_argument("--session", required=True, help="e.g. Qualifying")
    parser.add_argument("--force",   action="store_true", help="Overwrite existing PNGs")
    args = parser.parse_args()
    run(year=args.year, event=args.event, session_type=args.session, force=args.force)
