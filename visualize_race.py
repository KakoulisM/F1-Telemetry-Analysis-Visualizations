
from pathlib import Path
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
from matplotlib.ticker import FuncFormatter



def plot_csv_table_as_png(csv_path, png_path, title=None, max_rows=30):
    import pandas as pd
    import matplotlib.pyplot as plt
    df = pd.read_csv(csv_path)
    # Limit rows for readability
    if len(df) > max_rows:
        df = df.head(max_rows)
    fig, ax = plt.subplots(figsize=(min(20, 2+len(df.columns)*2), min(1+len(df)*0.5, 18)))
    ax.axis('off')
    table = ax.table(cellText=df.values,
                     colLabels=df.columns,
                     loc='center',
                     cellLoc='center')
    table.auto_set_font_size(False)
    table.set_fontsize(11)
    table.scale(1.2, 1.2)
    if title:
        plt.title(title, fontsize=16, pad=20)
    plt.tight_layout()
    plt.savefig(png_path, dpi=200, bbox_inches='tight')
    plt.close(fig)
    print(f"Saved table PNG: {png_path}")

def plot_classifier_outputs_as_png(year, event, session_type):
    # Plot classifier output CSVs as PNG tables from telemetry_out
    import os
    classifier_dir = os.path.join('telemetry_out', str(year), event, session_type)
    files = [
        ('qual_flags_summary.csv', 'Driver Summary'),
        ('qual_flags_session_stats.csv', 'Session Statistics'),
        ('qual_flags_race_sim_median.csv', 'Race Sim Median'),
    ]
    for fname, title in files:
        fpath = os.path.join(classifier_dir, fname)
        png_path = os.path.join(classifier_dir, fname.replace('.csv', '.png'))
        if os.path.exists(fpath):
            plot_csv_table_as_png(fpath, png_path, title=title)
        else:
            print(f"[WARN] Classifier output not found: {fpath}")
            
def add_corner_markers(ax, lap_length_m, outdir, session=None, year=None):
    """
    Load circuit_corners.csv, rescale its Distance column to the same
    0..lap_length_m axis as the speed plot, and draw vertical dotted lines
    with turn numbers on the x-axis.
    """
    candidates = []
    # Use session and year to build telemetry_out path if provided
    if session and year:
        # session: e.g. Australian_Grand_Prix_Race or Australian_Grand_Prix_Practice_1
        session_parts = session.split('_')
        # Map session name to strict directory name
        # Handles both FP1-style and Practice_1-style session suffixes
        session_suffixes = [
            ('Practice_1', 'Practice_1'),
            ('Practice_2', 'Practice_2'),
            ('Practice_3', 'Practice_3'),
            ('FP1', 'Practice_1'),
            ('FP2', 'Practice_2'),
            ('FP3', 'Practice_3'),
            ('Sprint_Qualifying', 'Sprint_Qualifying'),
            ('SprintQualifying', 'Sprint_Qualifying'),
            ('SQ', 'Sprint_Qualifying'),
            ('Sprint_Race', 'Sprint'),
            ('SprintRace', 'Sprint'),
            ('Sprint_Shootout', 'Sprint_Shootout'),
            ('SprintShootout', 'Sprint_Shootout'),
            ('SS', 'Sprint_Shootout'),
            ('Sprint', 'Sprint'),
            ('S', 'Sprint'),
            ('Qualifying', 'Qualifying'),
            ('Q', 'Qualifying'),
            ('Race', 'Race'),
            ('R', 'Race'),
        ]
        event = session
        session_type = ''
        for suffix, mapped in session_suffixes:
            if session.endswith('_' + suffix):
                event = session[:-(len(suffix) + 1)]
                session_type = mapped
                break
        strict_path = Path("telemetry_out") / str(year) / event / session_type / "circuit_corners.csv"
        print("[DEBUG] strict corner file path:", strict_path)
        if not strict_path.exists():
            print(f"[ERROR] Strict mapping: {strict_path} not found. Skipping corners.")
            return
        corner_file = strict_path

    print(f"[DEBUG] using corner file: {corner_file}")
    corners = pd.read_csv(corner_file)

    if "Distance" not in corners.columns or "Number" not in corners.columns:
        print("[DEBUG] corner file missing Distance or Number columns")
        return

    dist_raw = corners["Distance"].astype(float).to_numpy()
    corner_nums = corners["Number"].astype(int).to_numpy()

    if dist_raw.size == 0 or not np.isfinite(dist_raw).any():
        print("[DEBUG] empty/invalid corner distances")
        return

    maxd_raw = float(np.nanmax(dist_raw))
    if maxd_raw <= 0:
        print("[DEBUG] max distance <= 0, skipping corners")
        return

    # Rescale raw distances (0..maxd_raw) to plot axis (0..lap_length_m)
    scale = lap_length_m / maxd_raw
    corner_pos = dist_raw * scale

    # Only keep corners inside current x-limits
    xmin, xmax = ax.get_xlim()
    mask = (corner_pos >= xmin) & (corner_pos <= xmax)
    corner_pos = corner_pos[mask]
    corner_nums = corner_nums[mask]

    if corner_pos.size == 0:
        print("[DEBUG] no corners within x-limits", xmin, xmax)
        return

    # Vertical dotted lines
    for d in corner_pos:
        ax.axvline(
            d,
            color="gray",
            alpha=0.35,
            linestyle=":",
            linewidth=0.7,
            zorder=0,
        )

    # Replace numeric ticks with turn labels
    ax.set_xticks(corner_pos)
    ax.set_xticklabels([f"T{int(n)}" for n in corner_nums])
    ax.set_xlabel("Turn")
def plot_driver_stint_lap_progression_continuous(laps: pd.DataFrame, driver_code: str, outdir: Path):
    """
    Plot lap time progression per stint for a single driver.
    X-axis: lap number
    Y-axis: lap time (mm:ss.SSS)
    Outlier laps (in/out laps, SC laps) are excluded via IQR filtering.
    Each stint gets a distinct color; compound shown in legend.
    """
    import matplotlib.pyplot as plt
    from matplotlib.ticker import FuncFormatter
    import numpy as np
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

    # --- Filter outlier laps (in/out laps, SC laps, etc.) ---
    # Drop laps explicitly flagged as pit in/out if columns exist.
    # NOTE: These columns are timedeltas saved to CSV as strings (e.g. "NaT").
    # Using pd.to_timedelta(errors='coerce') converts the string "NaT" to actual NaT
    # so .isna() correctly identifies laps WITHOUT a pit event.
    for pit_col in ("PitInTime", "PitOutTime"):
        if pit_col in df.columns:
            df = df[pd.to_timedelta(df[pit_col], errors='coerce').isna()]
    # IQR-based removal per driver: anything above Q3 + 1.5*IQR is an outlier
    q1 = df["LapTimeSec"].quantile(0.25)
    q3 = df["LapTimeSec"].quantile(0.75)
    iqr = q3 - q1
    upper_bound = q3 + 1.5 * iqr
    df = df[df["LapTimeSec"] <= upper_bound]
    if df.empty:
        print(f"No laps remaining after outlier filter for driver {driver_code}")
        return

    df = df.sort_values(["Stint", "LapNumber"])

    # Distinct color per stint (cycling through a palette so same-compound stints differ)
    stint_palette = [
        "#e6194b", "#3cb44b", "#4363d8", "#f58231", "#911eb4",
        "#42d4f4", "#f032e6", "#bfef45", "#fabed4", "#469990",
    ]
    compound_colors = {
        "HARD": "black", "MEDIUM": "#d4b800", "SOFT": "red",
        "INTERMEDIATE": "darkgreen", "WET": "blue",
    }

    stints = sorted(df["Stint"].dropna().unique())
    fig, ax = plt.subplots(figsize=(12, 6))

    for i, stint in enumerate(stints):
        stint_df = df[df["Stint"] == stint].sort_values("LapNumber").copy()
        if stint_df.empty:
            continue
        x = stint_df["LapNumber"].astype(int)
        y = stint_df["LapTimeSec"]

        comp_series = (
            stint_df.get("StintCompound", stint_df.get("Compound", pd.Series()))
            .dropna().astype(str).str.upper()
        )
        compound = comp_series.mode().iloc[0] if not comp_series.empty else "UNKNOWN"

        # Use compound color as the base, but vary alpha/linestyle per stint
        # so that multiple stints on same compound are distinguishable
        base_color = compound_colors.get(compound, stint_palette[i % len(stint_palette)])
        # Alternate linestyle for repeated compounds
        linestyles = ["-", "--", "-.", ":"]
        ls = linestyles[i % len(linestyles)]

        label = f"Stint {int(stint) if pd.notna(stint) else '?'} ({compound})"
        ax.plot(x, y, marker="o", linestyle=ls, color=base_color, label=label, linewidth=1.8)

    ax.set_xlabel("Lap Number")
    ax.set_ylabel("Lap time (mm:ss.SSS)")
    ax.yaxis.set_major_formatter(FuncFormatter(format_laptime))
    ax.set_title(f"{driver_code} – Lap time progression by stint")
    ax.grid(True, alpha=0.3)
    handles, labels = ax.get_legend_handles_labels()
    uniq = dict(zip(labels, handles))
    ax.legend(uniq.values(), uniq.keys())
    plt.tight_layout()
    out_file = outdir / f"{driver_code}_lap_progression.png"
    plt.savefig(out_file, dpi=150)
    plt.close(fig)
    print(f"Saved: {out_file}")
def plot_all_drivers_stint_lap_progression_continuous(laps, outdir):
    drivers = sorted(laps["Driver"].dropna().unique())
    for drv in drivers:
        plot_driver_stint_lap_progression_continuous(laps, drv, outdir)


# ---------------------------------------------------------------------------
# Sector-time table (per driver) – FP3 / Qualifying
# ---------------------------------------------------------------------------
_COMPOUND_COLORS = {
    "HARD": "#CCCCCC",
    "MEDIUM": "#FFE066",
    "SOFT": "#FF6B6B",
    "INTERMEDIATE": "#8BC34A",
    "WET": "#64B5F6",
    "UNKNOWN": "#EEEEEE",
}


