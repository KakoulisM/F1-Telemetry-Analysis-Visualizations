from pathlib import Path  
import numpy as np  
import pandas as pd  
import matplotlib.pyplot as plt  
from matplotlib.ticker import FuncFormatter  
from matplotlib.patches import Patch  
  
  
def _format_laptime(val, pos=None):  
    """Format seconds as M:SS.mmm for axis labels."""  
    if pd.isna(val):  
        return ""  
    v = float(val)  
    mins = int(v // 60)  
    secs = v % 60  
    return f"{mins}:{secs:06.3f}"  
  
  
def _ensure_laptime_sec(laps: pd.DataFrame) -> pd.DataFrame:  
    """Guarantee LapTimeSec column in seconds."""  
    laps = laps.copy()  
    if "LapTimeSec" in laps.columns:  
        return laps  
    if "LapTime" in laps.columns:  
        try:  
            if not pd.api.types.is_timedelta64_dtype(laps["LapTime"]):  
                laps["LapTime"] = pd.to_timedelta(laps["LapTime"], errors="coerce")  
            laps["LapTimeSec"] = laps["LapTime"].dt.total_seconds()  
            return laps  
        except Exception:  
            pass  
    laps["LapTimeSec"] = pd.to_numeric(laps.get("LapTime", np.nan), errors="coerce")  
    return laps  
  
  
def plot_stint_analysis_top7(  
    laps: pd.DataFrame, output_dir: Path, session_name: str  
):  
    """  
    ONE figure with 1 subplot per driver (stacked vertically).  
  
    - Drivers: top 7 finishers (by Position; fallback = best lap).  
    - Each acceptable lap = one bar.  
    - Bars grouped by stint; all laps in a stint share one colour:  
        SOFT=red, MEDIUM=yellow, HARD=white.  
    - Filtering:  
        * per driver: remove extreme slow laps (median + 120 s),  
        * per stint (two-step):  
            - keep laps <= (stint_mean_initial + 5 s),  
            - recompute mean on that subset and keep laps <= (mean_final + 5 s).  
      → no plotted lap is more than +5 s slower than the final stint mean.  
    - Stint length rules **after** filtering:  
        * stints with < 3 laps are normally not plotted,  
        * BUT if such a stint contains the driver's fastest lap, we still  
          plot the laps that remain so that every driver has a best lap shown.  
        * stints with 3 laps: plotted but NO mean line,  
        * stints with >= 4 laps: plotted with dotted mean line + label.  
    - All subplots share the same x-range → bars have the same visual width.  
    - Y-limits are also shared across drivers → heights are comparable.  
    - Fastest lap for each driver highlighted in lime.  
    """  
  
    output_dir = Path(output_dir)  
    output_dir.mkdir(parents=True, exist_ok=True)  
  
    # --- Basic checks and preparation ---  
    laps = laps.copy()  
    if "Driver" not in laps.columns or "LapNumber" not in laps.columns:  
        print("plot_stint_analysis_top7: laps must have 'Driver' and 'LapNumber' columns.")  
        return  
  
    laps = _ensure_laptime_sec(laps)  
    laps = laps[laps["LapTimeSec"].notna() & laps["Driver"].notna()]  
    if laps.empty:  
        print("plot_stint_analysis_top7: no numeric lap times, nothing to plot.")  
        return  
  
    # Need a stint label  
    if "Stint" not in laps.columns or laps["Stint"].isna().all():  
        print("plot_stint_analysis_top7: no 'Stint' column found; cannot do stint analysis.")  
        return  
  
    # Choose compound column  
    comp_col = None  
    for c in ["StintCompound", "Compound", "FreshTyre"]:  
        if c in laps.columns and laps[c].notna().any():  
            comp_col = c  
            break  
    if comp_col is None:  
        print("plot_stint_analysis_top7: no tyre compound column found.")  
        return  
  
    laps["CompoundNorm"] = laps[comp_col].astype(str).str.upper()  
  
    # --- Determine top 7 drivers ---  
    valid_pos = "Position" in laps.columns and laps["Position"].notna().any()  
    if valid_pos:  
        tmp = (  
            laps[laps["Position"].notna()]  
            .sort_values("Position")  
            .drop_duplicates("Driver")  
        )  
        top_drivers = tmp["Driver"].tolist()[:7]  
    else:  
        best = laps.groupby("Driver")["LapTimeSec"].min().sort_values()  
        top_drivers = best.index.tolist()[:7]  
  
    if not top_drivers:  
        print("plot_stint_analysis_top7: no drivers to plot.")  
        return  
  
    # Restrict dataset to those 7 drivers  
    laps = laps[laps["Driver"].isin(top_drivers)].copy()  
    if laps.empty:  
        print("plot_stint_analysis_top7: no laps for the selected drivers.")  
        return  
  
    # --- Fastest laps per driver (so each driver has a green bar) ---  
    driver_best_times = laps.groupby("Driver")["LapTimeSec"].min()  
    driver_best_laps = {}  
    for drv, best_t in driver_best_times.items():  
        best_rows = laps[  
            (laps["Driver"] == drv) & (laps["LapTimeSec"] == best_t)  
        ]  
        driver_best_laps[drv] = set(best_rows["LapNumber"].dropna().tolist())  
  
    # Colours  
    compound_colors = {"SOFT": "red", "MEDIUM": "yellow", "HARD": "white", "INTERMEDIATE": "darkgreen", "WET": "blue"}  
    palette = plt.cm.tab10(np.linspace(0, 1, len(top_drivers)))  
    driver_colors = {drv: palette[i] for i, drv in enumerate(top_drivers)}  
  
    # --- Create subplots: one per driver ---  
    n = len(top_drivers)  
    fig, axes = plt.subplots(n, 1, figsize=(18, 3.5 * n), sharex=False)  
    if n == 1:  
        axes = [axes]  
  
    fig.patch.set_facecolor("black")  
    for ax in axes:  
        ax.set_facecolor("black")  
  
    # We will force same x/y ranges across drivers  
    global_max_x = 0.0  
    global_min_height = float("inf")  
    global_max_height = 0.0  
  
    # --- Per-driver plotting ---  
    for ax, driver in zip(axes, top_drivers):  
        d_laps = laps[laps["Driver"] == driver].copy()  
        d_laps = d_laps.sort_values("LapNumber")  
  
        if d_laps.empty:  
            ax.text(0.5, 0.5, "No laps", color="white", ha="center", va="center")  
            continue  
  
        # Rough big-outlier removal (median + 120 s) per driver  
        med = d_laps["LapTimeSec"].median()  
        if pd.notna(med):  
            d_laps = d_laps[d_laps["LapTimeSec"] <= med + 120]  
        if d_laps.empty:  
            ax.text(0.5, 0.5, "No laps after filtering", color="white",  
                    ha="center", va="center")  
            continue  
  
        d_laps = d_laps[d_laps["Stint"].notna()]  
        if d_laps.empty:  
            ax.text(0.5, 0.5, "No stints", color="white",  
                    ha="center", va="center")  
            continue  
  
        # Ensure Stint is numeric  
        d_laps["Stint"] = pd.to_numeric(d_laps["Stint"], errors="coerce")  
        d_laps = d_laps[d_laps["Stint"].notna()]  
        if d_laps.empty:  
            ax.text(0.5, 0.5, "No stints", color="white",  
                    ha="center", va="center")  
            continue  
  
        stints = sorted(d_laps["Stint"].unique())  
  
        positions = []  
        lap_numbers = []  
        heights = []  
        colors = []  
        stint_means = []      # list of dicts: start_x, end_x, mean (only for >=4 laps)  
        max_height = 0.0  
        min_height = float("inf")  
  
        # for "Laps: N / COMPOUND" labels  
        stint_centers = {}  
        stint_counts = {}  
        stint_compounds = {}  
  
        current_pos = 1.0  
        best_laps_for_driver = driver_best_laps.get(driver, set())  
  
        for stint in stints:  
            stint_laps = d_laps[d_laps["Stint"] == stint].sort_values("LapNumber")  
            if stint_laps.empty:  
                continue  
  
            # Does this stint contain the driver's best lap?  
            stint_best = stint_laps[  
                stint_laps["LapNumber"].isin(best_laps_for_driver)  
            ]  
            has_best = not stint_best.empty  
  
            # ----- Two-step filter inside stint -----  
            mean_initial = stint_laps["LapTimeSec"].mean()  
            valid = stint_laps[  
                stint_laps["LapTimeSec"] <= mean_initial + 5.0  
            ].copy()  
  
            if valid.empty:  
                if not has_best:  
                    continue  
                # keep at least the best lap of the driver  
                valid = stint_best.copy()  
            else:  
                mean_mid = valid["LapTimeSec"].mean()  
                valid = valid[valid["LapTimeSec"] <= mean_mid + 5.0].copy()  
  
                # ensure best lap is kept even if it was outside the +5 window  
                if has_best:  
                    valid = pd.concat([valid, stint_best]).drop_duplicates()  
  
                if valid.empty:  
                    if not has_best:  
                        continue  
                    valid = stint_best.copy()  
  
            n_valid = len(valid)  
            if n_valid == 0:  
                continue  
  
            # Mean used only if stint has >=4 valid laps  
            mean_final = valid["LapTimeSec"].mean()  
  
            # Stint length rule:  
            # - normally skip stints with < 3 laps  
            # - but if this stint contains the driver's best lap, keep it  
            if n_valid < 3 and not has_best:  
                continue  
  
            # Determine one colour (compound) for the entire stint  
            comp_series = valid["CompoundNorm"].dropna()  
            if comp_series.empty:  
                comp_mode = "UNKNOWN"  
            else:  
                comp_mode = comp_series.mode().iloc[0]  
  
            stint_positions = []  
  
            for _, row in valid.iterrows():  
                pos = current_pos  
                current_pos += 1.0  
  
                positions.append(pos)  
                stint_positions.append(pos)  
  
                lt = float(row["LapTimeSec"])  
                heights.append(lt)  
                max_height = max(max_height, lt)  
                min_height = min(min_height, lt)  
  
                lapnum = row["LapNumber"]  
                lap_numbers.append(int(lapnum) if not pd.isna(lapnum) else None)  
  
                # base colour = compound of the stint  
                face = compound_colors.get(comp_mode, "gray")  
  
                # highlight fastest lap(s) for this driver in lime  
                if lapnum in best_laps_for_driver:  
                    face = "lime"  
  
                colors.append(face)  
  
            if stint_positions:  
                stint_centers[stint] = float(np.mean(stint_positions))  
                stint_counts[stint] = len(stint_positions)  
                stint_compounds[stint] = comp_mode  
  
                # Only stints with >=4 laps contribute a mean line  
                if n_valid >= 4:  
                    stint_means.append(  
                        {  
                            "start": min(stint_positions),  
                            "end": max(stint_positions),  
                            "mean": float(mean_final),  
                        }  
                    )  
  
                current_pos += 1.5  # gap before next stint  
  
        if not positions:  
            ax.text(0.5, 0.5, "No laps after filter / stint length rules",  
                    color="white", ha="center", va="center")  
            continue  
  
        # Draw bars – one bar per lap  
        edge_color = driver_colors[driver]  
        ax.bar(  
            positions,  
            heights,  
            color=colors,  
            edgecolor=edge_color,  
            width=0.8,          # identical width in data units  
            linewidth=0.7,  
            zorder=2,  
        )  
  
        # Track global x and y limits for uniform scaling  
        driver_max_x = max(positions) + 1.0  
        global_max_x = max(global_max_x, driver_max_x)  
        if max_height > 0:  
            global_max_height = max(global_max_height, max_height)  
        if min_height < float("inf"):  
            global_min_height = min(global_min_height, min_height)  
  
        # Draw mean lines for each stint (only those with >=4 laps)  
        for blk in stint_means:  
            ax.hlines(  
                y=blk["mean"],  
                xmin=blk["start"] - 0.4,  
                xmax=blk["end"] + 0.4,  
                colors=edge_color,  
                linestyles="dotted",  
                linewidth=1.6,  
                zorder=3,  
            )  
            ax.text(  
                (blk["start"] + blk["end"]) / 2.0,  
                blk["mean"] + 0.6,  
                f"Mean: {_format_laptime(blk['mean'])}",  
                color="white",  
                fontsize=9,  
                ha="center",  
                va="bottom",  
                fontweight="bold",  
                zorder=4,  
            )  
  
        # "Laps: N / COMPOUND" labels above each (plotted) stint  
        if heights:  
            y_top = max_height * 1.02  
            for stint, center in stint_centers.items():  
                count = stint_counts.get(stint, 0)  
                comp_mode = stint_compounds.get(stint, "UNKNOWN")  
                comp_label = {  
                    "SOFT": "Soft",  
                    "MEDIUM": "Medium",  
                    "HARD": "Hard",  
                    "INTERMEDIATE": "Intermediate",  
                    "WET": "Wet",  
                }.get(comp_mode, comp_mode)  
  
                ax.text(  
                    center,  
                    y_top,  
                    f"Laps: {count}",  
                    ha="center",  
                    va="bottom",  
                    color="white",  
                    fontsize=9,  
                    fontweight="bold",  
                )  
                ax.text(  
                    center,  
                    y_top - 0.5,  
                    comp_label,  
                    ha="center",  
                    va="top",  
                    color="white",  
                    fontsize=8,  
                )  
  
        # X-ticks: show up to ~12 labelled lap numbers  
        n_pos = len(positions)  
        n_ticks = min(n_pos, 12)  
        idxs = np.linspace(0, n_pos - 1, n_ticks, dtype=int)  
        tick_pos = [positions[i] for i in idxs]  
        tick_labels = [  
            str(lap_numbers[i]) if lap_numbers[i] is not None else ""  
            for i in idxs  
        ]  
        ax.set_xticks(tick_pos)  
        ax.set_xticklabels(  
            tick_labels,  
            color="white",  
            rotation=45,  
            ha="right",  
        )  
  
        # Y-axis formatting  
        ax.set_ylabel("Lap Time", color="white", fontsize=11)  
        ax.yaxis.set_major_formatter(FuncFormatter(_format_laptime))  
        ax.tick_params(axis="y", colors="white")  
        ax.tick_params(axis="x", colors="white")  
  
        # Title per driver  
        ax.set_title(driver, color="white", fontsize=12, fontweight="bold")  
  
        # Grid  
        ax.grid(True, axis="y", alpha=0.3, color="white")  
  
    # --- Make all bars the same visual size: common x/y limits ---  
    if global_max_x > 0:  
        for ax in axes:  
            ax.set_xlim(0, global_max_x)  
  
    if global_max_height > 0 and global_min_height < float("inf"):  
        padding = 1.0  # seconds  
        y_min = max(0, global_min_height - padding)  
        y_max = global_max_height + padding  
        for ax in axes:  
            ax.set_ylim(y_min, y_max)  
  
    # Bottom x-label  
    axes[-1].set_xlabel("Lap Number (grouped by stint)", color="white", fontsize=12)  
  
    # Figure title  
    fig.suptitle(  
        f"{session_name}\nStint-by-Stint Analysis (Top 7 Drivers)",  
        color="white",  
        fontsize=16,  
        fontweight="bold",  
        y=0.99,  
    )  
  
    # Legend for compounds + best lap  
    legend_elems = [  
        Patch(facecolor="red", edgecolor="red", label="Soft"),  
        Patch(facecolor="yellow", edgecolor="yellow", label="Medium"),  
        Patch(facecolor="white", edgecolor="white", label="Hard"),  
        Patch(facecolor="pink", edgecolor="pink", label="Intermediate"),  
        Patch(facecolor="blue", edgecolor="blue", label="Wet"),  
        Patch(facecolor="lime", edgecolor="lime", label="Best Lap (per driver)"),  
    ]  
    fig.legend(  
        handles=legend_elems,  
        loc="upper right",  
        frameon=True,  
        facecolor="black",  
        edgecolor="white",  
        labelcolor="white",  
        fontsize=9,  
    )  
  
    plt.tight_layout(rect=[0.03, 0.03, 0.97, 0.95])  
    out_file = output_dir / "stint_analysis_top7.png"  
    plt.savefig(out_file, dpi=150, facecolor=fig.get_facecolor())  
    plt.close(fig)  
    print(f"Saved: {out_file}")  