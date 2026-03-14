"""
Standalone script: LEC vs HAM - Chinese GP Sprint Qualifying
Plots Speed, Throttle, and Brake vs Distance for their best SQ3 lap.

NOTE: For SQ sessions FastF1's LapStartTime/Time have an offset relative to the
telemetry SessionTime (FastF1 warns "Sprint Qualifying is not supported by Ergast").
We detect SQ3 laps directly from telemetry using the inter-segment zero-speed gap.
"""

import pandas as pd
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from pathlib import Path
import fastf1

# ── Config ──────────────────────────────────────────────────────────────────
YEAR        = 2026
EVENT       = "Chinese Grand Prix"
SESSION     = "SQ"
DRIVERS     = ["LEC", "HAM"]
COLORS      = {"LEC": "#E8002D", "HAM": "#27F4D2"}   # Ferrari red, Mercedes teal
SMOOTH_M    = 80        # smoothing window in metres
OUT_DIR     = Path("visualizations")
OUT_DIR.mkdir(exist_ok=True)
# ────────────────────────────────────────────────────────────────────────────

fastf1.Cache.enable_cache("f1_cache")
session = fastf1.get_session(YEAR, EVENT, SESSION)
session.load(telemetry=False, weather=False, messages=False)
laps = session.laps

sq_dir      = Path("telemetry_out/2026/Chinese_Grand_Prix/Sprint_Qualifying")
CORNERS_CSV = sq_dir / "circuit_corners.csv"


def load_full_telemetry(driver: str) -> pd.DataFrame:
    files = list(sq_dir.glob(f"*_{driver}_telemetry.csv"))
    if not files:
        raise FileNotFoundError(f"No telemetry file for {driver}")
    df = pd.read_csv(files[0])
    df["SessionTime"] = pd.to_timedelta(df["SessionTime"])
    return df


def find_sq3_best_lap(df: pd.DataFrame, driver: str) -> pd.DataFrame:
    """
    Detect the best SQ3 flying lap directly from telemetry.

    Strategy:
    1. Find the last long zero-speed gap (>90 s) — marks SQ2→SQ3 break.
    2. Within the post-gap SQ3 window, find the ~93-second rolling window
       with the highest average speed — that is the flying lap.
    """
    # Find zero-speed runs (potential inter-segment breaks)
    zero_mask = df["Speed"] < 5
    run_id = (zero_mask != zero_mask.shift()).cumsum()
    zero_runs = df[zero_mask].groupby(run_id)["SessionTime"].agg(["min", "max"])
    zero_runs["dur"] = zero_runs["max"] - zero_runs["min"]
    long_gaps = zero_runs[zero_runs["dur"] >= pd.Timedelta("90s")]

    if long_gaps.empty:
        raise ValueError(f"{driver}: No inter-segment zero-speed gap found")

    # Last long gap marks SQ2→SQ3 transition
    sq3_start_time = long_gaps.iloc[-1]["max"]
    sq3_df = df[df["SessionTime"] > sq3_start_time].copy().reset_index(drop=True)

    if sq3_df.empty or sq3_df["Speed"].max() < 200:
        raise ValueError(f"{driver}: No SQ3 racing data found after gap at {sq3_start_time}")

    # Rolling 93-second window → best average speed = flying lap
    sq3_df["t_sec"] = sq3_df["SessionTime"].dt.total_seconds()
    times  = sq3_df["t_sec"].values
    speeds = sq3_df["Speed"].values
    WINDOW = 93.0   # seconds — slightly longer than 1:32 lap

    best_mean  = 0.0
    best_start = times[0]
    best_end   = times[0] + WINDOW

    for i, t in enumerate(times):
        mask      = (times >= t) & (times <= t + WINDOW)
        seg_speed = speeds[mask]
        if len(seg_speed) < 5:
            continue
        mean_s = float(np.mean(seg_speed))
        if mean_s > best_mean:
            best_mean  = mean_s
            best_start = t
            best_end   = t + WINDOW

    lap_df = sq3_df[(sq3_df["t_sec"] >= best_start) &
                    (sq3_df["t_sec"] <= best_end)].copy()

    # Recompute Distance from 0 within this lap
    lap_df = lap_df.reset_index(drop=True)
    lap_df["Distance"] = (
        np.sqrt(lap_df["X"].diff() ** 2 + lap_df["Y"].diff() ** 2)
        .fillna(0)
        .cumsum()
    )

    lap_dur = lap_df["SessionTime"].iloc[-1] - lap_df["SessionTime"].iloc[0]
    print(f"  {driver}: SQ3 flying lap | "
          f"session {lap_df['SessionTime'].iloc[0]} to {lap_df['SessionTime'].iloc[-1]} | "
          f"~{lap_dur} | "
          f"avg_speed={best_mean:.0f} km/h | "
          f"speed_max={lap_df['Speed'].max():.0f}")
    return lap_df