def _fmt_sector(val):
    """Format a sector time value (seconds float or timedelta) to mm:ss.sss string."""
    try:
        if pd.isna(val):
            return ""
        if hasattr(val, "total_seconds"):
            secs = val.total_seconds()
        else:
            secs = float(val)
        mins = int(secs // 60)
        remaining = secs - mins * 60
        if mins:
            return f"{mins}:{remaining:06.3f}"
        return f"{remaining:.3f}"
    except Exception:
        return ""


def plot_driver_sector_table(laps: pd.DataFrame, driver_code: str, outdir: Path):
    """
    Render a PNG table for one driver showing each lap's tyre compound
    and Sector 1 / Sector 2 / Sector 3 / Lap Time.
    Personal-best sector times are highlighted in a lighter shade.
    """
    import matplotlib.pyplot as plt
    import matplotlib.patches as mpatches
    from matplotlib.colors import to_rgba

    df = laps[laps["Driver"] == driver_code].copy()
    # Keep only laps with at least one sector time
    sector_cols_present = [c for c in ("Sector1TimeSec", "Sector2TimeSec", "Sector3TimeSec") if c in df.columns]
    if not sector_cols_present:
        # Try timedelta columns
        for base, sec_col in [("Sector1Time", "Sector1TimeSec"),
                               ("Sector2Time", "Sector2TimeSec"),
                               ("Sector3Time", "Sector3TimeSec")]:
            if base in df.columns:
                df[sec_col] = pd.to_timedelta(df[base], errors="coerce").dt.total_seconds()
                sector_cols_present.append(sec_col)

    if not sector_cols_present:
        print(f"  No sector time columns for {driver_code}, skipping sector table")
        return

    df = df.sort_values("LapNumber").reset_index(drop=True)

    # Resolve compound column
    comp_col = next((c for c in ("Compound", "StintCompound") if c in df.columns), None)

    # Personal bests per sector
    pb = {}
    for sec_col in ("Sector1TimeSec", "Sector2TimeSec", "Sector3TimeSec"):
        if sec_col in df.columns:
            valid = df[sec_col].dropna()
            pb[sec_col] = valid.min() if not valid.empty else None

    # Build table rows
    col_headers = ["Lap", "Compound", "S1", "S2", "S3", "Lap Time"]
    rows = []
    row_compounds = []
    pb_mask = {h: [] for h in col_headers}

    for _, row in df.iterrows():
        compound = "UNKNOWN"
        if comp_col and pd.notna(row.get(comp_col)):
            compound = str(row[comp_col]).upper()

        lap_num = int(row["LapNumber"]) if "LapNumber" in row and pd.notna(row["LapNumber"]) else "?"

        def sec_val(col):
            if col in df.columns and pd.notna(row.get(col)):
                return row[col]
            return None

        s1 = sec_val("Sector1TimeSec")
        s2 = sec_val("Sector2TimeSec")
        s3 = sec_val("Sector3TimeSec")
        lt_sec = sec_val("LapTimeSec") if "LapTimeSec" in df.columns else None
        if lt_sec is None and "LapTime" in df.columns and pd.notna(row.get("LapTime")):
            try:
                lt_sec = pd.to_timedelta(row["LapTime"], errors="coerce").total_seconds()
            except Exception:
                lt_sec = None

        rows.append([
            str(lap_num),
            compound,
            _fmt_sector(s1),
            _fmt_sector(s2),
            _fmt_sector(s3),
            _fmt_sector(lt_sec),
        ])
        row_compounds.append(compound)

        # Track personal bests for highlight
        pb_mask["Lap"].append(False)
        pb_mask["Compound"].append(False)
        pb_mask["S1"].append(pb.get("Sector1TimeSec") is not None and s1 is not None and abs(s1 - pb["Sector1TimeSec"]) < 0.001)
        pb_mask["S2"].append(pb.get("Sector2TimeSec") is not None and s2 is not None and abs(s2 - pb["Sector2TimeSec"]) < 0.001)
        pb_mask["S3"].append(pb.get("Sector3TimeSec") is not None and s3 is not None and abs(s3 - pb["Sector3TimeSec"]) < 0.001)
        pb_mask["Lap Time"].append(False)

    if not rows:
        print(f"  No lap rows for {driver_code}")
        return

    n_rows = len(rows)
    n_cols = len(col_headers)
    row_height = 0.38
    col_width = 1.6
    fig_w = n_cols * col_width
    fig_h = (n_rows + 1) * row_height + 0.6  # +1 for header row, +0.6 title

    fig, ax = plt.subplots(figsize=(fig_w, fig_h))
    ax.axis("off")
    fig.patch.set_facecolor("#1C1C1E")

    # Draw header
    header_bg = "#2C2C2E"
    header_fg = "white"
    col_xs = [i * col_width / fig_w for i in range(n_cols)]
    col_w_norm = col_width / fig_w
    row_h_norm = row_height / fig_h

    # title
    ax.text(0.5, 1.0 - 0.3 / fig_h, f"{driver_code}  –  Sector Times",
            transform=ax.transAxes, ha="center", va="top",
            fontsize=13, fontweight="bold", color="white", fontfamily="monospace")

    def draw_cell(ax, col_idx, row_idx, text, bg, fg, bold=False):
        x = col_idx * col_w_norm
        y = 1.0 - (0.5 / fig_h) - (row_idx + 1) * row_h_norm
        rect = mpatches.FancyBboxPatch(
            (x + 0.002, y + 0.004), col_w_norm - 0.004, row_h_norm - 0.008,
            boxstyle="round,pad=0.005",
            linewidth=0, facecolor=bg, transform=ax.transAxes, clip_on=False
        )
        ax.add_patch(rect)
        ax.text(x + col_w_norm / 2, y + row_h_norm / 2, text,
                transform=ax.transAxes, ha="center", va="center",
                fontsize=9, color=fg,
                fontweight="bold" if bold else "normal",
                fontfamily="monospace")

    # Header row
    for ci, hdr in enumerate(col_headers):
        draw_cell(ax, ci, 0, hdr, header_bg, header_fg, bold=True)

    # Data rows
    for ri, (row_data, compound) in enumerate(zip(rows, row_compounds)):
        base_bg = _COMPOUND_COLORS.get(compound, _COMPOUND_COLORS["UNKNOWN"])
        # Alternate slight shade for readability
        row_bg = base_bg if ri % 2 == 0 else tuple(max(0, c - 0.06) for c in to_rgba(base_bg)[:3]) + (1.0,)
        for ci, (hdr, cell_text) in enumerate(zip(col_headers, row_data)):
            is_pb = pb_mask[hdr][ri]
            cell_bg = "#A8E6CF" if is_pb else row_bg  # mint green highlight for PB sectors
            cell_fg = "#1C1C1E"
            draw_cell(ax, ci, ri + 1, cell_text, cell_bg, cell_fg, bold=is_pb)

    plt.subplots_adjust(left=0, right=1, top=1, bottom=0)
    out_file = outdir / f"{driver_code}_sector_table.png"
    plt.savefig(out_file, dpi=150, facecolor=fig.get_facecolor())
    plt.close(fig)
    print(f"  Saved sector table: {out_file}")


def plot_all_drivers_sector_table(laps: pd.DataFrame, outdir: Path):
    """Generate sector table PNGs for all drivers in the session."""
    outdir.mkdir(parents=True, exist_ok=True)
    drivers = sorted(laps["Driver"].dropna().unique())
    for drv in drivers:
        plot_driver_sector_table(laps, drv, outdir)


def _load_qual_flags(year, event_clean, session_type_clean) -> pd.DataFrame:
    """Load qual_flags.csv for the given session, returning empty DataFrame if not found."""
    qf_path = Path(f"telemetry_out/{year}/{event_clean}/{session_type_clean}/qual_flags.csv")
    if qf_path.exists():
        return pd.read_csv(qf_path)
    return pd.DataFrame()


def _filter_laps_by_label(laps: pd.DataFrame, qual_flags: pd.DataFrame, label: str) -> pd.DataFrame:
    """Return rows of laps whose (Driver, LapNumber) appear in qual_flags with the given label.

    For 'race_sim': uses improved_label column exclusively (most accurate).
    For 'qual': uses predicted/heuristic_label columns since improved_label is only set for
    race-sim stints and qual laps are left as 'not_race_sim' in that column.
    """
    if qual_flags.empty:
        return pd.DataFrame(columns=laps.columns)

    if label == 'race_sim':
        if 'improved_label' not in qual_flags.columns:
            return pd.DataFrame(columns=laps.columns)
        mask = qual_flags['improved_label'] == 'race_sim'
    else:
        # For qual, check predicted then heuristic_label — improved_label doesn't track qual
        if 'predicted' in qual_flags.columns:
            mask = qual_flags['predicted'] == label
        elif 'heuristic_label' in qual_flags.columns:
            mask = qual_flags['heuristic_label'] == label
        else:
            return pd.DataFrame(columns=laps.columns)

    flagged = qual_flags[mask][['Driver', 'LapNumber']].drop_duplicates()
    flagged['LapNumber'] = pd.to_numeric(flagged['LapNumber'], errors='coerce')
    laps = laps.copy()
    laps['LapNumber'] = pd.to_numeric(laps['LapNumber'], errors='coerce')
    return laps.merge(flagged, on=['Driver', 'LapNumber'], how='inner')


def plot_best_lap_summary_table(laps_filtered: pd.DataFrame, title: str, outpath: Path):
    """
    Render a single PNG table showing the best lap (minimum LapTime) for each driver.
    Columns: Driver | Compound | S1 | S2 | S3 | Lap Time
    """
    import matplotlib.pyplot as plt

    if laps_filtered.empty:
        return

    # Resolve sector seconds columns
    df = laps_filtered.copy()
    for base, sec_col in [("Sector1Time", "Sector1TimeSec"),
                          ("Sector2Time", "Sector2TimeSec"),
                          ("Sector3Time", "Sector3TimeSec")]:
        if sec_col not in df.columns and base in df.columns:
            df[sec_col] = pd.to_timedelta(df[base], errors="coerce").dt.total_seconds()
    if "LapTimeSec" not in df.columns and "LapTime" in df.columns:
        df["LapTimeSec"] = pd.to_timedelta(df["LapTime"], errors="coerce").dt.total_seconds()

    # Pick best lap per driver (lowest LapTimeSec)
    if "LapTimeSec" in df.columns:
        idx = df.groupby("Driver")["LapTimeSec"].idxmin().dropna()
        best = df.loc[idx].sort_values("LapTimeSec").reset_index(drop=True)
    else:
        best = df.sort_values("Driver").reset_index(drop=True)

    comp_col = next((c for c in ("Compound", "StintCompound") if c in best.columns), None)

    def fmt_sec(v):
        try:
            v = float(v)
            m, s = divmod(v, 60)
            return f"{int(m)}:{s:06.3f}"
        except Exception:
            return "—"

    rows = []
    for _, row in best.iterrows():
        compound = str(row[comp_col]).upper() if comp_col and pd.notna(row.get(comp_col)) else "—"
        s1 = fmt_sec(row.get("Sector1TimeSec")) if "Sector1TimeSec" in best.columns and pd.notna(row.get("Sector1TimeSec")) else "—"
        s2 = fmt_sec(row.get("Sector2TimeSec")) if "Sector2TimeSec" in best.columns and pd.notna(row.get("Sector2TimeSec")) else "—"
        s3 = fmt_sec(row.get("Sector3TimeSec")) if "Sector3TimeSec" in best.columns and pd.notna(row.get("Sector3TimeSec")) else "—"
        lt = fmt_sec(row.get("LapTimeSec")) if "LapTimeSec" in best.columns and pd.notna(row.get("LapTimeSec")) else "—"
        rows.append([row["Driver"], compound, s1, s2, s3, lt])

    col_headers = ["Driver", "Compound", "S1", "S2", "S3", "Lap Time"]
    n_rows = len(rows)
    fig_h = max(2.5, 0.45 * n_rows + 1.2)
    fig, ax = plt.subplots(figsize=(10, fig_h))
    fig.patch.set_facecolor("#1a1a2e")
    ax.set_facecolor("#1a1a2e")
    ax.axis("off")

    table = ax.table(
        cellText=rows,
        colLabels=col_headers,
        loc="center",
        cellLoc="center",
    )
    table.auto_set_font_size(False)
    table.set_fontsize(10)
    table.scale(1.2, 1.6)

    for (r, c), cell in table.get_celld().items():
        cell.set_edgecolor("#444466")
        if r == 0:
            cell.set_facecolor("#2e2e4e")
            cell.set_text_props(color="white", fontweight="bold")
        else:
            cell.set_facecolor("#16213e" if r % 2 == 0 else "#0f3460")
            cell.set_text_props(color="white")

    ax.set_title(title, color="white", fontsize=13, pad=12, fontweight="bold")
    plt.tight_layout()
    outpath.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(outpath, dpi=150, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)
    print(f"  Saved summary table: {outpath}")


def plot_quali_tables(laps: pd.DataFrame, year, event_clean, session_type_clean, output_dir: Path):
    """
    Generate per-driver and summary qualifying-lap tables for FP2/FP3.
    Uses qual_flags.csv improved_label=='qual' to filter laps.
    """
    qual_flags = _load_qual_flags(year, event_clean, session_type_clean)
    if qual_flags.empty:
        print(f"  [quali_tables] No qual_flags.csv found for {session_type_clean}, skipping")
        return
    filtered = _filter_laps_by_label(laps, qual_flags, 'qual')
    if filtered.empty:
        print(f"  [quali_tables] No qualifying laps detected, skipping")
        return

    table_dir = output_dir / 'quali_tables'
    table_dir.mkdir(parents=True, exist_ok=True)
    drivers = sorted(filtered["Driver"].dropna().unique())
    for drv in drivers:
        plot_driver_sector_table(filtered, drv, table_dir)
    # Best-per-driver summary
    plot_best_lap_summary_table(filtered, "Best Qualifying Lap – All Drivers", table_dir / "_summary_best_quali.png")
    print(f"  [quali_tables] Generated for {len(drivers)} drivers -> {table_dir}")


def plot_race_sim_tables(laps: pd.DataFrame, year, event_clean, session_type_clean, output_dir: Path):
    """
    Generate per-driver and summary race-simulation-lap tables for FP2/FP3.
    Uses qual_flags.csv improved_label=='race_sim' to filter laps.
    """
    qual_flags = _load_qual_flags(year, event_clean, session_type_clean)
    if qual_flags.empty:
        print(f"  [race_sim_tables] No qual_flags.csv found for {session_type_clean}, skipping")
        return
    filtered = _filter_laps_by_label(laps, qual_flags, 'race_sim')
    if filtered.empty:
        print(f"  [race_sim_tables] No race simulation laps detected, skipping")
        return

    table_dir = output_dir / 'race_sim_tables'
    table_dir.mkdir(parents=True, exist_ok=True)
    drivers = sorted(filtered["Driver"].dropna().unique())
    for drv in drivers:
        plot_driver_sector_table(filtered, drv, table_dir)
    # Best-per-driver summary
    plot_best_lap_summary_table(filtered, "Best Race Sim Lap – All Drivers", table_dir / "_summary_best_race_sim.png")
    print(f"  [race_sim_tables] Generated for {len(drivers)} drivers -> {table_dir}")


def plot_all_stints_lap_time(laps, output_dir):
    """
    Plot all tyre stints for every driver in the same plot, color-coded by compound.
    Black: Hard, Yellow: Medium, Red: Soft
    """
    import matplotlib.pyplot as plt
    import seaborn as sns
    compound_colors = {'Hard': 'black', 'Medium': 'yellow', 'Soft': 'red'}
    fig, ax = plt.subplots(figsize=(16, 8))
    drivers = pd.unique(laps['Driver'].dropna())
    for driver in drivers:
        driver_laps = laps[laps['Driver'] == driver].sort_values('LapNumber')
        if 'Stint' in driver_laps.columns and 'StintCompound' in driver_laps.columns:
            stints = driver_laps.groupby('Stint')
            for stint, sdf in stints:
                compound = sdf['StintCompound'].dropna().astype(str).mode().iloc[0] if sdf['StintCompound'].notna().any() else 'Unknown'
                color = compound_colors.get(compound, 'gray')
                ax.plot(sdf['LapNumber'], sdf['LapTimeSec'], marker='o', label=f'{driver} {compound}', color=color, alpha=0.7)
    ax.set_xlabel('Lap Number')
    ax.set_ylabel('Lap Time (s)')
    ax.set_title('Lap Time Progression by Tyre Stint (All Drivers)')
    ax.grid(True, alpha=0.3)
    handles, labels = ax.get_legend_handles_labels()
    by_label = dict(zip(labels, handles))
    ax.legend(by_label.values(), by_label.keys(), loc='upper right', fontsize=8)
    plt.tight_layout()
    out_file = output_dir / 'all_stints_lap_time.png'
    plt.savefig(out_file, dpi=150)
    plt.close(fig)
    print(f"Saved all stints lap time plot: {out_file}")
def plot_driver_stints_lap_time(laps, output_dir):
    """
    For each driver, plot all tyre stints in lap_progression folder, splitting into multiple plots if >20 laps.
    Black: Hard, Yellow: Medium, Red: Soft
    """
    import matplotlib.pyplot as plt
    import seaborn as sns
    compound_colors = {'Hard': 'black', 'Medium': 'yellow', 'Soft': 'red'}
    driver_dir = output_dir / 'lap_progression'
    driver_dir.mkdir(parents=True, exist_ok=True)
    drivers = pd.unique(laps['Driver'].dropna())
    for driver in drivers:
        driver_laps = laps[laps['Driver'] == driver].sort_values('LapNumber')
        lap_chunks = [driver_laps.iloc[i:i+20] for i in range(0, len(driver_laps), 20)]
        for chunk_idx, chunk in enumerate(lap_chunks, 1):
            fig, ax = plt.subplots(figsize=(14, 6))
            if 'Stint' in chunk.columns and 'StintCompound' in chunk.columns:
                stints = chunk.groupby('Stint')
                for stint, sdf in stints:
                    compound = sdf['StintCompound'].dropna().astype(str).mode().iloc[0] if sdf['StintCompound'].notna().any() else 'Unknown'
                    color = compound_colors.get(compound, 'gray')
                    ax.plot(sdf['LapNumber'], sdf['LapTimeSec'], marker='o', label=f'Stint {stint} ({compound})', color=color, alpha=0.7)
            ax.set_xlabel('Lap Number')
            ax.set_ylabel('Lap Time (s)')
            ax.set_title(f'{driver} - Lap Time Progression (Laps {chunk["LapNumber"].min()}-{chunk["LapNumber"].max()})')
            ax.grid(True, alpha=0.3)
            handles, labels = ax.get_legend_handles_labels()
            by_label = dict(zip(labels, handles))
            ax.legend(by_label.values(), by_label.keys(), loc='upper right', fontsize=8)
            plt.tight_layout()
            out_file = driver_dir / f'{driver}_lap_progression_stints_{chunk_idx}.png'
            plt.savefig(out_file, dpi=150)
            plt.close(fig)
            print(f"Saved driver stints lap time plot: {out_file}")
#!/usr/bin/env python3  
"""  
Race visualizer wrapper  
Loads lap CSV files for a specific race session from `telemetry_out/`  
and invokes the plotting methods in `visualize_testing.py` (TestingVisualizer).  
  
Usage:  
    python visualize_race.py --session Bahrain_Grand_Prix_Race --year 2025 --source local  
"""  
  
import argparse  
from pathlib import Path  
import json  
import sys  
  
import logging
import seaborn as sns
from matplotlib.ticker import FuncFormatter
  
import pandas as pd  
import re  
import numpy as np  
import matplotlib.pyplot as plt  
  

# --- Visualization Functions Copied from visualize_testing.py ---
def format_laptime(seconds, pos=None):
    if pd.isna(seconds):
        return "N/A"
    try:
        m, s = divmod(float(seconds), 60)
        return f"{int(m):02d}:{s:06.3f}"
    except Exception:
        return str(seconds)

def infer_stint_compound(df):
    for col in ["StintCompound", "Compound", "FreshTyre"]:
        if col in df.columns and df[col].notna().any():
            return df[col].dropna().iloc[0]
    return "Unknown"

def get_stint_color_map(stints):
    base_colors = list(plt.cm.tab10.colors)
    return {st: base_colors[i % len(base_colors)] for i, st in enumerate(stints)}

def format_time_of_day(row):
    if "TimeOfDay" in row and pd.notna(row["TimeOfDay"]):
        try:
            t = pd.to_datetime(row["TimeOfDay"])
            return t.strftime("%H:%M:%S")
        except Exception:
            return str(row["TimeOfDay"])
    return ""

def plot_best_lap_comparison(session_name, output_dir, laps):
    print("[VIZ 2/8] Best lap comparison...")
    valid_laps = laps[laps['LapTime'].notna()].copy()
    valid_laps['LapTimeSec'] = valid_laps['LapTime'].dt.total_seconds()
    best_laps = valid_laps.groupby('Driver')['LapTimeSec'].min().sort_values()
    fig, ax = plt.subplots(figsize=(12, 8))
    if best_laps.empty:
        print('  No valid best laps to plot; skipping best lap comparison.')
        return
    fastest = best_laps.values.min()
    deltas = best_laps.values - fastest
    colors = plt.cm.RdYlGn_r(np.linspace(0.2, 0.8, len(best_laps)))
    bars = ax.barh(range(len(best_laps)), deltas, color=colors)
    ax.set_yticks(range(len(best_laps)))
    ax.set_yticklabels(best_laps.index)
    ax.set_xlabel('Delta to Fastest (s)', fontsize=12)
    ax.set_title(f'{session_name}\nBest Lap Times (Δ to fastest)', fontsize=14, fontweight='bold')
    ax.invert_yaxis()
    ax.grid(True, axis='x', alpha=0.3)
    for i, delta in enumerate(deltas):
        label = f'+{delta:.3f}s'
        ax.text(delta + 0.01, i, label, va='center', fontsize=9)
    plt.tight_layout()
    output_file = Path(output_dir) / '02_best_laps.png'
    plt.savefig(output_file, dpi=300, bbox_inches='tight')
    plt.close()
    print(f"  Saved: {output_file}")

def plot_best_two_overlay(session_name, output_dir, laps, telemetry_traces=None):
    # ...full function code from visualize_testing.py...
    pass

def plot_sector_performance(session_name, output_dir, laps):
    print("[VIZ 3/8] Sector performance...")
    valid_laps = laps[
        laps['Sector1Time'].notna() & 
        laps['Sector2Time'].notna() & 
        laps['Sector3Time'].notna()
    ].copy()
    if len(valid_laps) == 0:
        print("No sector data available, skipping...")
        return
    sector_data = []
    for driver in valid_laps['Driver'].unique():
        driver_laps = valid_laps[valid_laps['Driver'] == driver]
        sector_data.append({
            'Driver': driver,
            'Sector1': driver_laps['Sector1Time'].dt.total_seconds().min(),
            'Sector2': driver_laps['Sector2Time'].dt.total_seconds().min(),
            'Sector3': driver_laps['Sector3Time'].dt.total_seconds().min()
        })
    sector_df = pd.DataFrame(sector_data).set_index('Driver')
    sector_df['Total'] = sector_df.sum(axis=1)
    sector_df = sector_df.sort_values('Total').head(5)  # Only top 5 drivers

    # Reverse order so best driver is at the top
    sector_df = sector_df.iloc[::-1]
    fig, ax = plt.subplots(figsize=(10, 7))
    sector_names = ['Sector1', 'Sector2', 'Sector3']
    y = np.arange(len(sector_df))
    width = 0.25
    for i, sector in enumerate(sector_names):
        bars = ax.barh(y + (i - 1) * width, sector_df[sector], height=width, label=f'Sector {i+1}', alpha=0.8)
        for idx, bar in enumerate(bars):
            time_val = sector_df[sector].iloc[idx]
            seconds = int(time_val)
            milliseconds = int((time_val - seconds) * 1000)
            label = f"{seconds}:{milliseconds:03d}"
            ax.text(bar.get_width() + 0.02, bar.get_y() + bar.get_height()/2,
                    label, va='center', ha='left', fontsize=11, color=bar.get_facecolor())
    ax.set_yticks(y)
    ax.set_yticklabels(sector_df.index, fontsize=12)
    ax.set_xlabel('Best Sector Time (seconds)', fontsize=12)
    ax.set_title(f'{session_name}\nTop 5 Drivers - Best Sector Times', fontsize=14, fontweight='bold')
    ax.legend()
    ax.grid(True, axis='x', alpha=0.3)
    plt.tight_layout()
    output_file = Path(output_dir) / '03_sector_performance.png'
    plt.savefig(output_file, dpi=300, bbox_inches='tight')
    plt.close()
    print(f"  Saved: {output_file}")

def plot_speed_comparison(session_name, output_dir, laps):
    print("[VIZ 4/8] Speed trap comparison...")
    speed_cols = [col for col in ['SpeedI1', 'SpeedI2', 'SpeedST'] 
             if col in laps.columns and laps[col].notna().any()]
    if not speed_cols:
        print("No speed data available, skipping...")
        return
    fig, axes = plt.subplots(1, len(speed_cols), figsize=(6*len(speed_cols), 8))
    if len(speed_cols) == 1:
        axes = [axes]
    for idx, speed_col in enumerate(speed_cols):
        valid_speeds = laps[laps[speed_col].notna()].copy()
        max_speeds = valid_speeds.groupby('Driver')[speed_col].max().sort_values(ascending=False)
        colors = plt.cm.plasma(np.linspace(0.2, 0.8, len(max_speeds)))
        axes[idx].barh(range(len(max_speeds)), max_speeds.values, color=colors)
        axes[idx].set_yticks(range(len(max_speeds)))
        axes[idx].set_yticklabels(max_speeds.index)
        axes[idx].set_xlabel('Speed (km/h)', fontsize=11)
        axes[idx].set_title(f'{speed_col.replace("Speed", "")}', fontsize=12, fontweight='bold')
        axes[idx].invert_yaxis()
        axes[idx].grid(True, axis='x', alpha=0.3)
        for i, (driver, speed) in enumerate(max_speeds.items()):
            axes[idx].text(speed + 1, i, f'{speed:.1f}', va='center', fontsize=9)
    fig.suptitle(f'{session_name}\nSpeed Trap Comparison', 
                fontsize=14, fontweight='bold', y=1.02)
    plt.tight_layout()
    output_file = Path(output_dir) / '04_speed_comparison.png'
    plt.savefig(output_file, dpi=300, bbox_inches='tight')
    plt.close()
    print(f"  Saved: {output_file}")

def plot_consistency(session_name, output_dir, laps):
    print("[VIZ 5/8] Lap time consistency by compound...")
    valid_laps = laps[laps['LapTime'].notna()].copy()
    try:
        if pd.api.types.is_timedelta64_dtype(valid_laps['LapTime']):
            valid_laps['LapTimeSec'] = valid_laps['LapTime'].dt.total_seconds()
        else:
            valid_laps['LapTimeSec'] = pd.to_timedelta(valid_laps['LapTime']).dt.total_seconds()
    except Exception:
        valid_laps['LapTimeSec'] = pd.to_numeric(valid_laps['LapTime'], errors='coerce')

    compound_map = {'HARD': 'Hard', 'MEDIUM': 'Medium', 'SOFT': 'Soft', 'INTERMEDIATE': 'Intermediate', 'WET': 'Wet'}
    compound_colors = {'Hard': 'black', 'Medium': 'yellow', 'Soft': 'red', 'Intermediate': 'darkgreen', 'Wet': 'blue'}
    compounds = ['Hard', 'Medium', 'Soft', 'Intermediate', 'Wet']
    for compound in compounds:
        laps_comp = valid_laps.copy()
        # Normalize compound column
        laps_comp['CompoundNorm'] = laps_comp.get('StintCompound', laps_comp.get('Compound', pd.Series())).astype(str).str.upper().map(compound_map).fillna('Unknown')
        laps_comp = laps_comp[laps_comp['CompoundNorm'] == compound]
        # Only skip if truly no laps for this compound
        if laps_comp.empty:
            print(f'No laps for {compound}, skipping...')
            continue
        # Group by driver and stint
        consistency_data = []
        for (driver, stint), stint_df in laps_comp.groupby(['Driver', 'Stint']):
            if len(stint_df) < 2:
                continue
            consistency_data.append({
                'Driver': driver,
                'Stint': stint,
                'Mean': stint_df['LapTimeSec'].mean(),
                'StdDev': stint_df['LapTimeSec'].std(),
                'Min': stint_df['LapTimeSec'].min(),
                'Max': stint_df['LapTimeSec'].max(),
                'Laps': len(stint_df)
            })
        consistency_df = pd.DataFrame(consistency_data)
        if consistency_df.empty or 'StdDev' not in consistency_df.columns:
            print(f'Not enough data for {compound} consistency plot, skipping...')
            continue
        # --- LIMIT TO MAX 4 STINTS PER DRIVER ---
        consistency_df = consistency_df.sort_values(['Driver', 'StdDev'])
        consistency_df = consistency_df.groupby('Driver').head(3).reset_index(drop=True)
        fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(16, 8))
        colors = plt.cm.RdYlGn(np.linspace(0.2, 0.8, len(consistency_df)))
        ax1.barh(range(len(consistency_df)), consistency_df['StdDev'], color=colors)
        ax1.set_yticks(range(len(consistency_df)))
        ax1.set_yticklabels([f"{row['Driver']} (Stint {row['Stint']})" for _, row in consistency_df.iterrows()])
        ax1.set_xlabel('Std Deviation (seconds)', fontsize=12)
        ax1.set_title(f'{compound} Lap Time Consistency\n(Lower = More Consistent, Greener = Better)', fontsize=12, fontweight='bold')
        ax1.invert_yaxis()
        ax1.grid(True, axis='x', alpha=0.3)
        box_data = []
        box_labels = []
        for _, row in consistency_df.iterrows():
            driver = row['Driver']
            stint = row['Stint']
            driver_times = laps_comp[(laps_comp['Driver'] == driver) & (laps_comp['Stint'] == stint)]['LapTimeSec']
            box_data.append(driver_times)
            # Use single-line label for clarity
            box_labels.append(f"{driver} – Stint {stint} ({row['Laps']} laps)")
        bp = ax2.boxplot(box_data, tick_labels=box_labels, patch_artist=True, vert=False)
        # 1) just color the boxes
        for patch, color in zip(bp['boxes'], colors):
            patch.set_facecolor(color)
        # 2) one cursor for all boxes
        try:
            import mplcursors
        except ImportError:
            print("mplcursors not installed; skipping interactive tooltips.")
        else:
            drivers     = list(consistency_df['Driver'])
            stints      = list(consistency_df['Stint'])
            laps_counts = list(consistency_df['Laps'])
            cursor = mplcursors.cursor(bp['boxes'], hover=True)
            @cursor.connect("add")
            def on_add(sel):
                i = sel.index
                sel.annotation.set_text(
                    f"Driver: {drivers[i]}\n"
                    f"Stint: {stints[i]}\n"
                    f"Laps:  {laps_counts[i]}"
                )
                sel.annotation.set_linespacing(1.5)      # more vertical space
                sel.annotation.get_bbox_patch().set_alpha(0.9)
                # optional small offset from mouse
                # x, y = sel.annotation.xy
                # sel.annotation.xy = (x + 5, y + 5)
        def format_laptime(val, pos=None):
            if pd.isna(val):
                return ''
            mins = int(val // 60)
            secs = int(val % 60)
            ms = int((val - mins * 60 - secs) * 1000)
            return f"{mins}:{secs:02d}:{ms:03d}"
        ax2.xaxis.set_major_formatter(FuncFormatter(format_laptime))
        ax2.set_xlabel('Lap Time (mm:ss:ms)', fontsize=12)
        ax2.set_title(f'{compound} Lap Time Distribution', fontsize=12, fontweight='bold')
        ax2.grid(True, axis='x', alpha=0.3)
        fig.suptitle(f'{session_name}\nDriver Consistency Analysis ({compound})\nLower StdDev and Greener = More Consistent.\nA driver with many green stints is consistently fast; red and fewer means less consistent.', fontsize=14, fontweight='bold', y=1.0)
        plt.tight_layout()
        output_file = Path(output_dir) / f'05_consistency_{compound.lower()}.png'
        plt.savefig(output_file, dpi=300, bbox_inches='tight')
        plt.close()
        print(f"  Saved: {output_file}")

def plot_stint_analysis(session_name, output_dir, laps):
    print("[VIZ 6/8] Stint analysis...")
    valid_laps = laps[laps['LapTime'].notna() & laps['Stint'].notna()].copy()
    if len(valid_laps) == 0:
        print("No stint data available, skipping...")
        return
    valid_laps['LapTimeSec'] = valid_laps['LapTime'].dt.total_seconds()
    top_drivers = valid_laps['Driver'].value_counts().head(5).index
    fig, axes = plt.subplots(len(top_drivers), 1, figsize=(14, 4*len(top_drivers)))
    if len(top_drivers) == 1:
        axes = [axes]
    for idx, driver in enumerate(top_drivers):
        driver_laps = valid_laps[valid_laps['Driver'] == driver].sort_values('LapNumber')
        med = driver_laps['LapTimeSec'].median()
        if pd.notna(med):
            before = len(driver_laps)
            driver_laps = driver_laps[driver_laps['LapTimeSec'] <= (med + 120)]
            excluded = before - len(driver_laps)
        else:
            excluded = 0
        if len(driver_laps) == 0:
            axes[idx].text(0.5, 0.5, 'No valid laps after filtering', ha='center', va='center')
            axes[idx].set_title(f'{driver}', fontsize=12, fontweight='bold')
            continue
        unique_stints = sorted(driver_laps['Stint'].dropna().unique())
        color_map = get_stint_color_map(unique_stints)
        positions = []
        heights = []
        bar_colors = []
        lap_numbers = []
        lap_time_labels = []
        lapnum_to_pos = {}
        current_pos = 0
        stint_centers = {}
        stint_tyre = {}
        for stint in unique_stints:
            stint_laps = driver_laps[driver_laps['Stint'] == stint].sort_values('LapNumber')
            cnt = len(stint_laps)
            if cnt == 0:
                continue
            pos = current_pos + np.arange(cnt)
            for i, (_, row) in enumerate(stint_laps.iterrows()):
                p = float(pos[i])
                positions.append(p)
                heights.append(float(row['LapTimeSec']))
                ln = int(row['LapNumber']) if 'LapNumber' in row and not pd.isna(row['LapNumber']) else None
                lap_numbers.append(ln)
                lap_time_labels.append(format_time_of_day(row))
                if ln is not None:
                    lapnum_to_pos[ln] = p
            cmap_key = str(int(stint)) if (isinstance(stint, (int, float)) and float(stint).is_integer()) else str(stint)
            bar_colors.extend([color_map.get(cmap_key, '#CCCCCC')] * cnt)
            stint_centers[stint] = float(np.mean(pos))
            stint_tyre[stint] = infer_stint_compound(stint_laps)
            current_pos = float(pos[-1]) + 1.5
        bars = axes[idx].bar(positions, heights, color=bar_colors, alpha=0.95, edgecolor='black', linewidth=0.4)
        try:
            driver_laps['LapTimeSec_num'] = pd.to_numeric(driver_laps['LapTimeSec'], errors='coerce')
            if driver_laps['LapTimeSec_num'].notna().any():
                best_idx = driver_laps['LapTimeSec_num'].idxmin()
                best_lap = driver_laps.loc[best_idx]
                if best_lap is not None and 'LapNumber' in best_lap and not pd.isna(best_lap['LapNumber']):
                    best_num = int(best_lap['LapNumber'])
                    if best_num in lap_numbers:
                        bi = lap_numbers.index(best_num)
                        bars[bi].set_facecolor('black')
                        bars[bi].set_edgecolor('yellow')
        except Exception:
            pass
        if len(heights) > 0:
            maxh = max(heights)
        else:
            maxh = 0
        for stint, center in stint_centers.items():
            stint_count = len(driver_laps[driver_laps['Stint'] == stint])
            y_top = maxh * 1.01
            y_compound = y_top - 0.6
            axes[idx].text(center, y_top, f'Laps: {stint_count}', ha='center', va='bottom', fontsize=9, fontweight='bold')
            tyre_value = stint_tyre.get(stint, 'Unknown')
            axes[idx].text(center, y_compound, str(tyre_value), ha='center', va='top', fontsize=8)
        if len(positions) > 0:
            keep = np.linspace(0, len(positions)-1, min(12, len(positions))).astype(int)
            axes[idx].set_xticks([positions[i] for i in keep])
            axes[idx].set_xticklabels([f"{lap_numbers[i]}" if lap_numbers[i] is not None else '' for i in keep], rotation=45, ha='right')
        axes[idx].set_ylabel('Lap Time', fontsize=11)
        axes[idx].yaxis.set_major_formatter(FuncFormatter(format_laptime))
        axes[idx].set_title(f'{driver}', fontsize=12, fontweight='bold')
        axes[idx].grid(True, alpha=0.3, axis='y')
    axes[-1].set_xlabel('Lap Number (grouped by stint)', fontsize=12)
    fig.suptitle(f'{session_name}\nStint-by-Stint Analysis (Top 5 Drivers)', fontsize=14, fontweight='bold', y=1.02)
    plt.tight_layout()
    output_file = Path(output_dir) / '06_stint_analysis.png'
    plt.savefig(output_file, dpi=300, bbox_inches='tight')
    plt.close()
    print(f"  Saved: {output_file}")

def plot_team_comparison(session_name, output_dir, laps):
    print("[VIZ 7/8] Team comparison...")
    laps_team = laps[laps['Team'].notna()].copy()
    if len(laps_team) == 0:
        print("No team data available, skipping...")
        return
    # Best lap per team (only for LapTime notna)
    laps_team['LapTimeSec'] = laps_team['LapTime'].dt.total_seconds()
    team_best = laps_team[laps_team['LapTime'].notna()].groupby('Team')['LapTimeSec'].min().sort_values()
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(16, 8))
    fastest = team_best.values.min()
    deltas = team_best.values - fastest
    colors = plt.cm.tab20(np.linspace(0, 1, len(team_best)))
    ax1.barh(range(len(team_best)), deltas, color=colors)
    ax1.set_yticks(range(len(team_best)))
    ax1.set_yticklabels(team_best.index)
    ax1.set_xlabel('Delta to Fastest Team (s)', fontsize=12)
    ax1.set_title('Best Lap by Team (Δ to fastest)', fontsize=12, fontweight='bold')
    ax1.invert_yaxis()
    ax1.grid(True, axis='x', alpha=0.3)
    for i, delta in enumerate(deltas):
        ax1.text(delta + 0.01, i, f'+{delta:.3f}s', va='center', fontsize=9)
    # Count all laps per team, regardless of LapTime validity
    team_laps = laps_team.groupby('Team').size().sort_values(ascending=False)
    ax2.bar(range(len(team_laps)), team_laps.values, color=colors)
    ax2.set_xticks(range(len(team_laps)))
    ax2.set_xticklabels(team_laps.index, rotation=45, ha='right')
    ax2.set_ylabel('Total Laps Completed', fontsize=12)
    ax2.set_title('Total Laps by Team', fontsize=12, fontweight='bold')
    ax2.grid(True, axis='y', alpha=0.3)
    fig.suptitle(f'{session_name}\nTeam Performance Overview', 
                fontsize=14, fontweight='bold', y=1.0)
    plt.tight_layout()
    output_file = Path(output_dir) / '07_team_comparison.png'
    plt.savefig(output_file, dpi=300, bbox_inches='tight')
    plt.close()
    print(f"  Saved: {output_file}")

