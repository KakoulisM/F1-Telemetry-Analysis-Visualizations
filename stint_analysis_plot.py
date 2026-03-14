from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter
import numpy as np
import pandas as pd

def plot_driver_stint_analysis(laps: pd.DataFrame, driver_code: str, outdir: Path):
    """
    Stint analysis plot for a single driver with black background, colored bars by compound,
    mean time line, best lap highlight, and mm:ss:ms formatting.
    """
    df = laps[(laps["Driver"] == driver_code) & laps["LapTime"].notna()].copy()
    if df.empty:
        print(f"No valid laps for driver {driver_code}")
        return
    if "LapTimeSec" not in df.columns:
        try:
            df["LapTime"] = pd.to_timedelta(df["LapTime"], errors="coerce")
        except Exception:
            pass
        df["LapTimeSec"] = df["LapTime"].dt.total_seconds()
    df = df[df["LapTimeSec"].notna()].copy()
    if df.empty:
        print(f"No numeric lap times for driver {driver_code}")
        return
    df = df.sort_values(["Stint", "LapNumber"])
        compound_colors = {
            "HARD": "white",
            "MEDIUM": "yellow",
            "SOFT": "red",
            "INTERMEDIATE": "darkgreen",
            "WET": "blue",
        }
    fig, ax = plt.subplots(figsize=(10, 6))
    fig.patch.set_facecolor('black')
    ax.set_facecolor('black')
    offset = 0
    total_plotted = 0
    for stint in sorted(df["Stint"].dropna().unique()):
        stint_df = df[df["Stint"] == stint].sort_values("LapNumber").copy()
        if stint_df.empty:
            continue
        mean_lap = stint_df["LapTimeSec"].mean()
        filtered_stint_df = stint_df[(stint_df["LapTimeSec"] >= mean_lap - 5) & (stint_df["LapTimeSec"] <= mean_lap + 5)].copy()
        n_laps = len(filtered_stint_df)
        if n_laps == 0:
            continue
        x = np.arange(offset + 1, offset + n_laps + 1)
        offset += n_laps
        total_plotted += n_laps
        comp_series = (
            filtered_stint_df.get("StintCompound", filtered_stint_df.get("Compound", pd.Series()))
            .dropna()
            .astype(str)
            .str.upper()
        )
        if not comp_series.empty:
            compound = comp_series.mode().iloc[0]
        else:
            comp_series = (
                filtered_stint_df.get("Compound", pd.Series())
                .dropna()
                .astype(str)
                .str.upper()
            )
            compound = comp_series.mode().iloc[0] if not comp_series.empty else "UNKNOWN"
        color = compound_colors.get(compound, "gray")
        label = f"Stint {int(stint) if pd.notna(stint) else '?'} ({compound})"
        for idx, lap_time in enumerate(filtered_stint_df["LapTimeSec"]):
            lap_idx = x[idx]
            is_best = (lap_time == filtered_stint_df["LapTimeSec"].min())
            bar_color = 'lime' if is_best else color
            ax.bar(lap_idx, lap_time, color=bar_color, width=0.7, edgecolor='white', zorder=2)
            mins = int(lap_time // 60)
            secs = int(lap_time % 60)
            ms = int((lap_time - mins * 60 - secs) * 1000)
            ax.text(lap_idx, lap_time + 0.5, f"{mins}:{secs:02d}:{ms:03d}", ha='center', va='bottom', color='white', fontsize=9, fontweight='bold')
        ax.axhline(mean_lap, color=color, linestyle='--', linewidth=2, zorder=1)
        mins = int(mean_lap // 60)
        secs = int(mean_lap % 60)
        ms = int((mean_lap - mins * 60 - secs) * 1000)
        ax.text(x.mean(), mean_lap, f"Mean: {mins}:{secs:02d}:{ms:03d}", ha='center', va='bottom', color='white', fontsize=10, fontweight='bold', backgroundcolor=color)
    if total_plotted == 0:
        print(f"No laps plotted for driver {driver_code}")
        return
    ax.set_xlabel("Lap (continuous across stints)", color='white')
    ax.set_ylabel("Lap time (mm:ss:ms)", color='white')
    ax.yaxis.set_major_formatter(FuncFormatter(lambda val, pos: f"{int(val//60)}:{int(val%60):02d}:{int((val-int(val//60)*60-int(val%60))*1000):03d}" if not np.isnan(val) else ''))
    ax.tick_params(axis='x', colors='white')
    ax.tick_params(axis='y', colors='white')
    ax.set_title(f"{driver_code} – Stint Analysis (compound color bars, mean, best lap)", color='white')
    ax.grid(True, alpha=0.3, color='white')
    plt.tight_layout()
    out_file = outdir / f"{driver_code}_stint_analysis.png"
    plt.savefig(out_file, dpi=150, facecolor=fig.get_facecolor())
    plt.close(fig)
    print(f"Saved: {out_file}")
