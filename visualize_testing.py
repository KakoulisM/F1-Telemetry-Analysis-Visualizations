"""
F1 Testing Session Visualizations
Creates Python-based visualizations from testing telemetry data

This script can work with:
1. Local CSV files (telemetry_out/) - Fast, no Snowflake queries
2. Snowflake tables - Query warehouse data directly

Usage:
    python visualize_testing.py --test 1 --day 1 [--source local|snowflake]
"""

import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
import matplotlib
import warnings
from pathlib import Path
from pipeline_logger import get_logger
import json
import argparse
from datetime import timedelta
import numpy as np
from matplotlib.ticker import FuncFormatter
import re

# Use non-interactive backend to avoid Tk event loop in headless runs
matplotlib.use('Agg')

# Suppress noisy runtime warnings from numpy when grouping empty arrays
warnings.filterwarnings("ignore", category=RuntimeWarning)

# Set style
plt.style.use('seaborn-v0_8-darkgrid')
sns.set_palette("husl")

# Time formatter for lap times
def format_laptime(seconds, pos=None):
    """Convert seconds to MM:SS.mmm format"""
    # Accept pandas Timedelta or datetime.timedelta as well as numeric seconds
    try:
        if isinstance(seconds, pd.Timedelta):
            seconds = seconds.total_seconds()
    except Exception:
        pass
    try:
        # also allow builtin timedelta
        from datetime import timedelta as _td
        if isinstance(seconds, _td):
            seconds = seconds.total_seconds()
    except Exception:
        pass

    if pd.isna(seconds):
        return ''
    try:
        if float(seconds) <= 0:
            return ''
    except Exception:
        return ''

    minutes = int(float(seconds) // 60)
    remaining_seconds = float(seconds) % 60
    return f'{minutes}:{remaining_seconds:06.3f}'


def get_stint_color_map(stints):
    """Return a dict mapping stint (as string) -> color. Uses a large qualitative palette."""
    # Normalize and preserve order
    seen = []
    for s in stints:
        if pd.isna(s):
            continue
        key = str(int(s)) if (isinstance(s, (int, float)) and float(s).is_integer()) else str(s)
        if key not in seen:
            seen.append(key)
    n = max(len(seen), 1)
    # Use tab20 or husl depending on number
    palette = sns.color_palette('tab20' if n > 10 else 'tab10', n_colors=n)
    return {seen[i]: palette[i % len(palette)] for i in range(len(seen))}


def infer_stint_compound(stint_laps):
    """Infer a tyre compound string for a stint dataframe.
    Priority: Compound/ Tyre columns -> FreshTyre -> TyreLife heuristic -> 'Unknown'
    """
    # prefer explicit per-stint column if present, then check common compound columns
    for col in ['StintCompound', 'Compound', 'Tyre', 'TyreCompound', 'TyreCompoundShort']:
        if col in stint_laps.columns and stint_laps[col].dropna().astype(str).str.strip().ne('').any():
            mode = stint_laps[col].mode()
            if len(mode) > 0:
                return str(mode.iloc[0])

    # FreshTyre flag
    if 'FreshTyre' in stint_laps.columns:
        if stint_laps['FreshTyre'].astype(bool).any():
            return 'Fresh'

    # TyreLife heuristic
    if 'TyreLife' in stint_laps.columns:
        try:
            med_life = pd.to_numeric(stint_laps['TyreLife'], errors='coerce').median()
            if pd.notna(med_life):
                return 'New' if med_life < 5 else 'Used'
        except Exception:
            pass

    return 'Unknown'


def format_time_of_day(lap_row):
    """Return HH:MM:SS.mmm time string for a lap row using LapStartDate + LapStartTime or LapStartTime.

    Notes:
    - If only a timedelta is available (session elapsed), this will format that timedelta
      as an H:M:S.mmm string (not an absolute timezone-aware timestamp).
    - If both LapStartDate and LapStartTime exist, they are combined and formatted.
    """
    try:
        # If we have an absolute date + timedelta, combine and show HH:MM:SS.mmm
        if 'LapStartDate' in lap_row and pd.notna(lap_row['LapStartDate']) and 'LapStartTime' in lap_row and pd.notna(lap_row['LapStartTime']):
            dt = pd.to_datetime(lap_row['LapStartDate']) + pd.to_timedelta(lap_row['LapStartTime'])
            return dt.strftime('%H:%M:%S.%f')[:-3]

        # If we only have a timedelta (session elapsed), format it as H:M:S.mmm
        if 'LapStartTime' in lap_row and pd.notna(lap_row['LapStartTime']):
            try:
                secs = float(pd.to_timedelta(lap_row['LapStartTime']).total_seconds())
            except Exception:
                secs = float(lap_row['LapStartTime'].total_seconds()) if hasattr(lap_row['LapStartTime'], 'total_seconds') else None
            if secs is not None:
                h = int(secs // 3600) % 24
                m = int((secs % 3600) // 60)
                s = int(secs % 60)
                ms = int((secs - int(secs)) * 1000)
                return f'{h:02d}:{m:02d}:{s:02d}.{ms:03d}'
    except Exception:
        pass
    return ''

class TestingVisualizer:
    """Generate visualizations from F1 testing session data"""
    
    def __init__(self, test_number: int, day: int, source: str = 'local', meta_path: str = None):
        """
        Initialize visualizer
        
        Args:
            test_number: Test number (1 or 2)
            day: Day number (1, 2, or 3)
            source: 'local' (CSV files) or 'snowflake' (warehouse)
        """
        self.test_number = test_number
        self.day = day
        self.source = source

        # Logger
        self.logger = get_logger()

        # Load metadata from provided path or default
        if meta_path is None:
            meta_path = 'telemetry_out/session_meta.json'
        try:
            with open(meta_path, 'r', encoding='utf-8') as f:
                self.meta = json.load(f)
        except Exception:
            self.meta = {}

        # place visualizations under a folder named <Event>_<Session>_<Year>
        raw_tag = f"{self.meta.get('event_name','')}_{self.meta.get('session_name','')}_{self.meta.get('year','') }"
        # sanitize to safe folder name
        tag = re.sub(r'[^A-Za-z0-9_]', '_', raw_tag.replace(' ', '_'))
        tag = re.sub(r'_+', '_', tag).strip('_')
        self.output_dir = Path('visualizations') / tag
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        self.session_name = f"{self.meta['event_name']} - {self.meta['session_name']}"
        self.logger.info(f"\n{'='*70}")
        self.logger.info("F1 TESTING VISUALIZATIONS")
        self.logger.info(f"{'='*70}")
        self.logger.info(f"Session: {self.session_name}")
        self.logger.info(f"Date: {self.meta.get('date')}")
        self.logger.info(f"Source: {source}")
        self.logger.info(f"Output: {self.output_dir}/")
        self.logger.info(f"{'='*70}\n")
    
    def load_lap_data(self):
        """Load lap data from source"""
        if self.source == 'local':
            return self._load_local_laps()
        else:
            return self._load_snowflake_laps()
    
    def _load_local_laps(self):
        """Load laps from local CSV files"""
        # Use fixed telemetry structure for lap files
        lap_files = []
        for year_dir in Path('telemetry_out').iterdir():
            if not year_dir.is_dir():
                continue
            for session_dir in year_dir.iterdir():
                if not session_dir.is_dir():
                    continue
                for type_dir in session_dir.iterdir():
                    if not type_dir.is_dir():
                        continue
                    lap_files.extend(type_dir.glob('*_laps.csv'))
        if not lap_files:
            # Fallback: recursive search
            lap_files = list(Path('telemetry_out').rglob('*_laps.csv'))
        if not lap_files:
            raise FileNotFoundError("No lap files found in telemetry_out/")
        print(f"Loading {len(lap_files)} driver files...")
        all_laps = []
        for lap_file in lap_files:
            try:
                df = pd.read_csv(lap_file)
                # Convert all timedelta columns from string format
                time_cols = ['Time', 'LapTime', 'Sector1Time', 'Sector2Time', 'Sector3Time',
                           'Sector1SessionTime', 'Sector2SessionTime', 'Sector3SessionTime',
                           'LapStartTime', 'PitOutTime', 'PitInTime']
                for col in time_cols:
                    if col in df.columns:
                        # Convert from string format like "0 days 00:01:23.456000" to timedelta
                        df[col] = pd.to_timedelta(df[col], errors='coerce')
                all_laps.append(df)
            except Exception as e:
                self.logger.warning(f"Failed to load {lap_file.name}: {e}")
        
        laps = pd.concat(all_laps, ignore_index=True)
        
        # Convert timedelta columns to seconds for easier plotting
        timedelta_cols = ['LapTime', 'Sector1Time', 'Sector2Time', 'Sector3Time']
        for col in timedelta_cols:
            if col in laps.columns:
                sec_col = col + 'Sec'
                laps[sec_col] = laps[col].dt.total_seconds()
        
        print(f"Loaded {len(laps)} laps from {len(all_laps)} drivers\n")
        return laps
    
    def _load_snowflake_laps(self):
        """Load laps from Snowflake"""
        from snowflake_config import SNOWFLAKE_CONFIG
        import snowflake.connector
        
        # Sanitize schema name (same logic as aggregate_for_viz.py)
        import re
        raw_name = f"Pre-Season_Testing_-_Bahrain_(Test_{self.test_number})_Testing_Day_{self.day}_{self.meta['year']}"
        schema_name = re.sub(r'[^A-Za-z0-9_]', '_', raw_name)
        schema_name = re.sub(r'_+', '_', schema_name).strip('_')
        
        print(f"Connecting to Snowflake schema: {schema_name}...")
        
        conn = snowflake.connector.connect(
            account=SNOWFLAKE_CONFIG['account'],
            user=SNOWFLAKE_CONFIG['user'],
            password=SNOWFLAKE_CONFIG['password'],
            database=SNOWFLAKE_CONFIG['database'],
            warehouse=SNOWFLAKE_CONFIG['warehouse'],
            schema=schema_name,
            role=SNOWFLAKE_CONFIG['role']
        )
        
        query = "SELECT * FROM VIZ_LAP_PROGRESSION"
        laps = pd.read_sql(query, conn)
        conn.close()

        print(f"OK Loaded {len(laps)} laps from Snowflake\n")
        return laps
    
    def plot_lap_times_progression(self, laps):
        """Plot individual lap time progression for each driver"""
        print("[VIZ 1/8] Lap time progression (individual drivers)...")
        
        # Create output subdirectory for individual driver charts
        driver_dir = self.output_dir / 'lap_progression'
        driver_dir.mkdir(exist_ok=True)
        
        # Get all drivers sorted by best lap
        best_laps = laps.groupby('Driver')['LapTimeSec'].min().sort_values()
        drivers = best_laps.index.tolist()
        
        print(f"  Creating {len(drivers)} individual driver charts...")
        
        for idx, driver in enumerate(drivers, 1):
            driver_laps = laps[laps['Driver'] == driver].copy()
            valid_laps = driver_laps[driver_laps['LapTimeSec'].notna()].sort_values('LapNumber')
            # keep original best lap (before filtering)
            original_best = None
            if len(driver_laps[driver_laps['LapTimeSec'].notna()]) > 0:
                try:
                    original_best = driver_laps[driver_laps['LapTimeSec'].notna()].loc[driver_laps[driver_laps['LapTimeSec'].notna()]['LapTimeSec'].idxmin()]
                except Exception:
                    original_best = None

            # Exclude any lap whose LapTimeSec is > 120s (absolute threshold)
            before_count = len(valid_laps)
            valid_laps = valid_laps[valid_laps['LapTimeSec'] <= 120]
            excluded = before_count - len(valid_laps)

            # If original best is within the 120s threshold and was removed, re-add it; otherwise do not re-add
            if original_best is not None:
                try:
                    best_time_ok = pd.notna(original_best.get('LapTimeSec')) and float(original_best.get('LapTimeSec')) <= 120
                except Exception:
                    best_time_ok = False
                if best_time_ok:
                    if original_best['LapNumber'] not in valid_laps['LapNumber'].values:
                        valid_laps = pd.concat([valid_laps, original_best.to_frame().T], ignore_index=True, sort=False)
                        valid_laps = valid_laps.sort_values('LapNumber')

            # Exclude laps that are > 3 seconds off the driver's mean lap time
            try:
                if 'LapTimeSec' in valid_laps.columns and len(valid_laps) > 0:
                    # compute mean using numeric conversion to avoid dtype issues
                    tmp = pd.to_numeric(valid_laps['LapTimeSec'], errors='coerce')
                    if tmp.notna().any():
                        mean_time = float(tmp.mean())
                        before_mean_filter = len(valid_laps)
                        valid_laps = valid_laps[tmp.sub(mean_time).abs() <= 3.0].copy()

                        # If filtering removed the previously saved original_best but that row is within 3s of mean, re-add it
                        if original_best is not None and pd.notna(original_best.get('LapTimeSec')):
                            try:
                                orig_time = float(original_best.get('LapTimeSec'))
                                if abs(orig_time - mean_time) <= 3.0 and (original_best['LapNumber'] not in valid_laps['LapNumber'].values):
                                    valid_laps = pd.concat([valid_laps, original_best.to_frame().T], ignore_index=True, sort=False)
                                    valid_laps = valid_laps.sort_values('LapNumber')
                            except Exception:
                                pass
            except Exception:
                # if anything goes wrong with mean filtering, continue without it
                pass
            
            if len(valid_laps) == 0:
                continue
            
            # Get team for color
            team = valid_laps['Team'].iloc[0] if 'Team' in valid_laps.columns else 'Unknown'
            
            # Create figure
            fig, ax = plt.subplots(figsize=(14, 6))
            
            # Plot lap times grouped by stint, using distinct colors and lap-count annotations
            if 'Stint' in valid_laps.columns:
                # Compute compound per stint and only keep stints whose compound is MED
                stint_groups = valid_laps.groupby('Stint')
                stint_compound = {}
                for stint, sdf in stint_groups:
                    comp = None
                    # prefer per-stint explicit column when available
                    if 'StintCompound' in sdf.columns and sdf['StintCompound'].dropna().astype(str).str.strip().ne('').any():
                        try:
                            comp = str(sdf['StintCompound'].dropna().astype(str).mode().iloc[0])
                        except Exception:
                            comp = infer_stint_compound(sdf)
                    elif 'Compound' in sdf.columns and sdf['Compound'].dropna().astype(str).str.strip().ne('').any():
                        try:
                            comp = str(sdf['Compound'].dropna().astype(str).mode().iloc[0])
                        except Exception:
                            comp = infer_stint_compound(sdf)
                    else:
                        comp = infer_stint_compound(sdf)
                    stint_compound[stint] = comp

                # Preserve natural order of stints as they appear by minimum LapNumber
                stint_order = sorted([s for s in stint_compound.keys()], key=lambda x: float(valid_laps[valid_laps['Stint'] == x]['LapNumber'].min() if len(valid_laps[valid_laps['Stint'] == x])>0 else 0))

                # select MED stints (case-insensitive, prefix match)
                med_stints = [s for s in stint_order if isinstance(stint_compound.get(s), str) and stint_compound.get(s).strip().upper().startswith('MED')]

                if len(med_stints) == 0:
                    # fallback: if no stints classified as MED, try filtering whole-driver rows by Compound
                    if 'StintCompound' in valid_laps.columns:
                        med_rows = valid_laps[valid_laps['StintCompound'].astype(str).str.upper().str.startswith('MED')]
                    elif 'Compound' in valid_laps.columns:
                        med_rows = valid_laps[valid_laps['Compound'].astype(str).str.upper().str.startswith('MED')]
                        if med_rows.empty:
                            # no MED data found; draw full lap progression as before
                            ax.bar(valid_laps['LapNumber'], valid_laps['LapTimeSec'], 
                                   color='#00D2BE', alpha=0.8, edgecolor='black', linewidth=0.5)
                            legend_elements = []
                            med_stints = []
                        else:
                            valid_laps = med_rows.sort_values('LapNumber')
                            med_stints = list(valid_laps['Stint'].dropna().unique()) if 'Stint' in valid_laps.columns else []
                    else:
                        ax.bar(valid_laps['LapNumber'], valid_laps['LapTimeSec'], 
                               color='#00D2BE', alpha=0.8, edgecolor='black', linewidth=0.5)
                        legend_elements = []

                if len(med_stints) > 0:
                    # restrict valid_laps to only MED stints
                    valid_laps = valid_laps[valid_laps['Stint'].isin(med_stints)].copy()

                    # Build a robust color map keyed by string(stint) to avoid dtype mismatches
                    color_map = get_stint_color_map(med_stints)

                    # detect tyre column if present (prefer per-stint column)
                    tyre_col_candidates = ['StintCompound', 'Tyre', 'TyreCompound', 'Compound', 'TyreCompoundShort']
                    tyre_col = next((c for c in tyre_col_candidates if c in valid_laps.columns), None)

                    # Build contiguous x positions per stint so blocks are grouped visually.
                    positions = []
                    heights = []
                    bar_colors = []
                    lap_numbers = []
                    lap_time_labels = []
                    lapnum_to_pos = {}
                    current_pos = 0
                    stint_centers = {}
                    stint_tyre = {}
                    stint_pos_range = {}
                    stint_mean = {}

                    for stint in med_stints:
                        stint_laps = valid_laps[valid_laps['Stint'] == stint].sort_values('LapNumber')
                        count = len(stint_laps)
                        # skip stints reduced to 1 or 0 laps after filtering
                        if count <= 1:
                            continue
                        pos = current_pos + np.arange(count)
                        # extend positions and heights
                        for i, (_, row) in enumerate(stint_laps.iterrows()):
                            p = float(pos[i])
                            positions.append(p)
                            heights.append(float(row['LapTimeSec']))
                            ln = int(row['LapNumber']) if 'LapNumber' in row and not pd.isna(row['LapNumber']) else None
                            lap_numbers.append(ln)
                            lap_time_labels.append(format_time_of_day(row))
                            if ln is not None:
                                lapnum_to_pos[ln] = p

                        # lookup using string key for consistent colors
                        cmap_key = str(int(stint)) if (isinstance(stint, (int, float)) and float(stint).is_integer()) else str(stint)
                        bar_colors.extend([color_map.get(cmap_key, '#CCCCCC')] * count)

                        # center for annotation
                        stint_centers[stint] = float(np.mean(pos))
                        # tyre for this stint (infer if missing)
                        stint_tyre[stint] = stint_compound.get(stint, infer_stint_compound(stint_laps))
                        # record pos range and mean
                        stint_pos_range[stint] = (float(pos[0]), float(pos[-1]))
                        try:
                            stint_mean[stint] = float(stint_laps['LapTimeSec'].dropna().astype(float).mean())
                        except Exception:
                            stint_mean[stint] = float('nan')

                        # small gap after stint
                        current_pos = float(pos[-1]) + 1.5

                    # Draw bars at computed positions (guard if positions empty)
                    if len(positions) == 0:
                        # Nothing survived filtering (e.g., all MED stints had <=1 lap).
                        # Fall back to drawing full lap progression for this driver.
                        ax.bar(valid_laps['LapNumber'], valid_laps['LapTimeSec'], color='#00D2BE', alpha=0.8, edgecolor='black', linewidth=0.5)
                        bars = ax.patches
                    else:
                        bars = ax.bar(positions, heights, color=bar_colors, alpha=0.95, edgecolor='black', linewidth=0.4)

                    # draw per-stint mean lines and annotate
                    for stint in stint_pos_range:
                        mean_val = stint_mean.get(stint, None)
                        if mean_val is None or pd.isna(mean_val):
                            continue
                        start, end = stint_pos_range[stint]
                        ax.hlines(y=mean_val, xmin=start-0.4, xmax=end+0.4, colors='black', linestyles='-', linewidth=1.5, alpha=0.8)
                        cx = (start + end) / 2.0
                        try:
                            ax.text(cx, mean_val, format_laptime(mean_val), ha='center', va='bottom', fontsize=9, bbox=dict(boxstyle='round', fc='white', alpha=0.7))
                        except Exception:
                            pass

                    # Determine best lap from the (possibly re-added) valid_laps using numeric LapTimeSec
                    best_lap = None
                    try:
                        valid_laps['LapTimeSec_num'] = pd.to_numeric(valid_laps['LapTimeSec'], errors='coerce')
                        if valid_laps['LapTimeSec_num'].notna().any():
                            best_idx = valid_laps['LapTimeSec_num'].idxmin()
                            best_lap = valid_laps.loc[best_idx]
                    except Exception:
                        best_lap = None

                    # If we have a best lap, find its bar by lap number and highlight it
                    try:
                        if best_lap is not None and 'LapNumber' in best_lap and not pd.isna(best_lap['LapNumber']):
                            best_num = int(best_lap['LapNumber'])
                            if best_num in lap_numbers:
                                bar_index = lap_numbers.index(best_num)
                                bars[bar_index].set_facecolor('black')
                                bars[bar_index].set_edgecolor('yellow')
                    except Exception:
                        pass

                    # Annotate lap counts above each stint block and tyre tag vertically to the left
                    # Annotate lap counts above each stint block and tyre tag vertically to the left
                    if len(heights) > 0:
                        max_h = max(heights)
                    else:
                        # if there are no heights (we fell back to full lap bars), compute from valid_laps
                        try:
                            max_h = float(valid_laps['LapTimeSec'].max()) if 'LapTimeSec' in valid_laps.columns else 0.0
                        except Exception:
                            max_h = 0.0

                    for stint, center in stint_centers.items():
                        stint_count = len(valid_laps[valid_laps['Stint'] == stint])
                        # show laps count above and compound beneath to decluster
                        y_top = max_h * 1.01 if max_h>0 else 0.0
                        y_compound = y_top - 0.6
                        ax.text(center, y_top, f'Laps: {stint_count}', ha='center', va='bottom', fontsize=9, fontweight='bold')
                        tyre_value = stint_tyre.get(stint, 'Unknown')
                        ax.text(center, y_compound, str(tyre_value), ha='center', va='top', fontsize=8)

                    # Set x-ticks (sparse to avoid overlap) using our aligned lap_numbers + time labels
                    if len(positions) > 0:
                        keep = np.linspace(0, len(positions)-1, min(20, len(positions))).astype(int)
                        ax.set_xticks([positions[i] for i in keep])
                        # Show only lap numbers on x-axis (remove time-of-day to reduce confusion)
                        ax.set_xticklabels([f"{lap_numbers[i]}" if lap_numbers[i] is not None else '' for i in keep], rotation=45, ha='right')

                    # No separate legend for stint colors; colors shown on bars
                    legend_elements = []
            else:
                ax.bar(valid_laps['LapNumber'], valid_laps['LapTimeSec'], 
                      color='#00D2BE', alpha=0.8, edgecolor='black', linewidth=0.5)
            
            # Ensure best lap is computed numerically (covers both stint and non-stint cases)
            try:
                valid_laps['LapTimeSec_num'] = pd.to_numeric(valid_laps['LapTimeSec'], errors='coerce')
                if valid_laps['LapTimeSec_num'].notna().any():
                    best_idx = valid_laps['LapTimeSec_num'].idxmin()
                    best_lap = valid_laps.loc[best_idx]
                else:
                    best_lap = None
            except Exception:
                best_lap = None

            best_time_str = format_laptime(best_lap['LapTimeSec']) if best_lap is not None else ''

            # Determine x coordinate for the best lap marker using lap_numbers -> positions mapping if available
            best_x = None
            try:
                if best_lap is not None and 'LapNumber' in best_lap and not pd.isna(best_lap['LapNumber']):
                    best_num = int(best_lap['LapNumber'])
                    if 'lap_numbers' in locals() and best_num in lap_numbers:
                        bar_index = lap_numbers.index(best_num)
                        # Use the x-coordinate of that bar
                        best_x = positions[bar_index]
                    else:
                        # Fallback to using lap number as x
                        best_x = best_num
                else:
                    best_x = None
            except Exception:
                best_x = None

            if best_x is not None:
                ax.scatter([best_x], [best_lap['LapTimeSec']],
                          color='red', s=200, marker='*', zorder=6,
                          linewidths=1.5, edgecolors='darkred')
                # Add a small annotation next to the star so static images carry the info
                try:
                    time_of_day = format_time_of_day(best_lap)
                    ann_text = f"Best: {best_time_str}\nLap {int(best_lap['LapNumber'])}" + (f"\n{time_of_day}" if time_of_day else "")
                    ax.annotate(ann_text, xy=(best_x, best_lap['LapTimeSec']), xytext=(8, 8), textcoords='offset points',
                                bbox=dict(boxstyle='round', fc='yellow', alpha=0.85), fontsize=9)
                except Exception:
                    pass
            
            # Calculate statistics
            median_time = valid_laps['LapTimeSec'].median()
            median_time_str = format_laptime(median_time)
            ax.axhline(y=median_time, color='gray', linestyle='--', alpha=0.6, 
                      linewidth=2)
            
            # Formatting
            ax.set_xlabel('Lap Number', fontsize=13, fontweight='bold')
            ax.set_ylabel('Lap Time', fontsize=13, fontweight='bold')
            ax.set_title(f'{driver} - {team}\n{self.session_name}', 
                        fontsize=15, fontweight='bold', pad=20)
            
            # Format y-axis to show MM:SS.mmm
            ax.yaxis.set_major_formatter(FuncFormatter(format_laptime))
            
            # Create legend
            if 'Stint' in valid_laps.columns:
                legend_elements.append(plt.Line2D([0], [0], marker='*', color='w', 
                                                 markerfacecolor='red', markersize=12, 
                                                 label=f'Best: {best_time_str}', 
                                                 markeredgecolor='darkred', markeredgewidth=1.5))
                legend_elements.append(plt.Line2D([0], [0], color='gray', linestyle='--', 
                                                 label=f'Median: {median_time_str}', linewidth=2))
                ax.legend(handles=legend_elements, loc='upper right', fontsize=10, framealpha=0.9)
            else:
                ax.legend([f'Best: {best_time_str}', f'Median: {median_time_str}'], 
                         loc='upper right', fontsize=10, framealpha=0.9)
            
            ax.grid(True, alpha=0.3, linestyle='--', axis='y')

            # Add lap count annotation (do not show excluded counter)
            info_text = f'Total Laps: {len(valid_laps)}'
            ax.text(0.02, 0.98, info_text, 
                    transform=ax.transAxes, fontsize=11, verticalalignment='top',
                    bbox=dict(boxstyle='round', facecolor='wheat', alpha=0.5))
            
            # Save individual chart
            output_file = driver_dir / f'{idx:02d}_{driver}_lap_progression.png'
            plt.savefig(output_file, dpi=300, bbox_inches='tight')
            plt.close()
        
        print(f"  OK Saved {len(drivers)} driver charts to: {driver_dir}/")
        
        # Also create a comparison overview with top 8 drivers
        print("  Creating top 13 comparison overview...")
        self._create_lap_progression_overview(laps, best_laps.head(13).index.tolist())
    
    def _create_lap_progression_overview(self, laps, top_drivers):
        """Create a 2x4 grid showing top 8 drivers for quick comparison
        and save individual per-driver charts using the same per-stint style.
        """
        # allow variable-size grids (use up to 5 columns so top 13 fits neatly into 3x5)
        n = len(top_drivers)
        cols = 5
        rows = int(np.ceil(n / cols)) if n > 0 else 1
        fig, axes = plt.subplots(rows, cols, figsize=(4 * cols, 4 * rows))
        axes = axes.flatten()

        def _draw_stint_style(ax, driver, driver_laps, pos_title=None, apply_outlier_filter=False):
            """Draw per-stint colored bars, compound labels and mean lines on `ax`."""
            laps_df = driver_laps.copy()
            # ensure LapSeconds
            if 'LapTime' in laps_df.columns and pd.api.types.is_timedelta64_dtype(laps_df['LapTime']):
                laps_df['LapSeconds'] = laps_df['LapTime'].dt.total_seconds()
            else:
                laps_df['LapSeconds'] = laps_df.get('LapTimeSec', pd.Series(dtype=float))

            # exclude any very long laps (> 120s) from plotting and mean calculations
            laps_df = laps_df[laps_df['LapSeconds'] <= 120]

            if 'Stint' in laps_df.columns and len(laps_df['Stint'].dropna().unique()) > 0:
                # initial stint summary (used to identify mean for outlier filtering)
                stint_info = (
                    laps_df.groupby('Stint')
                        .apply(lambda x: pd.Series({
                            'first_lap': x['LapNumber'].min(),
                            'last_lap':  x['LapNumber'].max(),
                            # Use infer_stint_compound which handles StintCompound/Compound/FreshTyre heuristics
                            'compound':  infer_stint_compound(x),
                            'mean_sec':  x['LapSeconds'].mean(),
                            'n_laps':    len(x)
                        }))
                )

                # optionally remove per-stint outliers beyond an absolute 3s deviation from the stint mean
                laps_plot = laps_df
                if apply_outlier_filter:
                    keep_idx = []
                    for stint in stint_info.index:
                        try:
                            mean_sec = float(stint_info.loc[stint, 'mean_sec']) if not pd.isna(stint_info.loc[stint, 'mean_sec']) else None
                        except Exception:
                            mean_sec = None
                        stint_rows = laps_df[laps_df['Stint'] == stint]
                        if mean_sec is None:
                            keep_idx.extend(stint_rows.index.tolist())
                        else:
                            # keep only laps within +/-3.0 seconds of the stint mean
                            allowed = stint_rows[stint_rows['LapSeconds'].sub(mean_sec).abs() <= 3.0].index.tolist()
                            keep_idx.extend(allowed)
                    if len(keep_idx) > 0:
                        laps_plot = laps_df.loc[sorted(set(keep_idx))]

                # recompute stint summary for plotted laps
                stint_info_plot = (
                    laps_plot.groupby('Stint')
                        .apply(lambda x: pd.Series({
                            'first_lap': x['LapNumber'].min(),
                            'last_lap':  x['LapNumber'].max(),
                            # Always derive compound via infer_stint_compound to avoid taking a possibly-NaN first value
                            'compound':  infer_stint_compound(x),
                            'mean_sec':  x['LapSeconds'].mean(),
                            'n_laps':    len(x)
                        }))
                )

                # remove stints reduced to 1 or fewer laps after filtering
                stint_info_plot = stint_info_plot[stint_info_plot['n_laps'] > 1]
                if len(stint_info_plot) == 0:
                    ax.text(0.5, 0.5, 'No stints to display after filtering', ha='center')
                else:
                    base_colors = list(plt.cm.tab10.colors)
                    color_map = {st: base_colors[i % len(base_colors)] for i, st in enumerate(stint_info_plot.index)}

                    # restrict plotted laps to remaining stints
                    laps_plot = laps_plot[laps_plot['Stint'].isin(stint_info_plot.index)]

                    ax.bar(laps_plot['LapNumber'], laps_plot['LapSeconds'],
                           color=laps_plot['Stint'].map(color_map), edgecolor='black', linewidth=0.4)

                    ax.yaxis.set_major_formatter(FuncFormatter(format_laptime))
                    ax.set_xlabel('Lap Number')
                    ax.set_ylabel('Lap Time')

                    ymin, ymax = ax.get_ylim()
                    ax.set_ylim(ymin, ymax + 2)
                    text_y = ax.get_ylim()[1] - 0.5

                    for stint, row in stint_info_plot.iterrows():
                        first_lap = int(row['first_lap'])
                        last_lap = int(row['last_lap'])
                        compound = row['compound']
                        mean_sec = float(row['mean_sec']) if not pd.isna(row['mean_sec']) else None
                        n_laps = int(row['n_laps'])

                        x_mid = (first_lap + last_lap) / 2.0
                        # show laps count above and compound beneath to decluster
                        ax.text(x_mid, text_y, f'Laps: {n_laps}', ha='center', va='bottom', fontsize=9, fontweight='bold')
                        ax.text(x_mid, text_y - 0.6, str(compound), ha='center', va='top', fontsize=8)
                        if mean_sec is not None:
                            ax.hlines(mean_sec, xmin=first_lap - 0.4, xmax=last_lap + 0.4, colors='k', linestyles='--', linewidth=1.5)
                            # tooltip-like label showing mean formatted
                            try:
                                ax.text(last_lap + 0.6, mean_sec, format_laptime(mean_sec), va='center', ha='left', fontsize=8, bbox=dict(boxstyle='round', fc='white', alpha=0.7))
                            except Exception:
                                pass
            else:
                ax.bar(laps_df['LapNumber'], laps_df['LapSeconds'], color='#00D2BE', alpha=0.8, edgecolor='black', linewidth=0.3)

            if pos_title:
                ax.set_title(pos_title, fontsize=11, fontweight='bold')

        # draw overview grid
        for idx, driver in enumerate(top_drivers):
            ax = axes[idx]
            driver_laps = laps[laps['Driver'] == driver].copy()
            valid_laps = driver_laps[driver_laps['LapTime'].notna()].sort_values('LapNumber') if 'LapTime' in driver_laps.columns else driver_laps[driver_laps['LapTimeSec'].notna()].sort_values('LapNumber')
            if len(valid_laps) == 0:
                continue
            best_time = valid_laps['LapTime'].min() if 'LapTime' in valid_laps.columns else (valid_laps['LapTimeSec'].min() if 'LapTimeSec' in valid_laps.columns else None)
            best_time_str = format_laptime(best_time) if best_time is not None else ''
            team = valid_laps['Team'].iloc[0] if 'Team' in valid_laps.columns else ''
            _draw_stint_style(ax, driver, valid_laps, pos_title=f'P{idx+1}: {driver} - {team}\nBest: {best_time_str}')

        fig.suptitle(f'{self.session_name}\nTop 8 Drivers - Lap Progression Comparison', fontsize=16, fontweight='bold', y=0.995)
        plt.tight_layout()
        output_file = self.output_dir / '01_lap_progression_top8.png'
        plt.savefig(output_file, dpi=300, bbox_inches='tight')
        plt.close()
        print(f"  OK Saved overview: {output_file}")

        # save individual per-driver charts using the same style
        for driver in top_drivers:
            driver_laps = laps[laps['Driver'] == driver].copy()
            valid_laps = driver_laps[driver_laps['LapTime'].notna()].sort_values('LapNumber') if 'LapTime' in driver_laps.columns else driver_laps[driver_laps['LapTimeSec'].notna()].sort_values('LapNumber')
            if len(valid_laps) == 0:
                continue
            fig, ax = plt.subplots(figsize=(12, 6))
            team = valid_laps['Team'].iloc[0] if 'Team' in valid_laps.columns else ''
            _draw_stint_style(ax, driver, valid_laps, pos_title=f'{driver} - {team}\nBest: {format_laptime(valid_laps["LapTime"].min()) if "LapTime" in valid_laps.columns else format_laptime(valid_laps["LapTimeSec"].min())}', apply_outlier_filter=True)
            out_file = self.output_dir / f'01_lap_progression_top8_{driver}.png'
            plt.tight_layout()
            plt.savefig(out_file, dpi=300, bbox_inches='tight')
            plt.close()
            print(f'  OK Saved top8 individual: {out_file}')
    
    def plot_best_lap_comparison(self, laps):
        """Plot best lap comparison"""
        print("[VIZ 2/8] Best lap comparison...")
        
        valid_laps = laps[laps['LapTime'].notna()].copy()
        valid_laps['LapTimeSec'] = valid_laps['LapTime'].dt.total_seconds()
        
        # Get each driver's best lap
        best_laps = valid_laps.groupby('Driver')['LapTimeSec'].min().sort_values()
        
        fig, ax = plt.subplots(figsize=(12, 8))
        # Plot delta-to-best (seconds) instead of absolute seconds
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
        ax.set_title(f'{self.session_name}\nBest Lap Times (Δ to fastest)', fontsize=14, fontweight='bold')
        ax.invert_yaxis()
        ax.grid(True, axis='x', alpha=0.3)

        # Add +delta labels on bars (show +0.000s for the fastest)
        for i, delta in enumerate(deltas):
            label = f'+{delta:.3f}s'
            # place label at end of bar (small offset)
            ax.text(delta + 0.01, i, label, va='center', fontsize=9)
        
        plt.tight_layout()
        output_file = self.output_dir / '02_best_laps.png'
        plt.savefig(output_file, dpi=300, bbox_inches='tight')
        plt.close()
        print(f"  Saved: {output_file}")

    def plot_best_two_overlay(self, laps, telemetry_traces=None):
        """Plot speed vs distance for the best lap of the top 2 drivers.

        Produces a dark-background 2D line graph where X is distance in meters
        and Y is speed in km/h. Attempts to load circuit corner distances from
        `telemetry_out/circuit_corners.csv` and marks them as points.

        Expected input (constructed within this function):
          - distance_m: 1D numpy array (meters)
          - speed_<driver>_kmh: 1D numpy array (km/h), same length
        """
        print("[VIZ 2b] Best-two distance vs speed overlay...")

        # Ensure LapTimeSec is available
        if 'LapTimeSec' not in laps.columns:
            if 'LapTime' in laps.columns:
                laps['LapTimeSec'] = laps['LapTime'].dt.total_seconds()
            else:
                laps['LapTimeSec'] = pd.to_numeric(laps.get('LapTimeSec', pd.Series(dtype=float)), errors='coerce')

        best_laps = laps.groupby('Driver')['LapTimeSec'].min().sort_values()
        if len(best_laps) < 2:
            self.logger.warning("Not enough drivers for overlay, skipping...")
            return

        top2 = best_laps.index.tolist()[:2]
        driver_colors = ['#FF00FF', '#00FF66']

        # Load corner distances if available
        corner_file = Path('telemetry_out') / 'circuit_corners.csv'
        corner_distances = None
        corner_labels = None
        if corner_file.exists():
            try:
                cdf = pd.read_csv(corner_file)
                if 'Distance' in cdf.columns:
                    corner_distances = cdf['Distance'].astype(float).values
                    corner_labels = cdf.get('Number', None).astype(str).values if 'Number' in cdf.columns else None
                    # normalize to meters if necessary
                    if corner_distances.max() < 1000:
                        corner_distances = corner_distances * 1000.0

                    # if the distances look wildly out of expected bounds,
                    # do not apply automatic scaling — warn and keep values.
                    try:
                        maxd_raw = float(np.nanmax(corner_distances))
                        MIN_LAP_M = 1500.0
                        MAX_LAP_M = 15000.0
                        if not (MIN_LAP_M <= maxd_raw <= MAX_LAP_M):
                            try:
                                self.logger.warning(
                                    f"Circuit corner distances appear out of range: max={maxd_raw:.3f}.\n"
                                    "Expected lap length between 1500m and 15000m.\n"
                                    "Using corner indices on X axis may be safer."
                                )
                            except Exception:
                                pass
                    except Exception:
                        pass
            except Exception:
                corner_distances = None

            # Decide whether to use turn-index X axis or real distances
            try:
                if corner_distances is None:
                    use_turn_index = True
                else:
                    maxd = float(np.nanmax(corner_distances))
                    if 1500.0 <= maxd <= 15000.0:
                        use_turn_index = False
                    else:
                        use_turn_index = True
            except Exception:
                use_turn_index = True

        fig, ax = plt.subplots(figsize=(14, 6))
        fig.patch.set_facecolor('#0b0b0b')
        ax.set_facecolor('#0b0b0b')
        ax.tick_params(colors='white')
        ax.xaxis.label.set_color('white')
        ax.yaxis.label.set_color('white')
        ax.title.set_color('white')

        # Collect apex-speed traces for both drivers, aligned to a common X axis
        traces = []  # list of dicts: {'driver': name, 'lap_sec': val, 'x_pts': arr, 'y_pts': arr}
        annotations = []
        for i, driver in enumerate(top2):
            driver_rows = laps[laps['Driver'] == driver]
            if driver_rows.empty:
                self.logger.warning(f"No lap rows found for driver {driver}, skipping")
                continue
            try:
                best_idx = driver_rows['LapTimeSec'].idxmin()
                best = driver_rows.loc[best_idx]
            except Exception:
                best = driver_rows.iloc[0]

            # Attempt to isolate per-turn corner rows for the driver's best lap and prefer ApexSpeed
            got_trace = False
            x_pts = None
            y_pts = None
            try:
                corners_dir = Path('telemetry_out')
                corner_candidates = list(corners_dir.glob(f"*_{driver}_corners.csv"))
                if not corner_candidates:
                    corner_candidates = list(corners_dir.glob(f"*{driver}*_corners.csv"))

                for cc in corner_candidates:
                    try:
                        cdf = pd.read_csv(cc)
                    except Exception:
                        continue

                    # pick best lap number for this driver
                    best_row = driver_rows.sort_values('LapTimeSec').head(1)
                    lapnum = None
                    if not best_row.empty and 'LapNumber' in best_row.columns:
                        try:
                            lapnum = int(best_row.iloc[0].get('LapNumber'))
                        except Exception:
                            lapnum = None

                    if lapnum is None or 'LapNumber' not in cdf.columns:
                        continue

                    sel = cdf[cdf['LapNumber'] == lapnum].copy()
                    if sel is None or sel.empty:
                        continue

                    # prefer ApexSpeed, then Entry/Exit, then generic speed
                    spc = next((c for c in ['ApexSpeed', 'ExitSpeed', 'EntrySpeed', 'Speed', 'Speed_kmh'] if c in sel.columns), None)
                    if spc is None:
                        continue

                    xs_list = []
                    ys_list = []
                    # Determine base corner index offset so first corner starts at 0
                    corner_numbers = []
                    for _, crow in sel.iterrows():
                        cnum = crow.get('Corner') if 'Corner' in sel.columns else None
                        try:
                            cnum_i = int(cnum) if pd.notna(cnum) else None
                        except Exception:
                            cnum_i = None
                        corner_numbers.append(cnum_i)

                    min_corner = None
                    try:
                        nums = [n for n in corner_numbers if n is not None]
                        if nums:
                            min_corner = min(nums)
                    except Exception:
                        min_corner = None

                    corner_index = 0
                    for _, crow in sel.iterrows():
                        cnum = crow.get('Corner') if 'Corner' in sel.columns else None
                        try:
                            cnum_i = int(cnum) if pd.notna(cnum) else None
                        except Exception:
                            cnum_i = None

                        if cnum_i is not None and min_corner is not None:
                            pos = float(cnum_i - min_corner)
                        else:
                            pos = float(corner_index)

                        spv = pd.to_numeric(crow.get(spc), errors='coerce')
                        corner_index += 1
                        if pd.isna(spv):
                            continue
                        xs_list.append(pos)
                        ys_list.append(float(spv))

                    if len(xs_list) < 2:
                        continue

                    # convert to numpy arrays and convert m/s->km/h if needed
                    x_pts = np.asarray(xs_list, dtype=float)
                    y_pts = np.asarray(ys_list, dtype=float)
                    try:
                        if np.nanmax(y_pts) < 90:
                            y_pts = y_pts * 3.6
                    except Exception:
                        pass

                    try:
                        self.logger.info(f"Using {len(x_pts)} corner rows for driver {driver} lap {lapnum} (apex speed column: {spc})")
                    except Exception:
                        pass

                    got_trace = True
                    break
            except Exception:
                got_trace = False

            if not got_trace:
                # fallback: try telemetry_traces if provided
                if telemetry_traces and driver in telemetry_traces:
                    try:
                        x_tmp, y_tmp = telemetry_traces[driver]
                        x_pts = np.asarray(x_tmp, dtype=float)
                        y_pts = np.asarray(y_tmp, dtype=float)
                    except Exception:
                        x_pts = None
                        y_pts = None

            if x_pts is None or y_pts is None:
                # As a last resort derive from lap rows (coarse)
                if 'Distance' in driver_rows.columns and any(driver_rows['Distance'].notna()):
                    distance = pd.to_numeric(driver_rows['Distance'], errors='coerce').fillna(method='ffill').fillna(0.0).astype(float).values
                    speed_col = next((c for c in ['Speed_kmh', 'Speed', 'SpeedKMH', 'Speed_km/h'] if c in driver_rows.columns), None)
                    if speed_col is None:
                        self.logger.warning(f"No speed data for driver {driver}, skipping")
                        continue
                    speeds = pd.to_numeric(driver_rows[speed_col], errors='coerce').fillna(0.0).astype(float).values
                    x_pts = np.asarray(distance, dtype=float)
                    y_pts = np.asarray(speeds, dtype=float)
                    try:
                        if np.nanmax(y_pts) < 90:
                            y_pts = y_pts * 3.6
                    except Exception:
                        pass
                else:
                    self.logger.warning(f"No corner or telemetry data available for {driver}, skipping")
                    continue

            # Normalize so every trace starts at 0
            try:
                x_pts = x_pts - float(np.nanmin(x_pts))
            except Exception:
                pass

            traces.append({'driver': driver, 'lap_sec': float(best.get('LapTimeSec', 0)), 'x_pts': x_pts, 'y_pts': y_pts})

        if not traces:
            self.logger.warning("Not enough valid data to plot best-two overlay")
            plt.close()
            return

        # Build a common X axis (index-based) starting at 0 and spanning the largest x
        try:
            x_max = max([t['x_pts'].max() for t in traces if len(t['x_pts']) > 0])
        except Exception:
            x_max = 0.0
        if x_max <= 0:
            x_common = np.linspace(0.0, 1.0, 800)
        else:
            x_common = np.linspace(0.0, float(x_max), 800)

        plotted_any = False
        # Interpolate each trace onto x_common, smooth and plot
        for i, t in enumerate(traces):
            try:
                from scipy.interpolate import interp1d
                f = interp1d(t['x_pts'], t['y_pts'], kind='cubic', bounds_error=False, fill_value='extrapolate')
                y_common = f(x_common)
            except Exception:
                # fallback to numpy interpolation
                try:
                    y_common = np.interp(x_common, t['x_pts'], t['y_pts'])
                except Exception:
                    continue

            # smooth
            try:
                from scipy.signal import savgol_filter
                wl = min(51, len(y_common) - (1 - len(y_common) % 2))
                if wl % 2 == 0:
                    wl = max(3, wl-1)
                if wl >= 5:
                    y_smooth = savgol_filter(y_common, window_length=wl, polyorder=3)
                else:
                    y_smooth = y_common
            except Exception:
                window = min(11, max(3, len(y_common)//40))
                kernel = np.ones(window) / window
                y_smooth = np.convolve(y_common, kernel, mode='same')

            label = f"{t['driver']} ({t['lap_sec']:.3f}s)"
            ax.plot(x_common, y_smooth, label=label, color=driver_colors[i], linewidth=1.2)
            try:
                max_idx = int(np.nanargmax(y_smooth))
                annotations.append((x_common[max_idx], float(y_smooth[max_idx]), t['driver']))
                ax.scatter([x_common[max_idx]], [float(y_smooth[max_idx])], color=driver_colors[i], s=30)
            except Exception:
                pass

            plotted_any = True

        if not plotted_any:
            self.logger.warning("Not enough valid data to plot best-two overlay")
            plt.close()
            return

        # mark corners: if using turn-index plotting, draw vertical lines at
        # integer corner positions and label them with corner numbers.
        if corner_distances is not None and not use_turn_index:
            for cd in corner_distances:
                ax.axvline(cd, color='white', alpha=0.08, linewidth=1)
            if corner_labels is not None:
                for cd, lab in zip(corner_distances, corner_labels):
                    ax.text(cd, ax.get_ylim()[1]*0.98, lab, color='white', fontsize=7, rotation=90, va='top', ha='center')
        elif corner_distances is not None and use_turn_index:
            # integer positions from 1..N
            if corner_labels is not None:
                try:
                    n = len(corner_labels)
                    xticks = np.arange(1, n+1)
                    for xt in xticks:
                        ax.axvline(xt, color='white', alpha=0.08, linewidth=1)
                    for xt, lab in zip(xticks, corner_labels):
                        ax.text(xt, ax.get_ylim()[1]*0.98, lab, color='white', fontsize=7, rotation=90, va='top', ha='center')
                    ax.set_xticks(xticks)
                    ax.set_xticklabels(corner_labels, rotation=90, fontsize=7)
                except Exception:
                    pass

        for x, y, drv in annotations:
            ax.text(x, y + 2.0, f"{drv}: {y:.1f} km/h", color='white', fontsize=8, ha='center')

        ax.set_xlabel('Distance (m)')
        ax.set_ylabel('Speed (km/h)')
        ax.set_title('Top 2 Drivers - Best Lap Speed Overlay')
        ax.legend(loc='upper right')
        ax.grid(alpha=0.15, color='white')
        plt.tight_layout()
        output_file = self.output_dir / '02_best_two_overlay.png'
        plt.savefig(output_file, dpi=300, bbox_inches='tight')
        plt.close()
        self.logger.info(f"Saved: {output_file}")

        # Optional: create a LightningChart interactive chart if available.
        # This is non-blocking and will not raise if lightningchart is not installed.
        try:
            try:
                import lightningchart as lc
            except Exception:
                try:
                    import lcpy as lc
                except Exception:
                    lc = None

            if lc is not None:
                try:
                    # Create a ChartXY and add the two driver series
                    chart = lc.ChartXY(title=f"Top 2 Drivers - {self.session_name}")
                    # Use the same colors and labels
                    for i, driver in enumerate(top2):
                        # retrieve the plotted series from matplotlib if available
                        # fall back to recomputing arrays
                        if 'x_dense' in locals() and 'y_smooth' in locals():
                            xs = x_dense.tolist() if hasattr(x_dense, 'tolist') else list(x_dense)
                            ys = y_smooth.tolist() if hasattr(y_smooth, 'tolist') else list(y_smooth)
                        else:
                            # recompute minimal series for LightningChart
                            drv_rows = laps[laps['Driver'] == driver]
                            xs = (drv_rows['Distance'].astype(float).values.tolist() if 'Distance' in drv_rows.columns else list(np.linspace(0, 5400, num=len(drv_rows))))
                            spc = next((c for c in ['Speed_kmh', 'Speed', 'SpeedKMH', 'Speed_km/h'] if c in drv_rows.columns), None)
                            ys = pd.to_numeric(drv_rows[spc], errors='coerce').fillna(0.0).astype(float).values.tolist() if spc is not None else [0.0]*len(xs)

                        series = chart.add_line_series()
                        try:
                            series.add(xs, ys)
                        except Exception:
                            # Some LightningChart versions expect dicts or different calls; try add_dict_data
                            try:
                                data = [{'x': float(x), 'y': float(y)} for x, y in zip(xs, ys)]
                                series.add_dict_data(data)
                            except Exception:
                                pass

                    # Attempt to export if supported
                    if hasattr(chart, 'save_image'):
                        try:
                            lc_out = output_file.with_suffix('.lc.png')
                            chart.save_image(str(lc_out))
                            print(f"  Saved LightningChart image: {lc_out}")
                        except Exception:
                            pass
                    else:
                        # Do not open interactive chart in automated runs
                        self.logger.info('LightningChart available: created chart object (not opened).')
                except Exception:
                    # Do not fail visualizations if LightningChart usage errors
                    pass
        except Exception:
            pass
            
    
    def plot_sector_performance(self, laps):
        """Plot sector time comparison"""
        print("[VIZ 3/8] Sector performance...")
        
        valid_laps = laps[
            laps['Sector1Time'].notna() & 
            laps['Sector2Time'].notna() & 
            laps['Sector3Time'].notna()
        ].copy()
        
        if len(valid_laps) == 0:
            self.logger.warning("No sector data available, skipping...")
            return
        
        # Calculate sector times in seconds
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
        sector_df = sector_df.sort_values('Total')
        
        fig, ax = plt.subplots(figsize=(12, 8))
        
        x = np.arange(len(sector_df))
        width = 0.25
        
        ax.bar(x - width, sector_df['Sector1'], width, label='Sector 1', alpha=0.8)
        ax.bar(x, sector_df['Sector2'], width, label='Sector 2', alpha=0.8)
        ax.bar(x + width, sector_df['Sector3'], width, label='Sector 3', alpha=0.8)
        
        ax.set_xticks(x)
        ax.set_xticklabels(sector_df.index, rotation=45, ha='right')
        ax.set_ylabel('Best Sector Time (seconds)', fontsize=12)
        ax.set_title(f'{self.session_name}\nBest Sector Times', fontsize=14, fontweight='bold')
        ax.legend()
        ax.grid(True, axis='y', alpha=0.3)
        
        plt.tight_layout()
        output_file = self.output_dir / '03_sector_performance.png'
        plt.savefig(output_file, dpi=300, bbox_inches='tight')
        plt.close()
        print(f"  Saved: {output_file}")
    
    def plot_speed_comparison(self, laps):
        """Plot speed trap comparison"""
        print("[VIZ 4/8] Speed trap comparison...")
        
        # Check which speed columns are available
        speed_cols = [col for col in ['SpeedI1', 'SpeedI2', 'SpeedST'] 
                 if col in laps.columns and laps[col].notna().any()]
        
        if not speed_cols:
            self.logger.warning("No speed data available, skipping...")
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
            
            # Add speed labels
            for i, (driver, speed) in enumerate(max_speeds.items()):
                axes[idx].text(speed + 1, i, f'{speed:.1f}', va='center', fontsize=9)
        
        fig.suptitle(f'{self.session_name}\nSpeed Trap Comparison', 
                    fontsize=14, fontweight='bold', y=1.02)
        plt.tight_layout()
        output_file = self.output_dir / '04_speed_comparison.png'
        plt.savefig(output_file, dpi=300, bbox_inches='tight')
        plt.close()
        print(f"  Saved: {output_file}")
    
    def plot_consistency(self, laps):
        """Plot lap time consistency"""
        print("[VIZ 5/8] Lap time consistency...")
        
        valid_laps = laps[laps['LapTime'].notna()].copy()
        # Robust conversion to seconds - lap time may already be timedelta or numeric
        try:
            if pd.api.types.is_timedelta64_dtype(valid_laps['LapTime']):
                valid_laps['LapTimeSec'] = valid_laps['LapTime'].dt.total_seconds()
            else:
                valid_laps['LapTimeSec'] = pd.to_timedelta(valid_laps['LapTime']).dt.total_seconds()
        except Exception:
            # Fallback: coerce to numeric seconds where possible
            valid_laps['LapTimeSec'] = pd.to_numeric(valid_laps['LapTime'], errors='coerce')

        # Calculate consistency metrics
        consistency_data = []
        for driver in valid_laps['Driver'].unique():
            driver_laps = valid_laps[valid_laps['Driver'] == driver]
            if len(driver_laps) >= 3:  # Need at least 3 laps
                consistency_data.append({
                    'Driver': driver,
                    'Mean': driver_laps['LapTimeSec'].mean(),
                    'StdDev': driver_laps['LapTimeSec'].std(),
                    'Min': driver_laps['LapTimeSec'].min(),
                    'Max': driver_laps['LapTimeSec'].max(),
                    'Laps': len(driver_laps)
                })

        consistency_df = pd.DataFrame(consistency_data)
        if consistency_df.empty or 'StdDev' not in consistency_df.columns:
            self.logger.warning('Not enough data for consistency plot, skipping...')
            return
        consistency_df = consistency_df.sort_values('StdDev')
        
        fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(16, 8))
        
        # Plot 1: Standard deviation (consistency)
        colors = plt.cm.RdYlGn(np.linspace(0.2, 0.8, len(consistency_df)))
        ax1.barh(range(len(consistency_df)), consistency_df['StdDev'], color=colors)
        ax1.set_yticks(range(len(consistency_df)))
        ax1.set_yticklabels(consistency_df['Driver'])
        ax1.set_xlabel('Std Deviation (seconds)', fontsize=12)
        ax1.set_title('Lap Time Consistency\n(Lower = More Consistent)', fontsize=12, fontweight='bold')
        ax1.invert_yaxis()
        ax1.grid(True, axis='x', alpha=0.3)
        
        # Plot 2: Box plot of lap times
        box_data = []
        box_labels = []
        for _, row in consistency_df.iterrows():
            driver = row['Driver']
            driver_times = valid_laps[valid_laps['Driver'] == driver]['LapTimeSec']
            box_data.append(driver_times)
            box_labels.append(f"{driver}\n({row['Laps']} laps)")
        
        bp = ax2.boxplot(box_data, labels=box_labels, patch_artist=True, vert=False)
        for patch, color in zip(bp['boxes'], colors):
            patch.set_facecolor(color)
        
        ax2.set_xlabel('Lap Time (seconds)', fontsize=12)
        ax2.set_title('Lap Time Distribution', fontsize=12, fontweight='bold')
        ax2.grid(True, axis='x', alpha=0.3)
        
        fig.suptitle(f'{self.session_name}\nDriver Consistency Analysis', 
                    fontsize=14, fontweight='bold', y=1.0)
        plt.tight_layout()
        output_file = self.output_dir / '05_consistency.png'
        plt.savefig(output_file, dpi=300, bbox_inches='tight')
        plt.close()
        print(f"  Saved: {output_file}")
    
    def plot_stint_analysis(self, laps):
        """Plot stint-based lap time analysis"""
        print("[VIZ 6/8] Stint analysis...")
        
        valid_laps = laps[laps['LapTime'].notna() & laps['Stint'].notna()].copy()
        
        if len(valid_laps) == 0:
            self.logger.warning("No stint data available, skipping...")
            return
        
        valid_laps['LapTimeSec'] = valid_laps['LapTime'].dt.total_seconds()
        # Plot top 5 drivers by total laps
        top_drivers = valid_laps['Driver'].value_counts().head(5).index

        fig, axes = plt.subplots(len(top_drivers), 1, figsize=(14, 4*len(top_drivers)))
        if len(top_drivers) == 1:
            axes = [axes]

        for idx, driver in enumerate(top_drivers):
            driver_laps = valid_laps[valid_laps['Driver'] == driver].sort_values('LapNumber')

            # Remove laps more than 120s over the driver's median
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

            # Convert to bar-layout grouped by stint (same principal as lap progression)
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

            # Draw bars
            bars = axes[idx].bar(positions, heights, color=bar_colors, alpha=0.95, edgecolor='black', linewidth=0.4)

            # Highlight best lap
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

            # Annotate lap counts and tyre vertically
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

            # Set sparse x-ticks using positions
            if len(positions) > 0:
                keep = np.linspace(0, len(positions)-1, min(12, len(positions))).astype(int)
                axes[idx].set_xticks([positions[i] for i in keep])
                axes[idx].set_xticklabels([f"{lap_numbers[i]}" if lap_numbers[i] is not None else '' for i in keep], rotation=45, ha='right')

            axes[idx].set_ylabel('Lap Time', fontsize=11)
            axes[idx].yaxis.set_major_formatter(FuncFormatter(format_laptime))
            axes[idx].set_title(f'{driver}', fontsize=12, fontweight='bold')
            axes[idx].grid(True, alpha=0.3, axis='y')

        axes[-1].set_xlabel('Lap Number (grouped by stint)', fontsize=12)
        fig.suptitle(f'{self.session_name}\nStint-by-Stint Analysis (Top 5 Drivers)', fontsize=14, fontweight='bold', y=1.02)
        plt.tight_layout()
        output_file = self.output_dir / '06_stint_analysis.png'
        plt.savefig(output_file, dpi=300, bbox_inches='tight')
        plt.close()
        print(f"  Saved: {output_file}")
    
    def plot_team_comparison(self, laps):
        """Plot team-based comparison"""
        print("[VIZ 7/8] Team comparison...")
        
        valid_laps = laps[laps['LapTime'].notna() & laps['Team'].notna()].copy()
        
        if len(valid_laps) == 0:
            self.logger.warning("No team data available, skipping...")
            return
        
        valid_laps['LapTimeSec'] = valid_laps['LapTime'].dt.total_seconds()
        
        # Get best lap per team
        team_best = valid_laps.groupby('Team')['LapTimeSec'].min().sort_values()
        
        fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(16, 8))
        
        # Plot 1: Best lap per team
        # Plot delta-to-best team (seconds)
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

        # Label bars with +delta in seconds
        for i, delta in enumerate(deltas):
            ax1.text(delta + 0.01, i, f'+{delta:.3f}s', va='center', fontsize=9)
        
        # Plot 2: Total laps per team
        team_laps = valid_laps.groupby('Team').size().sort_values(ascending=False)
        ax2.bar(range(len(team_laps)), team_laps.values, color=colors)
        ax2.set_xticks(range(len(team_laps)))
        ax2.set_xticklabels(team_laps.index, rotation=45, ha='right')
        ax2.set_ylabel('Total Laps Completed', fontsize=12)
        ax2.set_title('Total Laps by Team', fontsize=12, fontweight='bold')
        ax2.grid(True, axis='y', alpha=0.3)
        
        fig.suptitle(f'{self.session_name}\nTeam Performance Overview', 
                    fontsize=14, fontweight='bold', y=1.0)
        plt.tight_layout()
        output_file = self.output_dir / '07_team_comparison.png'
        plt.savefig(output_file, dpi=300, bbox_inches='tight')
        plt.close()
        print(f"  Saved: {output_file}")
    
    def plot_session_summary(self, laps):
        """Plot overall session summary"""
        print("[VIZ 8/8] Session summary...")
        
        valid_laps = laps[laps['LapTime'].notna()].copy()
        valid_laps['LapTimeSec'] = valid_laps['LapTime'].dt.total_seconds()
        
        fig = plt.figure(figsize=(16, 8))
        gs = fig.add_gridspec(1, 2, wspace=0.3)

        # 1. Total laps per driver (left)
        ax1 = fig.add_subplot(gs[0, 0])
        lap_counts = valid_laps['Driver'].value_counts().sort_values(ascending=False)
        ax1.bar(range(len(lap_counts)), lap_counts.values, color='skyblue')
        ax1.set_xticks(range(len(lap_counts)))
        ax1.set_xticklabels(lap_counts.index, rotation=45, ha='right')
        ax1.set_ylabel('Total Laps', fontsize=11)
        ax1.set_title('Total Laps per Driver', fontsize=12, fontweight='bold')
        ax1.grid(True, axis='y', alpha=0.3)

        # 2. Session statistics (right)
        ax2 = fig.add_subplot(gs[0, 1])
        ax2.axis('off')
        # Prepare session statistics: fastest lap (formatted), top 3 with deltas, frequency explanation, best sectors
        total_laps = len(valid_laps)
        unique_drivers = valid_laps['Driver'].nunique()
        unique_teams = valid_laps['Team'].nunique() if 'Team' in valid_laps.columns else 'N/A'

        # Fastest lap and driver
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

        # Top 3 fastest laps and deltas to fastest
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


        # Best sector times overall (find driver + time per sector)
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

        # Build stats text
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
        
        fig.suptitle(f'{self.session_name}\nSession Summary', fontsize=16, fontweight='bold', y=1.02)
        output_file = self.output_dir / '08_session_summary.png'
        plt.tight_layout()
        plt.savefig(output_file, dpi=300, bbox_inches='tight')
        plt.close()
        print(f"  OK Saved: {output_file}")
    
    def generate_all_visualizations(self):
        """Generate all visualizations"""
        laps = self.load_lap_data()
        
        self.plot_lap_times_progression(laps)
        self.plot_best_lap_comparison(laps)
        self.plot_best_two_overlay(laps)
        self.plot_sector_performance(laps)
        self.plot_speed_comparison(laps)
        self.plot_consistency(laps)
        self.plot_stint_analysis(laps)
        self.plot_team_comparison(laps)
        self.plot_session_summary(laps)
        
        print(f"\n{'='*70}")
        print("ALL VISUALIZATIONS GENERATED")
        print(f"{'='*70}")
        print(f"Output directory: {self.output_dir.absolute()}")
        print(f"Total files: 8 PNG images")
        print(f"{'='*70}\n")


def verify_snowflake_tables():
    """Verify Snowflake tables exist and show how to query them"""
    try:
        from snowflake_config import SNOWFLAKE_CONFIG
        import snowflake.connector
        
        print("\n" + "="*70)
        print("SNOWFLAKE TABLE VERIFICATION")
        print("="*70)
        
        # Load metadata to get schema name
        with open('telemetry_out/session_meta.json', 'r') as f:
            meta = json.load(f)
        
        # Sanitize schema name
        import re
        test_num = 1  # Adjust based on your test
        day_num = 1   # Adjust based on your day
        raw_name = f"Pre-Season_Testing_-_Bahrain_(Test_{test_num})_Testing_Day_{day_num}_{meta['year']}"
        schema_name = re.sub(r'[^A-Za-z0-9_]', '_', raw_name)
        schema_name = re.sub(r'_+', '_', schema_name).strip('_')
        
        print(f"\nConnecting to Snowflake...")
        print(f"  Account: {SNOWFLAKE_CONFIG['account']}.snowflakecomputing.com")
        print(f"  Database: {SNOWFLAKE_CONFIG['database']}")
        print(f"  Schema: {schema_name}")
        
        conn = snowflake.connector.connect(
            account=SNOWFLAKE_CONFIG['account'],
            user=SNOWFLAKE_CONFIG['user'],
            password=SNOWFLAKE_CONFIG['password'],
            database=SNOWFLAKE_CONFIG['database'],
            warehouse=SNOWFLAKE_CONFIG['warehouse'],
            schema=schema_name,
            role=SNOWFLAKE_CONFIG['role']
        )
        
        cursor = conn.cursor()
        cursor.execute("SHOW TABLES")
        tables = cursor.fetchall()
        
        print(f"\nOK Found {len(tables)} tables in schema '{schema_name}':\n")
        for table in tables:
            table_name = table[1]  # Table name is in column 1
            cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
            row_count = cursor.fetchone()[0]
            print(f"  • {table_name:<40} ({row_count:,} rows)")
        
        print("\n" + "="*70)
        print("EXAMPLE QUERIES:")
        print("="*70)
        print(f"""
# Query best laps
SELECT * FROM {schema_name}.VIZ_BEST_LAPS ORDER BY LAPTIME LIMIT 10;

# Query lap progression
SELECT * FROM {schema_name}.VIZ_LAP_PROGRESSION WHERE DRIVER = 'VER';

# Query sector performance
SELECT * FROM {schema_name}.VIZ_SECTOR_PERFORMANCE ORDER BY TOTAL_TIME LIMIT 10;

# Query speed comparison
SELECT * FROM {schema_name}.VIZ_SPEED_TRACE ORDER BY SPEED DESC LIMIT 10;
        """)
        
        conn.close()
        print("="*70 + "\n")
        
    except Exception as e:
        print(f"\nWARNING: Error verifying Snowflake tables: {e}\n")


def main():
    parser = argparse.ArgumentParser(description='Generate F1 testing session visualizations')
    parser.add_argument('--test', type=int, required=True, choices=[1, 2],
                       help='Test number (1 or 2)')
    parser.add_argument('--day', type=int, required=True, choices=[1, 2, 3],
                       help='Day number (1, 2, or 3)')
    parser.add_argument('--source', choices=['local', 'snowflake'], default='local',
                       help='Data source: local CSV files or Snowflake tables')
    parser.add_argument('--verify-snowflake', action='store_true',
                       help='Verify Snowflake tables and show example queries')
    
    args = parser.parse_args()
    
    if args.verify_snowflake:
        verify_snowflake_tables()
        return
    
    viz = TestingVisualizer(args.test, args.day, args.source)
    viz.generate_all_visualizations()


if __name__ == '__main__':
    main()