def plot_session_summary(session_name, output_dir, laps):
    print("[VIZ 8/8] Session summary...")
    fig = plt.figure(figsize=(16, 8))
    gs = fig.add_gridspec(1, 2, wspace=0.3)
    ax1 = fig.add_subplot(gs[0, 0])
    # Include all drivers from the session, even those with zero laps
    if 'Driver' in laps.columns:
        all_drivers = sorted(pd.unique(laps['Driver'].dropna()))
        lap_counts = laps['Driver'].value_counts().reindex(all_drivers, fill_value=0)
    else:
        lap_counts = pd.Series(dtype=int)
    ax1.bar(range(len(lap_counts)), lap_counts.values, color='skyblue')
    ax1.set_xticks(range(len(lap_counts)))
    ax1.set_xticklabels(lap_counts.index, rotation=45, ha='right')
    ax1.set_ylabel('Total Laps', fontsize=11)
    ax1.set_title('Total Laps per Driver', fontsize=12, fontweight='bold')
    ax1.grid(True, axis='y', alpha=0.3)
    ax2 = fig.add_subplot(gs[0, 1])
    ax2.axis('off')
    # Only use valid_laps for statistics that require valid lap times
    valid_laps = laps[laps['LapTime'].notna()].copy()
    valid_laps['LapTimeSec'] = valid_laps['LapTime'].dt.total_seconds()
    total_laps = len(valid_laps)
    unique_drivers = len(pd.unique(laps['Driver'].dropna())) if 'Driver' in laps.columns else 0
    unique_teams = len(pd.unique(laps['Team'].dropna())) if 'Team' in laps.columns else 'N/A'
    fastest_idx = None
    fastest_time = None
    fastest_driver = 'N/A'
    try:
        fastest_idx = valid_laps['LapTimeSec'].idxmin()
        fastest_time = float(valid_laps.loc[fastest_idx, 'LapTimeSec'])
        fastest_driver = valid_laps.loc[fastest_idx, 'Driver']
    except Exception:
        pass
    fastest_formatted = format_laptime(fastest_time) if fastest_time is not None else 'N/A'
    top3 = []
    try:
        top_df = valid_laps.nsmallest(3, 'LapTimeSec')[['Driver', 'LapNumber', 'LapTimeSec']]
        for _, row in top_df.iterrows():
            ddriver = row['Driver']
            lapnum = int(row['LapNumber']) if not pd.isna(row['LapNumber']) else ''
            t = float(row['LapTimeSec'])
            delta = t - fastest_time if fastest_time is not None else 0.0
            top3.append((ddriver, lapnum, t, delta))
    except Exception:
        top3 = []
    best_sectors = {}
    for s_col, label in [('Sector1Time', 'Sector 1'), ('Sector2Time', 'Sector 2'), ('Sector3Time', 'Sector 3')]:
        try:
            if s_col in valid_laps.columns and valid_laps[s_col].notna().any():
                secs = valid_laps[s_col].dt.total_seconds()
                idx = secs.idxmin()
                drv = valid_laps.loc[idx, 'Driver']
                tval = float(secs.loc[idx])
                best_sectors[label] = (drv, format_laptime(tval))
            else:
                best_sectors[label] = ('N/A', 'N/A')
        except Exception:
            best_sectors[label] = ('N/A', 'N/A')
    stats_lines = []
    stats_lines.append('SESSION STATISTICS')
    stats_lines.append('')
    stats_lines.append(f'Total Laps: {total_laps:,}')
    stats_lines.append(f'Unique Drivers: {unique_drivers}')
    stats_lines.append(f'Unique Teams: {unique_teams}')
    stats_lines.append('')
    stats_lines.append(f'Fastest Lap: {fastest_formatted}')
    stats_lines.append(f'  By: {fastest_driver}')
    stats_lines.append('')
    stats_lines.append('Top 3 (Δ to fastest):')
    for drv, ln, t, delta in top3:
        stats_lines.append(f'  {drv} L{ln}  +{delta:.3f}s')
    stats_lines.append('')
    stats_lines.append('Best Sectors (overall):')
    for lab in ['Sector 1', 'Sector 2', 'Sector 3']:
        drv, tf = best_sectors.get(lab, ('N/A','N/A'))
        stats_lines.append(f'  {lab}: {drv} {tf}')
    ax2.text(0.02, 0.5, "\n".join(stats_lines), fontsize=11, family='monospace', verticalalignment='center')
    fig.suptitle(f'{session_name}\nSession Summary', fontsize=16, fontweight='bold', y=1.02)
    output_file = Path(output_dir) / '08_session_summary.png'
    plt.tight_layout()
    plt.savefig(output_file, dpi=300, bbox_inches='tight')
    plt.close()
    print(f"  OK Saved: {output_file}")
  