def smooth(series: pd.Series, distances: pd.Series, window_m: float) -> np.ndarray:
    """Distance-aware rolling mean."""
    if len(series) < 3:
        return series.values
    spacing = distances.diff().median()
    if pd.isna(spacing) or spacing <= 0:
        return series.values
    win = max(1, int(window_m / spacing))
    return series.rolling(win, center=True, min_periods=1).mean().values


# ── Get FastF1 lap times for labels only ─────────────────────────────────────
lap_times = {}
for drv in DRIVERS:
    drv_laps = laps.pick_drivers(drv)
    soft = drv_laps[drv_laps["Compound"] == "SOFT"].dropna(subset=["LapTime"])
    if not soft.empty:
        best = soft.loc[soft["LapTime"].idxmin(), "LapTime"]
        lap_times[drv] = best
    else:
        lap_times[drv] = None

# ── Main ─────────────────────────────────────────────────────────────────────
print("Detecting SQ3 laps from telemetry...")
tel_data = {}

for drv in DRIVERS:
    full_df = load_full_telemetry(drv)
    tel_data[drv] = find_sq3_best_lap(full_df, drv)

# ── Load corners and compute common distance scale ────────────────────────────
corners = pd.read_csv(CORNERS_CSV)
circuit_length_m = float(corners["Distance"].max())   # raw FastF1 circuit length

# Normalise each driver's lap distance to the circuit length so corners align
for drv in DRIVERS:
    df = tel_data[drv]
    lap_len = df["Distance"].max()
    if lap_len > 0:
        df["DistNorm"] = df["Distance"] / lap_len * circuit_length_m
    else:
        df["DistNorm"] = df["Distance"]

# Corner positions in metres (already in circuit_length_m scale)
corner_dists = corners["Distance"].astype(float).values
corner_nums  = corners["Number"].astype(int).values

# ── Plot ──────────────────────────────────────────────────────────────────────
fig, axes = plt.subplots(3, 1, figsize=(14, 13), sharex=True)
fig.patch.set_facecolor("#1a1a2e")
for ax in axes:
    ax.set_facecolor("#16213e")
    ax.tick_params(colors="white")
    ax.yaxis.label.set_color("white")
    for spine in ax.spines.values():
        spine.set_edgecolor("#444466")

channels = [
    ("Speed",    "Speed (km/h)",  (0, 360)),
    ("Throttle", "Throttle (%)",  (0, 105)),
    ("Brake",    "Brake (%)",     (0, 110)),
]

for (channel, ylabel, ylim), ax in zip(channels, axes):
    for drv in DRIVERS:
        df  = tel_data[drv]

        if channel not in df.columns:
            print(f"  WARNING: {channel} not in {drv} telemetry, skipping")
            continue

        values   = df[channel].ffill().fillna(0)
        dist     = df["DistNorm"]
        smoothed = smooth(values, dist, SMOOTH_M)

        # Build label with FastF1 best SOFT lap time (for reference)
        lt = lap_times.get(drv)
        if lt is not None:
            label = (f"{drv}  {lt.components.minutes:01d}:{lt.components.seconds:02d}."
                     f"{lt.components.milliseconds:03d}")
        else:
            label = drv

        ax.plot(dist, smoothed, color=COLORS[drv], linewidth=1.6,
                label=label, alpha=0.92)

    ax.set_ylabel(ylabel, fontsize=9, color="white")
    ax.set_ylim(ylim)
    ax.grid(axis="y", color="#2a2a4a", linewidth=0.5)
    # Vertical corner lines
    for cd in corner_dists:
        ax.axvline(cd, color="#555577", linewidth=0.6, linestyle=":", zorder=0)

# Set shared x-axis ticks to corner positions (bottom axis only, shared via sharex)
axes[-1].set_xticks(corner_dists)
axes[-1].set_xticklabels([f"T{n}" for n in corner_nums], fontsize=8, color="white")
axes[-1].set_xlabel("", fontsize=9)   # label replaced by turn numbers

# Legend on speed plot
axes[0].legend(loc="upper left", fontsize=9,
               facecolor="#1a1a2e", edgecolor="#444466", labelcolor="white")

# Title
def fmt_lt(lt):
    if lt is None:
        return "N/A"
    return f"{lt.components.minutes}:{lt.components.seconds:02d}.{lt.components.milliseconds:03d}"

fig.suptitle(
    f"Chinese GP  |  Sprint Qualifying SQ3  |  Best flying lap\n"
    f"LEC {fmt_lt(lap_times.get('LEC'))}  vs  HAM {fmt_lt(lap_times.get('HAM'))}",
    color="white", fontsize=11, y=0.98
)

fig.tight_layout(rect=[0, 0, 1, 0.95])

out_path = OUT_DIR / "Chinese_GP_SQ3_LEC_HAM_brake_throttle.png"
fig.savefig(out_path, dpi=150, bbox_inches="tight",
            facecolor=fig.get_facecolor())
plt.close(fig)

print(f"\nSaved: {out_path}")