def sanitize_tag(raw_tag: str) -> str:  
    return re.sub(r'[^A-Za-z0-9_]', '_', raw_tag.replace(' ', '_')).strip('_')  
  
  
def load_session_laps(session_prefix: str, year: str = None, event_name: str = None, session_type: str = None):  
    telemetry_dir = Path('telemetry_out')
    if not telemetry_dir.exists():
        raise FileNotFoundError('telemetry_out directory not found')

    candidates = []
    # Use fixed telemetry structure for lap files when year/event/session_type are provided
    if year and event_name and session_type:
        telemetry_path = Path(f"telemetry_out/{year}/{event_name}/{session_type}")
        if telemetry_path.exists():
            candidates = list(telemetry_path.glob('*_laps.csv'))
        if not candidates:
            # Fallback: recursive search within the correct session folder only
            candidates = list(telemetry_path.rglob('*_laps.csv'))
    elif session_prefix:
        # Legacy: try to parse from session_prefix string (less reliable)
        parts = session_prefix.split('_')
        session_type_map = {'FP1': 'Practice_1', 'FP2': 'Practice_2', 'FP3': 'Practice_3'}
        session_type_raw = parts[-1]
        parsed_session_type = session_type_map.get(session_type_raw, session_type_raw)
        parsed_year = parts[0]
        parsed_event = '_'.join(parts[1:-1])
        telemetry_path = Path(f"telemetry_out/{parsed_year}/{parsed_event}/{parsed_session_type}")
        if telemetry_path.exists():
            candidates = list(telemetry_path.glob('*_laps.csv'))
        if not candidates:
            candidates = list(telemetry_path.rglob('*_laps.csv'))
    if not candidates:
        raise FileNotFoundError(f'No lap CSV files found for session: year={year}, event={event_name}, session_type={session_type}')

    all_laps = []
    for f in candidates:
        try:
            df = pd.read_csv(f)
            # Try to infer driver from filename (assumes *_<DRIVER>_laps.csv)
            parts = f.stem.split('_')
            if len(parts) >= 2:
                driver = parts[-2]
                df['Driver'] = df.get('Driver', driver)
            all_laps.append(df)
        except Exception:
            continue

    if not all_laps:
        raise RuntimeError('Failed to load any lap CSVs')

    laps = pd.concat(all_laps, ignore_index=True)
    return laps
  
  
def simple_best_two_overlay(telemetry_traces, drivers, outdir, laps, session=None, year=None):
    """  
    Plot smoothed speed-vs-distance for the best laps of two drivers,  
    compute time delta from speed traces only, save speed/time deltas  
    to a CSV file, and overlay circuit corner markers.  
    """  
    if len(drivers) < 2:  
        return  
  
    # --- 0. Lap length and common distance grid ---
    lap_length_m = 4300.0
    n_grid = 1500
    xi = np.linspace(0, lap_length_m, n_grid)

    fig, ax = plt.subplots(figsize=(10, 4))
    colors = ['magenta', 'lime']
    smoothed_speed = {}
    # ...existing code...

    # Common distance grid for both drivers
    n_grid = 1500
    xi = np.linspace(0, lap_length_m, n_grid)
  
    fig, ax = plt.subplots(figsize=(10, 4))
    colors = ['magenta', 'lime']  
  
    # --- 1–4. Build smoothed speed traces on common grid ---  
    smoothed_speed = {}  
  
    for drv, color in zip(drivers, colors):  
        if drv not in telemetry_traces:  
            continue  
  
        tr = telemetry_traces[drv]  
  
        if isinstance(tr, dict):  
            xs = np.asarray(tr.get('dist', []), float)  
            ys = np.asarray(tr.get('speed', []), float)  
        else:  
            xs, ys = tr  
            xs = np.asarray(xs, float)  
            ys = np.asarray(ys, float)  
  
        # 1. normalise distance per lap and rescale to meters  
        xs = xs - np.nanmin(xs)  
        max_d = np.nanmax(xs)  
        if max_d <= 0:  
            continue  
        xs = xs / max_d * lap_length_m  
  
        # sort by distance  
        order = np.argsort(xs)  
        xs, ys = xs[order], ys[order]  
  
        # convert to km/h if looks like m/s  
        try:  
            if np.nanmax(ys) < 90:  
                ys = ys * 3.6  
        except Exception:  
            pass  
  
        # 2. drop very low speeds (pit / glitches)  
        mask = ys > 20.0  
        xs, ys = xs[mask], ys[mask]  
        if len(xs) < 40:  
            continue  
  
        # 3. resample onto the common distance grid xi  
        uniq = np.concatenate(([True], np.diff(xs) > 1e-6))  
        xs_u, ys_u = xs[uniq], ys[uniq]  
        if len(xs_u) < 40:  
            continue  
  
        yi = np.interp(xi, xs_u, ys_u)  
  
        # 4. strong distance‑based smoothing (e.g. 120 m window)  
        dx = lap_length_m / n_grid           # metres between samples  
        window_m = 120.0                     # smoothing distance  
        win_len = int(window_m / dx)  
        win_len = max(5, min(win_len, 801))  
        if win_len % 2 == 0:  
            win_len += 1  
  
        kernel = np.ones(win_len, dtype=float) / win_len  
        yi_smooth = np.convolve(yi, kernel, mode="same")  
  
        smoothed_speed[drv] = yi_smooth  
        ax.plot(xi, yi_smooth, label=drv, color=color, linewidth=1.8)  
  
    # --- 5. Compute numeric deltas and save to CSV (no extra plot) ---  
    delta_time = None  
    if len(smoothed_speed) >= 2:  
        ref_drv = drivers[0]  
        cmp_drv = drivers[1]  
  
        if ref_drv in smoothed_speed and cmp_drv in smoothed_speed:  
            v_ref_kmh = smoothed_speed[ref_drv]  
            v_cmp_kmh = smoothed_speed[cmp_drv]  
  
            # speed delta (km/h): cmp - ref  
            delta_speed = v_cmp_kmh - v_ref_kmh  
  
            # speeds in m/s, safe against NaNs/zeros  
            v_ref = np.nan_to_num(v_ref_kmh / 3.6, nan=1.0, posinf=1e3, neginf=1.0)  
            v_cmp = np.nan_to_num(v_cmp_kmh / 3.6, nan=1.0, posinf=1e3, neginf=1.0)  
            v_ref = np.clip(v_ref, 1.0, None)  
            v_cmp = np.clip(v_cmp, 1.0, None)  
  
            # integrate delta_t(x) = ∫ (1/v_cmp - 1/v_ref) dx  
            ds = np.gradient(xi)  
            d_dt = ds * (1.0 / v_cmp - 1.0 / v_ref)  
            delta_time = np.cumsum(d_dt)  
            delta_time = delta_time - delta_time[0]  
  
            # Look up actual lap times and scale final delta if possible  
            T_ref_actual = None  
            T_cmp_actual = None  
            try:  
                T_ref_actual = float(  
                    laps[laps['Driver'] == ref_drv]  
                    .sort_values('LapTimeSec')  
                    .head(1)['LapTimeSec']  
                    .iloc[0]  
                )  
            except Exception:  
                pass  
            try:  
                T_cmp_actual = float(  
                    laps[laps['Driver'] == cmp_drv]  
                    .sort_values('LapTimeSec')  
                    .head(1)['LapTimeSec']  
                    .iloc[0]  
                )  
            except Exception:  
                pass  
  
            if (  
                T_ref_actual is not None and  
                T_cmp_actual is not None and  
                abs(delta_time[-1]) > 1e-9  
            ):  
                target_final = T_cmp_actual - T_ref_actual  
                scale = target_final / delta_time[-1]  
                delta_time *= scale  
  
            delta_df = pd.DataFrame({  
                "Distance_m": xi,  
                f"{ref_drv}_speed_kmh": v_ref_kmh,  
                f"{cmp_drv}_speed_kmh": v_cmp_kmh,  
                f"{cmp_drv}_minus_{ref_drv}_speed_delta_kmh": delta_speed,  
                f"{cmp_drv}_minus_{ref_drv}_time_delta_s": delta_time,  
            })  
            delta_path = outdir / "02_best_two_delta.csv"  
            try:  
                delta_df.to_csv(delta_path, index=False)  
                print(f"Saved delta details: {delta_path}")  
            except Exception:  
                pass  
  
        # --- 6. Annotate time delta at ~5 points along the lap + finish ---
        if delta_time is not None:
            base_fracs = np.linspace(0.1, 0.9, 5)
            idxs = [int(f * (len(xi) - 1)) for f in base_fracs]
            idxs.append(len(xi) - 1)
            idxs = sorted(set(idxs))

            y_min, y_max = ax.get_ylim()
            y_span = y_max - y_min
            y_label = y_max + 0.05 * y_span

            for idx in idxs:
                x = xi[idx]
                dt = float(delta_time[idx])
                ax.axvline(x, color='gray', alpha=0.2,
                           linestyle='--', linewidth=0.7)
                ax.text(
                    x, y_label,
                    f"{dt:+.3f}s",
                    ha='center', va='bottom',
                    fontsize=8, color='black',
                    clip_on=False
                )

            ax.set_ylim(y_min, y_label + 0.10 * y_span)

            try:
                cmp_drv
            except Exception:
                cmp_drv = drivers[1]
            try:
                ref_drv
            except Exception:
                ref_drv = drivers[0]

            ax.text(
                0.99, 0.02,
                f"Δt = {cmp_drv} - {ref_drv}  ( +ve: {cmp_drv} behind )",
                transform=ax.transAxes,
                ha='right', va='bottom',
                fontsize=8, color='black'
            )

        # --- 7. Corner markers (always try to draw them) ---
        add_corner_markers(ax, lap_length_m, outdir, session=session, year=year)

        # --- 8. Finish plot ---
        if ax.get_xlabel() == "":
            ax.set_xlabel("Distance [m]")

        ax.set_ylabel("Speed [km/h]")
        ax.set_title("Top 2 Drivers - Best Lap Speed Overlay")
        ax.grid(True, alpha=0.3)
        ax.legend()

        fig.tight_layout(rect=[0, 0.08, 1, 1])
        out_file = outdir / "02_best_two_overlay.png"
        fig.savefig(out_file, dpi=150)
        plt.close(fig)
        print(f"Saved simple overlay: {out_file}")
  
  
def simple_best_two_overlay_metric(telemetry_traces, drivers, outdir, laps, metric, ylabel, session=None, year=None):
    """
    Plot smoothed <metric>-vs-distance for the best laps of two drivers,
    overlay circuit corner markers, and save the plot.
    """
    if len(drivers) < 2:
        return
    lap_length_m = 4300.0
    n_grid = 1500
    xi = np.linspace(0, lap_length_m, n_grid)
    fig, ax = plt.subplots(figsize=(10, 4))
    colors = ['magenta', 'lime']
    for drv, color in zip(drivers, colors):
        if drv not in telemetry_traces:
            continue
        tr = telemetry_traces[drv]
        if isinstance(tr, dict):
            xs = np.asarray(tr.get('dist', []), float)
            ys = np.asarray(tr.get(metric, []), float)
        else:
            xs, ys = tr
            xs = np.asarray(xs, float)
            ys = np.asarray(ys, float)
        xs = xs - np.nanmin(xs)
        max_d = np.nanmax(xs)
        if max_d <= 0:
            continue
        xs = xs / max_d * lap_length_m
        order = np.argsort(xs)
        xs, ys = xs[order], ys[order]
        mask = np.isfinite(ys)
        xs, ys = xs[mask], ys[mask]
        if len(xs) < 40:
            continue
        uniq = np.concatenate(([True], np.diff(xs) > 1e-6))
        xs_u, ys_u = xs[uniq], ys[uniq]
        if len(xs_u) < 40:
            continue
        yi = np.interp(xi, xs_u, ys_u)
        dx = lap_length_m / n_grid
        window_m = 120.0
        win_len = int(window_m / dx)
        win_len = max(5, min(win_len, 801))
        if win_len % 2 == 0:
            win_len += 1
        kernel = np.ones(win_len, dtype=float) / win_len
        yi_smooth = np.convolve(yi, kernel, mode="same")
        ax.plot(xi, yi_smooth, label=drv, color=color, linewidth=1.8)
    add_corner_markers(ax, lap_length_m, outdir, session=session, year=year)
    ax.set_xlabel("Turns")
    ax.set_ylabel(ylabel)
    ax.set_title(f"Top 2 Drivers - Best Lap {ylabel} Overlay")
    ax.grid(True, alpha=0.3)
    ax.legend()
    fig.tight_layout(rect=[0, 0.08, 1, 1])
    out_file = outdir / f"02_best_two_overlay_{metric}.png"
    fig.savefig(out_file, dpi=150)
    plt.close(fig)
    print(f"Saved {ylabel} overlay: {out_file}")
  
def plot_best_two_overlay_all(telemetry_traces, top2, output_dir, laps, session=None, year=None):
    # Speed overlay (existing)
    simple_best_two_overlay(telemetry_traces, top2, output_dir, laps, session=session, year=year)
    # ApexRPM overlay
    simple_best_two_overlay_metric(telemetry_traces, top2, output_dir, laps, metric='ApexRPM', ylabel='Apex RPM', session=session, year=year)
    # EntryBrake overlay
    simple_best_two_overlay_metric(telemetry_traces, top2, output_dir, laps, metric='EntryBrake', ylabel='Entry Brake', session=session, year=year)
def main():  
    parser = argparse.ArgumentParser()  
    parser.add_argument('--session', required=True)  
    parser.add_argument('--year', type=int, required=True)  
    parser.add_argument('--source', default='local')  
    args = parser.parse_args()  
  
    session = args.session  
    year = args.year  
    source = args.source  
  
    # Standardize output_dir: visualizations/{year}/{event}/{session_type}/
    # Must handle multi-word session types like Practice_1, Sprint_Qualifying, etc.
    _known_types = [
        'Practice_1', 'Practice_2', 'Practice_3',
        'Sprint_Qualifying', 'Sprint_Shootout', 'Sprint',
        'Qualifying', 'Race',
        'FP1', 'FP2', 'FP3',
    ]
    _type_aliases = {'FP1': 'Practice_1', 'FP2': 'Practice_2', 'FP3': 'Practice_3'}
    event_name_clean = session
    session_type_clean = None
    for ktype in sorted(_known_types, key=len, reverse=True):
        if session.endswith('_' + ktype):
            session_type_clean = _type_aliases.get(ktype, ktype)
            event_name_clean = session[:-(len(ktype) + 1)]
            break
    if session_type_clean is None:
        # Fallback: last token
        _parts = session.split('_')
        session_type_clean = _parts[-1]
        event_name_clean = '_'.join(_parts[:-1])
    year_str = str(year)
    output_dir = Path('visualizations') / year_str / event_name_clean / session_type_clean
    output_dir.mkdir(parents=True, exist_ok=True)
    print(f"Generating visualizations for session: {session} -> {output_dir}")

    # Robust: Search recursively for session_meta.json in telemetry_out
    meta_files = list(Path('telemetry_out').rglob('session_meta.json'))
    meta_path = None
    # Try to find the most relevant session_meta.json for the current session
    for f in meta_files:
        # Match year, event, session_type in path
        if str(year) in str(f.parent) and event_name_clean in str(f.parent) and session_type_clean in str(f.parent):
            meta_path = f
            break
    if not meta_path and meta_files:
        meta_path = meta_files[0]  # fallback: use first found
    if not meta_path or not meta_path.exists():
        print("No session_meta.json found in telemetry_out, skipping visualizations.")
        sys.exit(0)

    laps = load_session_laps(session, year=year_str, event_name=event_name_clean, session_type=session_type_clean)  
  
    # Normalize/convert common time columns to seconds for the visualizer  
    time_cols = ['LapTime', 'Sector1Time', 'Sector2Time', 'Sector3Time', 'LapStartTime']  
    for col in time_cols:  
        if col in laps.columns:  
            try:  
                laps[col] = pd.to_timedelta(laps[col], errors='coerce')  
            except Exception:  
                pass  
  
    # Add seconds columns if present  
    for base in ['LapTime', 'Sector1Time', 'Sector2Time', 'Sector3Time']:  
        sec_col = base + 'Sec'  
        if base in laps.columns and sec_col not in laps.columns:  
            try:  
                laps[sec_col] = laps[base].dt.total_seconds()  
            except Exception:  
                laps[sec_col] = pd.to_numeric(laps.get(base), errors='coerce')  
  
    # Ensure LapNumber is numeric  
    if 'LapNumber' in laps.columns:  
        laps['LapNumber'] = pd.to_numeric(laps['LapNumber'], errors='coerce')  
  
    # Determine top-2 drivers by best lap
    # Narrow search to the specific session directory so rglob never accidentally
    # picks up telemetry from a different session/event for the same driver.
    _session_telemetry_dir = Path('telemetry_out') / year_str / event_name_clean / session_type_clean
    telemetry_dir = _session_telemetry_dir if _session_telemetry_dir.exists() else Path('telemetry_out')
    telemetry_traces = {}  
    try:  
        if 'LapTimeSec' not in laps.columns and 'LapTime' in laps.columns:  
            laps['LapTimeSec'] = pd.to_timedelta(laps['LapTime']).dt.total_seconds()  
    except Exception:  
        pass  
  
    try:  
        best_laps = laps.groupby('Driver')['LapTimeSec'].min().sort_values()  
        top2 = best_laps.index.tolist()[:2]  
    except Exception:  
        top2 = []  
  
    # Build simple telemetry traces for all drivers found in laps
    drivers_list = []
    try:
        drivers_list = list(pd.unique(laps['Driver'].dropna()))
    except Exception:
        drivers_list = list(top2)

    # prefer full telemetry files for each driver, search recursively
    for driver in drivers_list:
        candidates = list(telemetry_dir.rglob(f"*_{driver}_telemetry.csv"))
        if not candidates:
            candidates = list(telemetry_dir.rglob(f"*{driver}*_telemetry.csv"))
        if not candidates:
            candidates = list(telemetry_dir.rglob('*_telemetry.csv'))

        for tfile in candidates:
            try:
                tdf = pd.read_csv(tfile)
                dist_col = next(
                    (c for c in tdf.columns
                     if c.lower() in ('distance', 'dist', 'lap_distance', 'distance_m')),
                    None
                )
                speed_col = next((c for c in tdf.columns if 'speed' in c.lower()), None)
                if not dist_col or not speed_col:
                    continue

                # try to pick best lap slice if LapNumber exists
                tlap = tdf
                if 'LapNumber' in tdf.columns and 'LapNumber' in laps.columns:
                    try:
                        lapnum = int(
                            laps[laps['Driver'] == driver]
                            .sort_values('LapTimeSec')
                            .head(1)['LapNumber']
                            .iloc[0]
                        )
                        tlap = tdf[tdf['LapNumber'] == lapnum].copy()
                    except Exception:
                        tlap = tdf.copy()

                if tlap is None or tlap.empty:
                    continue

                xs = pd.to_numeric(tlap[dist_col], errors='coerce').to_numpy()
                ys = pd.to_numeric(tlap[speed_col], errors='coerce').to_numpy()

                # optional time column (kept for possible future use/debug)
                time_col = next(
                    (c for c in tdf.columns
                     if c.lower() in (
                         'time', 'sessiontime', 'timestamp', 't',
                         'lap_time', 'laptime', 'lap_time_sec', 'lap_time_s'
                     )),
                    None
                )
                ts = None
                if time_col and time_col in tlap.columns:
                    try:
                        raw = tlap[time_col]
                        if pd.api.types.is_timedelta64_dtype(raw.dtype):
                            ts = raw.dt.total_seconds().to_numpy()
                        else:
                            ts = pd.to_numeric(raw, errors='coerce').to_numpy()
                            if np.isnan(ts).all():
                                ts = pd.to_timedelta(raw, errors='coerce').dt.total_seconds().to_numpy()
                    except Exception:
                        ts = None

                if len(xs) < 10 or np.nanmax(ys) == 0 or np.isnan(xs).all() or np.isnan(ys).all():
                    continue
                try:
                    if np.nanmax(ys) < 90:
                        ys = ys * 3.6
                except Exception:
                    pass

                # Extract additional metrics for overlays
                apexrpm = None
                entrybrake = None
                # Try to load from telemetry file if columns exist
                if 'ApexRPM' in tdf.columns:
                    apexrpm = pd.to_numeric(tlap['ApexRPM'], errors='coerce').to_numpy()
                elif 'RPM' in tdf.columns:
                    apexrpm = pd.to_numeric(tlap['RPM'], errors='coerce').to_numpy()
                if 'EntryBrake' in tdf.columns:
                    entrybrake = pd.to_numeric(tlap['EntryBrake'], errors='coerce').to_numpy()
                elif 'Brake' in tdf.columns:
                    entrybrake = pd.to_numeric(tlap['Brake'], errors='coerce').to_numpy()
                telemetry_traces[driver] = {
                    'dist': xs,
                    'speed': ys,
                    'time': ts,
                    'ApexRPM': apexrpm,
                    'EntryBrake': entrybrake
                }
                break
            except Exception:
                continue
  
    # Set session_name for visualizations
    session_name = session_type_clean
    # Run all visualizations directly
    try:
        lap_prog_dir = output_dir / 'lap_progression'
        lap_prog_dir.mkdir(parents=True, exist_ok=True)
        plot_all_drivers_stint_lap_progression_continuous(laps, lap_prog_dir)
        plot_best_lap_comparison(session_name, output_dir, laps)
        print(f"Top2 drivers for overlay: {top2}")
        print(f"Telemetry traces keys: {list(telemetry_traces.keys())}")
        if len(top2) >= 2:
            plot_best_two_overlay_all(telemetry_traces, top2, output_dir, laps, session=session, year=year)
        team_col = next((c for c in laps.columns if c.lower() in ('team','constructor','car','teamname','constructorname','entrant')), None)
        if team_col is not None:
            intra_root = output_dir / 'intra'
            intra_root.mkdir(parents=True, exist_ok=True)
            teams = pd.unique(laps[team_col].dropna())
            for team in teams:
                try:
                    team_laps = laps[laps[team_col] == team]
                    best = team_laps.groupby('Driver')['LapTimeSec'].min().sort_values()
                    pair = best.index.tolist()[:2]
                    if len(pair) < 2:
                        continue
                    team_tag = sanitize_tag(str(team))
                    outdir_team = intra_root / team_tag
                    outdir_team.mkdir(parents=True, exist_ok=True)
                    print(f"Generating intra-team overlay for {team}: {pair}")
                    simple_best_two_overlay(telemetry_traces, pair, outdir_team, laps, session=session, year=year)
                    simple_best_two_overlay_metric(telemetry_traces, pair, outdir_team, laps, metric='ApexRPM', ylabel='Apex RPM', session=session, year=year)
                    simple_best_two_overlay_metric(telemetry_traces, pair, outdir_team, laps, metric='EntryBrake', ylabel='Entry Brake', session=session, year=year)
                except Exception:
                    continue
        plot_sector_performance(session_name, output_dir, laps)
        plot_speed_comparison(session_name, output_dir, laps)
        plot_consistency(session_name, output_dir, laps)
        plot_team_comparison(session_name, output_dir, laps)
        plot_session_summary(session_name, output_dir, laps)
        from stint_analysis_dev import plot_stint_dev as _plot_stint_dev, plot_stint_dev_by_finish as _plot_stint_dev_by_finish
        _sa_label = f"{event_name_clean.replace('_', ' ')} \u2013 {session_type_clean}"
        _plot_stint_dev(laps, _sa_label, output_dir / "stint_analysis_top7.png")
        if session_type_clean == 'Race':
            _plot_stint_dev_by_finish(laps, _sa_label, output_dir / "stint_analysis_top7_finishers.png")
        # --- Sector time tables (FP2 / FP3 / Qualifying) ---
        if session_type_clean in ('Practice_2', 'Practice_3', 'Qualifying'):
            sector_table_dir = output_dir / 'sector_tables'
            plot_all_drivers_sector_table(laps, sector_table_dir)
        # --- Qualifying-lap and race-sim-lap tables (FP2 / FP3 only) ---
        if session_type_clean in ('Practice_2', 'Practice_3'):
            plot_quali_tables(laps, year_str, event_name_clean, session_type_clean, output_dir)
            plot_race_sim_tables(laps, year_str, event_name_clean, session_type_clean, output_dir)
        # --- NEW: Plot classifier output tables as PNGs ---
        plot_classifier_outputs_as_png(year_str, event_name_clean, session_type_clean)
    except Exception as e:
        import warnings
        warnings.warn(f"Visualization failed: {e}")
    print(f"ALL RACE VISUALIZATIONS GENERATED -> {output_dir}")
  
if __name__ == '__main__':  
    main()