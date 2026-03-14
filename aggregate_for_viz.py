import pandas as pd
import numpy as np
import os
import json
import re
from pathlib import Path
from tqdm import tqdm
from pipeline_logger import get_logger, DataValidator

# Import Snowflake configuration
try:
    from snowflake_config import SNOWFLAKE_CONFIG
except ImportError:
    raise ImportError("[WARNING] snowflake_config.py not found. Please create it with your Snowflake credentials.")

def sanitize_schema_name(name):
    """
    Sanitize schema name for Snowflake compatibility
    
    Snowflake identifiers must:
    - Start with a letter or underscore
    - Contain only letters, digits, and underscores
    """
    # Replace invalid characters with underscores
    sanitized = re.sub(r'[^A-Za-z0-9_]', '_', name)
    # Remove consecutive underscores
    sanitized = re.sub(r'_+', '_', sanitized)
    # Ensure it starts with a letter or underscore
    if sanitized and sanitized[0].isdigit():
        sanitized = '_' + sanitized
    # Remove trailing underscores
    sanitized = sanitized.strip('_')
    return sanitized

def get_snowflake_connection(schema=None):

    def find_all_session_dirs(base_dir='telemetry_out'):
        """Recursively find all session directories in telemetry_out/{year}/{event}/{session_type}/"""
        session_dirs = []
        for year_dir in Path(base_dir).iterdir():
            if not year_dir.is_dir():
                continue
            for event_dir in year_dir.iterdir():
                if not event_dir.is_dir():
                    continue
                for session_dir in event_dir.iterdir():
                    if session_dir.is_dir():
                        session_dirs.append(session_dir)
        return session_dirs

    def load_all_lap_csvs(base_dir='telemetry_out'):
        """Load all *_laps.csv files from all session directories"""
        lap_dfs = []
        # Use fixed telemetry structure for lap files
        for year_dir in Path(base_dir).iterdir():
            if not year_dir.is_dir():
                continue
            for session_dir in year_dir.iterdir():
                if not session_dir.is_dir():
                    continue
                for type_dir in session_dir.iterdir():
                    if not type_dir.is_dir():
                        continue
                    for lap_file in type_dir.glob('*_laps.csv'):
                        try:
                            df = pd.read_csv(lap_file)
                            lap_dfs.append(df)
                        except Exception:
                            continue
        # Fallback: recursive search if none found
        if not lap_dfs:
            for lap_file in Path(base_dir).rglob('*_laps.csv'):
                try:
                    df = pd.read_csv(lap_file)
                    lap_dfs.append(df)
                except Exception:
                    continue
        return lap_dfs
    """Create Snowflake connection with optional schema override"""
    try:
        import snowflake.connector
        
        conn = snowflake.connector.connect(
            account=SNOWFLAKE_CONFIG['account'],
            user=SNOWFLAKE_CONFIG['user'],
            password=SNOWFLAKE_CONFIG['password'],
            database=SNOWFLAKE_CONFIG['database'],
            warehouse=SNOWFLAKE_CONFIG['warehouse'],
            schema=schema if schema else SNOWFLAKE_CONFIG['schema'],
            role=SNOWFLAKE_CONFIG['role']
        )
        return conn
    except Exception as e:
        print(f"[ERROR] Snowflake connection failed: {e}")
        return None

def create_schema_if_not_exists(conn, schema_name):
    """Create schema if it doesn't exist"""
    try:
        cursor = conn.cursor()
        # Use quoted, upper-case identifier to avoid SQL errors with special chars
        safe_name = str(schema_name).upper()
        try:
            cursor.execute(f'CREATE SCHEMA IF NOT EXISTS "{safe_name}"')
            cursor.close()
            print(f"[OK] Schema ready: {schema_name}")
            return True
        except Exception as e:
            # Log and attempt a fallback using a strictly sanitized identifier
            print(f"[WARN] Quoted schema creation failed: {e}")
            # Remove any non-alphanumeric/underscore characters and uppercase
            import re
            fallback = re.sub(r'[^A-Z0-9_]', '_', safe_name)
            try:
                cursor.execute(f'CREATE SCHEMA IF NOT EXISTS {fallback}')
                cursor.close()
                print(f"[OK] Schema ready (fallback): {fallback}")
                return True
            except Exception as e2:
                print(f"[ERROR] Fallback schema creation failed: {e2}")
                cursor.close()
                return False
    except Exception as e:
        print(f"[ERROR] Schema creation failed: {e}")
        return False

def write_to_snowflake(df, table_name, conn):
    """Write DataFrame to Snowflake table"""
    from snowflake.connector.pandas_tools import write_pandas
    
    logger = get_logger()
    
    # Create a copy and convert column names to uppercase (Snowflake convention)
    df_copy = df.copy()
    
    # Always reset index to avoid non-standard index warnings
    if not isinstance(df_copy.index, pd.RangeIndex):
        df_copy = df_copy.reset_index(drop=True)
    
    df_copy.columns = [str(col).upper() for col in df_copy.columns]
    
    # Cast boolean columns to int — Arrow/Snowflake write_pandas rejects bool dtype
    bool_cols = df_copy.select_dtypes(include='bool').columns.tolist()
    for col in bool_cols:
        df_copy[col] = df_copy[col].fillna(False).astype(int)
    # Also catch object columns whose values are Python bools or float 0.0/1.0 (or a mix)
    for col in df_copy.select_dtypes(include='object').columns:
        sample = df_copy[col].dropna()
        if sample.empty:
            continue
        if sample.apply(lambda x: isinstance(x, bool) or (isinstance(x, (int, float)) and x in (0, 1, 0.0, 1.0))).all():
            df_copy[col] = pd.to_numeric(
                df_copy[col].map(lambda x: int(x) if isinstance(x, bool) else x),
                errors='coerce'
            ).fillna(0).astype(int)
    
    try:
        success, nchunks, nrows, _ = write_pandas(
            conn=conn,
            df=df_copy,
            table_name=table_name.upper(),
            auto_create_table=True,
            overwrite=True
        )
        
        if success:
            logger.increment_tables(nrows)
            logger.debug(f"[OK] {table_name}: {nrows:,} rows written")
        else:
            logger.warning(f"[WARNING] {table_name}: Write returned success=False")
        
        return success
    except Exception as e:
        logger.error(f"[ERROR] Failed to write {table_name}: {e}")
        raise

def aggregate_for_visualizations(input_dir='telemetry_out'):
    """
    Creates pre-aggregated datasets optimized for visualization in BI tools.
    Stores all data directly in Snowflake.
    """
    logger = get_logger()
    validator = DataValidator(logger)

    # Recursively find all session_meta.json files in telemetry_out
    meta_files = list(Path(input_dir).rglob('session_meta.json'))
    if not meta_files:
        raise FileNotFoundError("No session metadata found in telemetry_out")
    for meta_file in meta_files:
        with open(meta_file, 'r', encoding='utf-8') as f:
            session_meta = json.load(f)
        event_name = session_meta['event_name'].replace(' ', '_')
        session_name = session_meta['session_name'].replace(' ', '_')
        year = session_meta['year']

        # Log session info
        logger.set_session_info(session_meta['event_name'], session_meta['session_name'], year)

        # Create dynamic schema name: Event_Session_Year (sanitized for Snowflake)
        schema_name_raw = f"{event_name}_{session_name}_{year}"
        schema_name = sanitize_schema_name(schema_name_raw)

        logger.info(f"\n{'='*70}")
        logger.info(f"Creating visualization-ready aggregations")
        logger.info(f"Session: {session_meta['event_name']} - {session_meta['session_name']} ({year})")
        logger.info(f"Target Schema: {schema_name}")
        if schema_name != schema_name_raw:
            logger.info(f"  (Sanitized from: {schema_name_raw})")

    # Derive session directory from the most recently processed session meta file
    # telemetry_out/{year}/{event}/{session_type}/session_meta.json -> parent = session dir
    session_dir = meta_files[-1].parent

    logger.info(f"{'='*70}\n")

    # Connect to Snowflake with dynamic schema
    logger.info(f"Connecting to Snowflake: {SNOWFLAKE_CONFIG['account']}.snowflakecomputing.com")
    snow_conn = get_snowflake_connection(schema=schema_name)
    if not snow_conn:
        raise ConnectionError("Failed to connect to Snowflake. Check your credentials in snowflake_config.py")

    logger.info(f"[OK] Connected to Snowflake database: {SNOWFLAKE_CONFIG['database']}")

    # Create schema if it doesn't exist
    if not create_schema_if_not_exists(snow_conn, schema_name):
        raise Exception(f"Failed to create schema: {schema_name}")

    # Get all drivers from the specific session directory
    csv_files = list(session_dir.glob('*_laps.csv'))
    drivers = sorted(set([f.stem.split('_')[-2] for f in csv_files]))
    
    logger.info(f"Processing {len(drivers)} drivers: {', '.join(drivers)}\n")
    
    # ============================================
    # AGG 1: LAP TIME PROGRESSION
    # ============================================
    print("[AGGREGATING] Aggregating lap time progression...")
    
    all_laps = []
    for driver in drivers:
        lap_file = str(session_dir / f"{event_name}_{session_name}_{driver}_laps.csv")
        if os.path.exists(lap_file):
            laps_df = pd.read_csv(lap_file)
            laps_df['Driver'] = driver
            all_laps.append(laps_df)
    
    if all_laps:
        combined_laps = pd.concat(all_laps, ignore_index=True)
        
        # Convert lap time to seconds for plotting
        if 'LapTime' in combined_laps.columns:
            combined_laps['LapTimeSeconds'] = pd.to_timedelta(combined_laps['LapTime']).dt.total_seconds()
        
        lap_progression = combined_laps[[
            'Driver', 'LapNumber', 'LapTime', 'LapTimeSeconds', 
            'Compound', 'TyreLife', 'Stint', 'IsPersonalBest',
            'Sector1Time', 'Sector2Time', 'Sector3Time',
            'SpeedI1', 'SpeedI2', 'SpeedST'
        ]].copy()
        
        # Add session metadata
        lap_progression['EventName'] = session_meta['event_name']
        lap_progression['SessionName'] = session_meta['session_name']
        lap_progression['Year'] = session_meta['year']
        
        write_to_snowflake(lap_progression, 'viz_lap_progression', snow_conn)
        print(f"  [OK] viz_lap_progression ({len(lap_progression):,} rows)")
        print(f"    Use for: Lap time evolution, stint analysis, tire degradation")
    
    # ============================================
    # AGG 2: BEST LAP COMPARISON
    # ============================================
    print("\n[AGGREGATING] Aggregating best lap comparison...")
    
    if all_laps and len(combined_laps) > 0:
        # Get best lap indices per driver, filter out NaN (drivers with no valid laps)
        indices = combined_laps.groupby('Driver')['LapTimeSeconds'].idxmin()
        indices = indices.dropna()  # Remove drivers with all NaN lap times
        
        if len(indices) == 0:
            print("  [SKIP] No valid lap times for best lap comparison")
        else:
            best_laps = combined_laps.loc[indices]
        
            best_laps_comparison = best_laps[[
            'Driver', 'LapNumber', 'LapTime', 'LapTimeSeconds',
            'Sector1Time', 'Sector2Time', 'Sector3Time',
            'Compound', 'TyreLife',
            'SpeedI1', 'SpeedI2', 'SpeedST'
        ]].copy()
        
        best_laps_comparison['EventName'] = session_meta['event_name']
        best_laps_comparison['SessionName'] = session_meta['session_name']
        
        # Calculate gaps to fastest
        fastest_time = best_laps_comparison['LapTimeSeconds'].min()
        best_laps_comparison['GapToFastest'] = best_laps_comparison['LapTimeSeconds'] - fastest_time
        
        best_laps_comparison = best_laps_comparison.sort_values('LapTimeSeconds').reset_index(drop=True)
        
        write_to_snowflake(best_laps_comparison, 'viz_best_laps', snow_conn)
        print(f"  [OK] viz_best_laps ({len(best_laps_comparison)} rows)")
        print(f"    Use for: Driver rankings, gap analysis, speed trap comparison")
    
    # ============================================
    # AGG 3: SECTOR PERFORMANCE HEATMAP
    # ============================================
    print("\n[AGGREGATING] Aggregating sector performance...")
    
    if all_laps:
        # Convert sector times to seconds
        sector_data = combined_laps[['Driver', 'Sector1Time', 'Sector2Time', 'Sector3Time']].copy()
        
        for col in ['Sector1Time', 'Sector2Time', 'Sector3Time']:
            if col in sector_data.columns:
                sector_data[f'{col}_Seconds'] = pd.to_timedelta(sector_data[col]).dt.total_seconds()
        
        sector_agg = sector_data.groupby('Driver').agg({
            'Sector1Time_Seconds': ['mean', 'min'],
            'Sector2Time_Seconds': ['mean', 'min'],
            'Sector3Time_Seconds': ['mean', 'min']
        }).reset_index()
        
        sector_agg.columns = [
            'Driver',
            'Sector1_AvgSeconds', 'Sector1_BestSeconds',
            'Sector2_AvgSeconds', 'Sector2_BestSeconds',
            'Sector3_AvgSeconds', 'Sector3_BestSeconds'
        ]
        
        # Calculate relative performance (% from best)
        for i in [1, 2, 3]:
            best = sector_agg[f'Sector{i}_BestSeconds'].min()
            sector_agg[f'Sector{i}_GapPercent'] = ((sector_agg[f'Sector{i}_BestSeconds'] - best) / best) * 100
        
        sector_agg['EventName'] = session_meta['event_name']
        
        write_to_snowflake(sector_agg, 'viz_sector_performance', snow_conn)
        print(f"  [OK] viz_sector_performance ({len(sector_agg)} rows)")
        print(f"    Use for: Sector comparison heatmap, driver strengths")
    
    # ============================================
    # AGG 4: TIRE COMPOUND ANALYSIS
    # ============================================
    print("\n[AGGREGATING] Aggregating tire compound performance...")
    
    if all_laps:
        valid_laps = combined_laps[combined_laps['LapTimeSeconds'].notna()].copy()
        
        tire_analysis = valid_laps.groupby(['Driver', 'Compound', 'TyreLife']).agg({
            'LapTimeSeconds': ['mean', 'min', 'count']
        }).reset_index()
        
        tire_analysis.columns = ['Driver', 'Compound', 'TyreLife', 'AvgLapTime', 'BestLapTime', 'LapCount']
        
        tire_analysis['EventName'] = session_meta['event_name']
        
        write_to_snowflake(tire_analysis, 'viz_tire_degradation', snow_conn)
        print(f"  [OK] viz_tire_degradation ({len(tire_analysis):,} rows)")
        print(f"    Use for: Tire degradation curves, compound comparison")
    
    # ============================================
    # AGG 5: CORNER PERFORMANCE COMPARISON
    # ============================================
    print("\n[AGGREGATING] Aggregating corner performance...")
    
    all_corners = []
    for driver in drivers:
        corners_file = str(session_dir / f"{event_name}_{session_name}_{driver}_corners.csv")
        if os.path.exists(corners_file):
            corners_df = pd.read_csv(corners_file)
            corners_df['Driver'] = driver
            all_corners.append(corners_df)
    
    if all_corners:
        combined_corners = pd.concat(all_corners, ignore_index=True)
        
        # Aggregate by driver and corner
        corner_agg = combined_corners.groupby(['Driver', 'Corner']).agg({
            'EntrySpeed': 'mean',
            'ApexSpeed': 'mean',
            'ExitSpeed': 'mean',
            'EntryThrottle': 'mean',
            'ApexThrottle': 'mean',
            'ExitThrottle': 'mean',
            'EntryGear': 'mean',
            'ApexGear': 'mean',
            'ExitGear': 'mean',
            'CornerAngle': 'first',
            'LapNumber': 'count'
        }).reset_index()
        
        corner_agg.columns = [
            'Driver', 'Corner',
            'AvgEntrySpeed', 'AvgApexSpeed', 'AvgExitSpeed',
            'AvgEntryThrottle', 'AvgApexThrottle', 'AvgExitThrottle',
            'AvgEntryGear', 'AvgApexGear', 'AvgExitGear',
            'CornerAngle', 'TotalLaps'
        ]
        
        # Calculate relative performance per corner
        for speed_col in ['AvgEntrySpeed', 'AvgApexSpeed', 'AvgExitSpeed']:
            corner_agg[f'{speed_col}_Rank'] = corner_agg.groupby('Corner')[speed_col].rank(ascending=False, method='dense')
            max_speed = corner_agg.groupby('Corner')[speed_col].transform('max')
            corner_agg[f'{speed_col}_Percent'] = (corner_agg[speed_col] / max_speed) * 100
        
        corner_agg['EventName'] = session_meta['event_name']
        
        write_to_snowflake(corner_agg, 'viz_corner_comparison', snow_conn)
        print(f"  [OK] viz_corner_comparison ({len(corner_agg)} rows)")
        print(f"    Use for: Corner-by-corner heatmap, driver vs driver corner analysis")

        # === PRINT OPTIMAL CORNER SPEED TABLE AS PNG ===
        # For each corner, find the max AvgApexSpeed (optimal speed)
        import matplotlib.pyplot as plt
        from pandas.plotting import table
        optimal_corners = corner_agg.loc[corner_agg.groupby('Corner')['AvgApexSpeed'].idxmax()][['Corner', 'AvgApexSpeed', 'Driver']]
        optimal_corners = optimal_corners.sort_values('Corner')
        optimal_corners.rename(columns={'AvgApexSpeed': 'OptimalApexSpeed', 'Driver': 'OptimalDriver'}, inplace=True)
        # Format speeds to 1 decimal
        optimal_corners['OptimalApexSpeed'] = optimal_corners['OptimalApexSpeed'].round(1)
        # Print as PNG table
        fig, ax = plt.subplots(figsize=(8, max(4, len(optimal_corners) * 0.4)))
        ax.axis('off')
        tbl = table(ax, optimal_corners, loc='center', colWidths=[0.2, 0.2, 0.2])
        tbl.auto_set_font_size(False)
        tbl.set_fontsize(10)
        tbl.scale(1, 1.5)
        plt.title(f'Optimal Corner Apex Speeds ({event_name} {session_name})', fontsize=12)
        png_path = f'visualizations/{year}/{event_name}/{session_name}/optimal_corner_speeds.png'
        os.makedirs(os.path.dirname(png_path), exist_ok=True)
        plt.savefig(png_path, bbox_inches='tight')
        plt.close(fig)
        print(f"[OK] Saved: {png_path}")
        
        # ============================================
        # AGG 6: CORNER SPEED HEATMAP (DRIVER x CORNER)
        # ============================================
        
        # Pivot for easy heatmap plotting (using original column names)
        corner_pivot = corner_agg.pivot(index='Driver', columns='Corner', values='AvgApexSpeed')
        corner_pivot = corner_pivot.reset_index()
        corner_pivot['EventName'] = session_meta['event_name']
        
        # Fill NaN values that may occur in pivot
        corner_pivot = corner_pivot.fillna(0)
        
        write_to_snowflake(corner_pivot, 'viz_corner_heatmap', snow_conn)
        print(f"  [OK] viz_corner_heatmap ({len(corner_pivot)} rows)")
        print(f"    Use for: Direct heatmap import (Driver x Corner)")
    
    # ============================================
    # AGG 7: SPEED ANALYSIS BY DISTANCE
    # ============================================
    print("\n[AGGREGATING] Aggregating speed traces...")
    
    speed_traces = []
    for driver in tqdm(drivers, desc="Processing telemetry"):
        telemetry_file = str(session_dir / f"{event_name}_{session_name}_{driver}_telemetry.csv")
        if os.path.exists(telemetry_file):
            telemetry_df = pd.read_csv(telemetry_file)
            
            if 'Distance' in telemetry_df.columns and 'Speed' in telemetry_df.columns:
                # Sample every 50 meters for visualization
                telemetry_df = telemetry_df.sort_values('Distance')
                telemetry_df['DistanceBucket'] = (telemetry_df['Distance'] // 50) * 50
                
                speed_by_dist = telemetry_df.groupby('DistanceBucket').agg({
                    'Speed': 'mean',
                    'Throttle': 'mean',
                    'Brake': 'mean',
                    'nGear': 'mean',
                    'RPM': 'mean'
                }).reset_index()
                
                speed_by_dist['Driver'] = driver
                speed_traces.append(speed_by_dist)
    
    if speed_traces:
        combined_speed = pd.concat(speed_traces, ignore_index=True)
        combined_speed['EventName'] = session_meta['event_name']
        
        write_to_snowflake(combined_speed, 'viz_speed_trace', snow_conn)
        print(f"  [OK] viz_speed_trace ({len(combined_speed):,} rows)")
        print(f"    Use for: Speed trace overlay, throttle/brake maps")
    
    # ============================================
    # AGG 8: DRIVER SUMMARY STATISTICS
    # ============================================
    print("\n[AGGREGATING] Creating driver summary...")
    
    if all_laps:
        driver_summary = combined_laps.groupby('Driver').agg({
            'LapTimeSeconds': ['count', 'min', 'mean', 'std'],
            'SpeedST': 'max',
            'SpeedI1': 'mean',
            'SpeedI2': 'mean',
            'Compound': lambda x: ', '.join(x.dropna().unique()),
            'IsPersonalBest': lambda x: int(x.fillna(False).astype(bool).sum())
        }).reset_index()
        
        driver_summary.columns = [
            'Driver', 'TotalLaps', 'BestLapTime', 'AvgLapTime', 'LapTimeStdDev',
            'MaxSpeedTrap', 'AvgSpeedSector1', 'AvgSpeedSector2',
            'CompoundsUsed', 'PersonalBests'
        ]
        
        # Calculate consistency score (inverse of std dev)
        driver_summary['ConsistencyScore'] = 100 - (driver_summary['LapTimeStdDev'] * 10)
        driver_summary['ConsistencyScore'] = driver_summary['ConsistencyScore'].clip(lower=0)
        
        driver_summary['EventName'] = session_meta['event_name']
        driver_summary['SessionName'] = session_meta['session_name']
        
        driver_summary = driver_summary.sort_values('BestLapTime').reset_index(drop=True)
        
        write_to_snowflake(driver_summary, 'viz_driver_summary', snow_conn)
        print(f"  [OK] viz_driver_summary ({len(driver_summary)} rows)")
        print(f"    Use for: Driver leaderboard, summary statistics")
    
    # ============================================
    # AGG 9: TOP 2 DRIVERS COMPARISON
    # ============================================
    print("\n[AGGREGATING] Creating top 2 drivers comparison...")
    
    if all_laps:
        # Get top 2 fastest drivers
        driver_best_times = combined_laps.groupby('Driver')['LapTimeSeconds'].min().sort_values()
        top_2_drivers = driver_best_times.head(2).index.tolist()
        
        # Filter all laps for top 2 drivers
        top_2_laps = combined_laps[combined_laps['Driver'].isin(top_2_drivers)].copy()
        
        top_2_laps['EventName'] = session_meta['event_name']
        top_2_laps['SessionName'] = session_meta['session_name']
        
        # Add rankings
        top_2_laps['Position'] = top_2_laps['Driver'].map({
            top_2_drivers[0]: 'P1',
            top_2_drivers[1]: 'P2'
        })
        
        top_2_laps = top_2_laps.reset_index(drop=True)
        
        write_to_snowflake(top_2_laps, 'viz_top2_comparison', snow_conn)
        print(f"  [OK] viz_top2_comparison ({len(top_2_laps):,} rows)")
        print(f"    Comparing: {top_2_drivers[0]} vs {top_2_drivers[1]}")
        print(f"    Use for: Head-to-head lap progression, direct comparison")
        
        # Top 2 sector comparison
        if all_corners:
            top_2_corners = combined_corners[combined_corners['Driver'].isin(top_2_drivers)].copy()
            
            top_2_corner_agg = top_2_corners.groupby(['Driver', 'Corner']).agg({
                'EntrySpeed': 'mean',
                'ApexSpeed': 'mean',
                'ExitSpeed': 'mean',
                'CornerAngle': 'first'
            }).reset_index()
            
            # Pivot for side-by-side comparison
            top_2_pivot = top_2_corner_agg.pivot(index='Corner', columns='Driver', values='ApexSpeed')
            top_2_pivot = top_2_pivot.reset_index()
            top_2_pivot['SpeedDifference'] = top_2_pivot[top_2_drivers[0]] - top_2_pivot[top_2_drivers[1]]
            top_2_pivot['EventName'] = session_meta['event_name']
            
            write_to_snowflake(top_2_pivot, 'viz_top2_corners', snow_conn)
            print(f"  [OK] viz_top2_corners ({len(top_2_pivot)} rows)")
            print(f"    Use for: Corner-by-corner speed difference visualization")
    
    # ============================================
    # AGG 10: TEAMMATE COMPARISON
    # ============================================
    print("\n[AGGREGATING] Creating teammate comparisons...")
    
    # 2025 F1 Team mappings (update as needed)
    team_mappings = {
        'VER': 'Red Bull Racing', 'PER': 'Red Bull Racing',
        'HAM': 'Ferrari', 'LEC': 'Ferrari',
        'NOR': 'McLaren', 'PIA': 'McLaren',
        'RUS': 'Mercedes', 'ANT': 'Mercedes',
        'ALO': 'Aston Martin', 'STR': 'Aston Martin',
        'GAS': 'Alpine', 'OCO': 'Alpine',
        'HUL': 'Haas', 'BEA': 'Haas',
        'ALB': 'Williams', 'SAI': 'Williams',
        'TSU': 'RB', 'LAW': 'RB',
        'BOT': 'Sauber', 'ZHO': 'Sauber',
        'MAG': 'Haas', 'SAR': 'Williams',  # Reserve/replacement drivers
        'COL': 'Alpine', 'BOR': 'Sauber', 'HAD': 'RB'
    }
    
    if all_laps:
        combined_laps['Team'] = combined_laps['Driver'].map(team_mappings)
        
        # Only keep drivers with team assignments
        team_laps = combined_laps[combined_laps['Team'].notna()].copy()
        
        # Get best lap per driver, filter out NaN indices
        indices = team_laps.groupby('Driver')['LapTimeSeconds'].idxmin()
        indices = indices.dropna()
        
        if len(indices) == 0:
            print("  [SKIP] No valid lap times for teammate comparison")
            teammate_comparison = pd.DataFrame()
        else:
            teammate_comparison = team_laps.loc[indices]
            
            teammate_comparison = teammate_comparison[[
                'Team', 'Driver', 'LapNumber', 'LapTime', 'LapTimeSeconds',
                'Sector1Time', 'Sector2Time', 'Sector3Time',
                'Compound', 'TyreLife', 'SpeedST'
            ]].sort_values(['Team', 'LapTimeSeconds'])
            
            # Calculate intra-team gap
            teammate_comparison['GapToTeammate'] = teammate_comparison.groupby('Team')['LapTimeSeconds'].diff()
            
            teammate_comparison['EventName'] = session_meta['event_name']
            teammate_comparison['SessionName'] = session_meta['session_name']
            
            teammate_comparison = teammate_comparison.reset_index(drop=True)
        
        write_to_snowflake(teammate_comparison, 'viz_teammate_comparison', snow_conn)
        print(f"  [OK] viz_teammate_comparison ({len(teammate_comparison)} rows)")
        print(f"    Use for: Intra-team battle, gap to teammate")
        
        # Detailed lap-by-lap teammate comparison
        teammate_laps_all = team_laps[[
            'Team', 'Driver', 'LapNumber', 'LapTimeSeconds', 
            'Compound', 'TyreLife', 'Stint'
        ]].copy()
        
        teammate_laps_all['EventName'] = session_meta['event_name']
        
        write_to_snowflake(teammate_laps_all, 'viz_teammate_progression', snow_conn)
        print(f"  [OK] viz_teammate_progression ({len(teammate_laps_all):,} rows)")
        print(f"    Use for: Lap-by-lap teammate progression by team")
        
        # Corner comparison by teammates
        if all_corners:
            combined_corners['Team'] = combined_corners['Driver'].map(team_mappings)
            team_corners = combined_corners[combined_corners['Team'].notna()].copy()
            
            teammate_corner_agg = team_corners.groupby(['Team', 'Driver', 'Corner']).agg({
                'ApexSpeed': 'mean',
                'EntrySpeed': 'mean',
                'ExitSpeed': 'mean'
            }).reset_index()
            
            teammate_corner_agg['EventName'] = session_meta['event_name']
            
            write_to_snowflake(teammate_corner_agg, 'viz_teammate_corners', snow_conn)
            print(f"  [OK] viz_teammate_corners ({len(teammate_corner_agg)} rows)")
            print(f"    Use for: Corner-by-corner teammate comparison per team")
    
    # ============================================
    # AGG 14: THROTTLE/BRAKE HEATMAPS
    # ============================================
    print("\n[AGGREGATING] Creating throttle/brake heatmaps...")
    
    throttle_brake_data = []
    for driver in drivers:
        telemetry_file = str(session_dir / f"{event_name}_{session_name}_{driver}_telemetry.csv")
        if os.path.exists(telemetry_file):
            telemetry_df = pd.read_csv(telemetry_file)
            
            if 'Distance' in telemetry_df.columns and 'Throttle' in telemetry_df.columns:
                telemetry_df = telemetry_df.sort_values('Distance')
                telemetry_df['DistanceBucket'] = (telemetry_df['Distance'] // 100) * 100
                
                pedal_agg = telemetry_df.groupby('DistanceBucket').agg({
                    'Throttle': 'mean',
                    'Brake': 'mean'
                }).reset_index()
                
                pedal_agg['Driver'] = driver
                throttle_brake_data.append(pedal_agg)
    
    if throttle_brake_data:
        combined_pedals = pd.concat(throttle_brake_data, ignore_index=True)
        combined_pedals['EventName'] = session_meta['event_name']
        
        write_to_snowflake(combined_pedals, 'viz_throttle_brake_map', snow_conn)
        print(f"  [OK] viz_throttle_brake_map ({len(combined_pedals):,} rows)")
        print(f"    Use for: Pedal application heatmap, driving style analysis")
    
    # ============================================
    # AGG 15: GEAR USAGE ANALYSIS
    # ============================================
    print("\n[AGGREGATING] Analyzing gear usage...")
    
    if all_corners:
        gear_by_corner = combined_corners.groupby(['Driver', 'Corner']).agg({
            'ApexGear': 'mean',
            'EntryGear': 'mean',
            'ExitGear': 'mean'
        }).reset_index()
        
        gear_by_corner['EventName'] = session_meta['event_name']
        
        write_to_snowflake(gear_by_corner, 'viz_gear_by_corner', snow_conn)
        print(f"  [OK] viz_gear_by_corner ({len(gear_by_corner)} rows)")
        print(f"    Use for: Gear selection comparison per corner")
    
    # Gear distribution
    gear_distribution = []
    for driver in drivers:
        telemetry_file = str(session_dir / f"{event_name}_{session_name}_{driver}_telemetry.csv")
        if os.path.exists(telemetry_file):
            telemetry_df = pd.read_csv(telemetry_file)
            
            if 'nGear' in telemetry_df.columns:
                gear_counts = telemetry_df['nGear'].value_counts().reset_index()
                gear_counts.columns = ['Gear', 'Count']
                gear_counts['Driver'] = driver
                gear_counts['Percentage'] = (gear_counts['Count'] / len(telemetry_df)) * 100
                gear_distribution.append(gear_counts)
    
    if gear_distribution:
        combined_gears = pd.concat(gear_distribution, ignore_index=True)
        combined_gears['EventName'] = session_meta['event_name']
        combined_gears = combined_gears.reset_index(drop=True)
        
        write_to_snowflake(combined_gears, 'viz_gear_distribution', snow_conn)
        print(f"  [OK] viz_gear_distribution ({len(combined_gears)} rows)")
        print(f"    Use for: Time spent in each gear")
    
    # ============================================
    # AGG 16: BRAKING ANALYSIS
    # ============================================
    print("\n[AGGREGATING] Analyzing braking points...")
    
    if all_corners:
        braking_analysis = combined_corners.groupby(['Driver', 'Corner']).agg({
            'EntrySpeed': ['mean', 'max'],
            'ApexSpeed': 'mean',
            'EntryBrake': 'mean',
            'ApexBrake': 'mean'
        }).reset_index()
        
        braking_analysis.columns = [
            'Driver', 'Corner', 
            'AvgEntrySpeed', 'MaxEntrySpeed', 
            'AvgApexSpeed', 'AvgEntryBrake', 'AvgApexBrake'
        ]
        
        braking_analysis['SpeedLoss'] = braking_analysis['MaxEntrySpeed'] - braking_analysis['AvgApexSpeed']
        braking_analysis['EventName'] = session_meta['event_name']
        
        write_to_snowflake(braking_analysis, 'viz_braking_analysis', snow_conn)
        print(f"  [OK] viz_braking_analysis ({len(braking_analysis)} rows)")
        print(f"    Use for: Braking efficiency, late braking comparison")
    
    # ============================================
    # AGG 17: ACCELERATION ZONES
    # ============================================
    print("\n[AGGREGATING] Identifying acceleration zones...")
    
    accel_zones = []
    for driver in drivers:
        telemetry_file = str(session_dir / f"{event_name}_{session_name}_{driver}_telemetry.csv")
        if os.path.exists(telemetry_file):
            telemetry_df = pd.read_csv(telemetry_file)
            
            if 'Speed' in telemetry_df.columns and len(telemetry_df) > 100:
                if 'Distance' in telemetry_df.columns:
                    telemetry_df = telemetry_df.sort_values('Distance')
                telemetry_df['SpeedDelta'] = telemetry_df['Speed'].diff()
                
                # Find acceleration zones (speed increasing with throttle)
                telemetry_df['IsAccelerating'] = (telemetry_df['SpeedDelta'] > 0) & (telemetry_df['Throttle'] > 50)
                
                if 'Distance' in telemetry_df.columns and telemetry_df['IsAccelerating'].sum() > 0:
                    accel_data = telemetry_df[telemetry_df['IsAccelerating']].groupby(
                        (telemetry_df[telemetry_df['IsAccelerating']]['Distance'] // 200) * 200
                    ).agg({
                        'SpeedDelta': 'sum',
                        'Throttle': 'mean',
                        'Speed': 'mean'
                    }).reset_index()
                    
                    accel_data['Driver'] = driver
                    accel_zones.append(accel_data)
    
    if accel_zones:
        combined_accel = pd.concat(accel_zones, ignore_index=True)
        combined_accel.columns = ['DistanceZone', 'AccelRate', 'AvgThrottle', 'AvgSpeed', 'Driver']
        combined_accel['EventName'] = session_meta['event_name']
        
        write_to_snowflake(combined_accel, 'viz_acceleration_zones', snow_conn)
        print(f"  [OK] viz_acceleration_zones ({len(combined_accel):,} rows)")
        print(f"    Use for: Traction analysis, acceleration comparison")
    
    # ============================================
    # AGG 18: CONSISTENCY METRICS
    # ============================================
    print("\n[AGGREGATING] Calculating consistency metrics...")
    
    if all_laps:
        # Lap time consistency
        consistency = combined_laps.groupby('Driver').agg({
            'LapTimeSeconds': ['std', 'mean', 'min', 'max']
        }).reset_index()
        
        consistency.columns = ['Driver', 'LapTimeStdDev', 'AvgLapTime', 'BestLapTime', 'WorstLapTime']
        consistency['ConsistencyScore'] = 100 - (consistency['LapTimeStdDev'] * 10).clip(upper=100)
        consistency['LapTimeRange'] = consistency['WorstLapTime'] - consistency['BestLapTime']
        consistency['EventName'] = session_meta['event_name']
        
        write_to_snowflake(consistency, 'viz_consistency_metrics', snow_conn)
        print(f"  [OK] viz_consistency_metrics ({len(consistency)} rows)")
        print(f"    Use for: Driver consistency rankings")
    
    # Corner consistency
    if all_corners:
        corner_consistency = combined_corners.groupby(['Driver', 'Corner']).agg({
            'ApexSpeed': 'std'
        }).reset_index()
        
        corner_consistency.columns = ['Driver', 'Corner', 'SpeedVariance']
        corner_consistency['EventName'] = session_meta['event_name']
        
        write_to_snowflake(corner_consistency, 'viz_corner_consistency', snow_conn)
        print(f"  [OK] viz_corner_consistency ({len(corner_consistency)} rows)")
        print(f"    Use for: Which corners are most inconsistent")
    
    # ============================================
    # AGG 19: FUEL EFFECT SIMULATION
    # ============================================
    print("\n[AGGREGATING] Estimating fuel effect...")
    
    if all_laps:
        # Assume 0.05 seconds per lap fuel effect
        fuel_corrected = combined_laps[combined_laps['LapTimeSeconds'].notna()].copy()
        
        # Estimate fuel load based on lap number
        fuel_corrected['EstimatedFuelCorrection'] = (fuel_corrected['LapNumber'] - 1) * 0.05
        fuel_corrected['FuelCorrectedLapTime'] = fuel_corrected['LapTimeSeconds'] - fuel_corrected['EstimatedFuelCorrection']
        fuel_corrected['EventName'] = session_meta['event_name']
        
        fuel_analysis = fuel_corrected[['Driver', 'LapNumber', 'LapTimeSeconds', 
                                        'EstimatedFuelCorrection', 'FuelCorrectedLapTime', 'EventName']].reset_index(drop=True)
        
        write_to_snowflake(fuel_analysis, 'viz_fuel_corrected_pace', snow_conn)
        print(f"  [OK] viz_fuel_corrected_pace ({len(fuel_analysis):,} rows)")
        print(f"    Use for: True pace without fuel weight")
    
    # ============================================
    # AGG 20: IDEAL LAP CONSTRUCTION
    # ============================================
    print("\n[AGGREGATING] Building ideal/theoretical laps...")
    
    if all_laps:
        # Convert sector times to seconds
        for col in ['Sector1Time', 'Sector2Time', 'Sector3Time']:
            if col in combined_laps.columns:
                combined_laps[f'{col}_Seconds'] = pd.to_timedelta(combined_laps[col]).dt.total_seconds()
        
        # Get best sectors per driver
        ideal_laps = combined_laps.groupby('Driver').agg({
            'Sector1Time_Seconds': 'min',
            'Sector2Time_Seconds': 'min',
            'Sector3Time_Seconds': 'min'
        }).reset_index()
        
        ideal_laps['TheoreticalBestLap'] = (
            ideal_laps['Sector1Time_Seconds'] + 
            ideal_laps['Sector2Time_Seconds'] + 
            ideal_laps['Sector3Time_Seconds']
        )
        
        # Get actual best lap
        actual_best = combined_laps.groupby('Driver')['LapTimeSeconds'].min().reset_index()
        actual_best.columns = ['Driver', 'ActualBestLap']
        
        ideal_laps = ideal_laps.merge(actual_best, on='Driver')
        ideal_laps['GapToIdeal'] = ideal_laps['ActualBestLap'] - ideal_laps['TheoreticalBestLap']
        ideal_laps['EventName'] = session_meta['event_name']
        
        write_to_snowflake(ideal_laps, 'viz_ideal_lap', snow_conn)
        print(f"  [OK] viz_ideal_lap ({len(ideal_laps)} rows)")
        print(f"    Use for: Theoretical best vs actual best")
    
    # ============================================
    # AGG 21: TIRE DEGRADATION CURVES
    # ============================================
    print("\n[AGGREGATING] Analyzing tire degradation...")
    
    if all_laps:
        tire_deg = combined_laps[combined_laps['LapTimeSeconds'].notna()].copy()
        
        tire_analysis = tire_deg.groupby(['Driver', 'Compound', 'TyreLife']).agg({
            'LapTimeSeconds': ['mean', 'min', 'count']
        }).reset_index()
        
        tire_analysis.columns = ['Driver', 'Compound', 'TyreLife', 'AvgLapTime', 'BestLapTime', 'LapCount']
        
        # Calculate degradation rate per compound per driver
        tire_analysis = tire_analysis.sort_values(['Driver', 'Compound', 'TyreLife'])
        tire_analysis['DegradationRate'] = tire_analysis.groupby(['Driver', 'Compound'])['AvgLapTime'].diff()
        tire_analysis['EventName'] = session_meta['event_name']
        
        write_to_snowflake(tire_analysis, 'viz_tire_deg_curves', snow_conn)
        print(f"  [OK] viz_tire_deg_curves ({len(tire_analysis):,} rows)")
        print(f"    Use for: Degradation rate, cliff detection")
    
    # ============================================
    # AGG 22: PIT STOP ANALYSIS
    # ============================================
    print("\n[AGGREGATING] Analyzing pit stops...")
    
    if all_laps:
        pit_laps = combined_laps[combined_laps['PitInTime'].notna() | combined_laps['PitOutTime'].notna()].copy()
        
        if len(pit_laps) > 0:
            pit_analysis = pit_laps[['Driver', 'LapNumber', 'LapTimeSeconds', 'Stint', 'Compound', 'TyreLife']].copy()
            pit_analysis['PitStop'] = True
            pit_analysis['EventName'] = session_meta['event_name']
            pit_analysis = pit_analysis.reset_index(drop=True)
            
            write_to_snowflake(pit_analysis, 'viz_pit_stops', snow_conn)
            print(f"  [OK] viz_pit_stops ({len(pit_analysis)} rows)")
            print(f"    Use for: Pit stop timing, strategy analysis")

        # === PITSTOP FIRST LAP ANALYSIS (Race only) ===
        if session_name.lower() == 'race':
            import matplotlib.pyplot as plt
            import matplotlib.patches as mpatches
            from matplotlib.ticker import FuncFormatter
            import numpy as np
            flagged_laps = []
            for driver in drivers:
                driver_laps = [laps for laps in all_laps if laps['Driver'].iloc[0] == driver]
                if not driver_laps:
                    continue
                df = pd.concat(driver_laps, ignore_index=True).sort_values('LapNumber')
                pit_out_mask = pd.to_timedelta(df['PitOutTime'], errors='coerce').notna()
                for idx, pit_row in df[pit_out_mask].iterrows():
                    next_lap_num = pit_row['LapNumber'] + 1
                    next_lap = df[df['LapNumber'] == next_lap_num]
                    if not next_lap.empty:
                        flagged_laps.append({
                            'Driver': driver,
                            'LapNumber': next_lap_num,
                            'LapTime': next_lap.iloc[0]['LapTime'],
                            'Compound': str(next_lap.iloc[0].get('Compound', '')).upper(),
                        })
            if flagged_laps:
                pitlap_df = pd.DataFrame(flagged_laps)
                pitlap_df['LapTimeSec'] = pd.to_timedelta(pitlap_df['LapTime'], errors='coerce').dt.total_seconds()
                pitlap_df['LapTimeStr'] = pd.to_timedelta(pitlap_df['LapTime'], errors='coerce').apply(
                    lambda x: f"{int(x.total_seconds() // 60)}:{x.total_seconds() % 60:06.3f}" if pd.notnull(x) else ""
                )
                pitlap_df = pitlap_df[pitlap_df['LapTimeSec'].notna()]

                # ── Compound palette (matches stint_analysis_dev) ──────────────
                PIT_COMPOUNDS = {
                    "SOFT":         "#E8002D",
                    "MEDIUM":       "#FFF200",
                    "HARD":         "#EBEBEB",
                    "INTERMEDIATE": "#39B54A",
                    "WET":          "#0067FF",
                }
                BG     = "#0E0E0E"
                PANEL  = "#181818"
                GRID   = "#2A2A2A"
                TICK_C = "#AAAAAA"
                TEXT_C = "#EAEAEA"
                BAR_H      = 0.65
                BAR_GAP    = 0.18   # gap between laps of same driver
                DRIVER_GAP = 0.90   # extra gap between drivers

                # Sort drivers by minimum exit-lap time (fastest at top)
                driver_order = (
                    pitlap_df.groupby('Driver')['LapTimeSec'].min()
                    .sort_values().index.tolist()
                )

                # ── Build y positions ─────────────────────────────────────────
                # Each lap gets its own sub-row, drivers are separated by DRIVER_GAP
                row_y       = {}   # (driver, df_index) -> y centre
                driver_ytick = {}  # driver -> y centre for label
                separator_ys = []  # y positions for horizontal dividers
                current_y = 0.0
                for driver in driver_order:
                    d_laps = pitlap_df[pitlap_df['Driver'] == driver].sort_values('LapNumber')
                    n = len(d_laps)
                    ys = [current_y + i * (BAR_H + BAR_GAP) for i in range(n)]
                    driver_ytick[driver] = (ys[0] + ys[-1]) / 2
                    for i, (dfidx, _) in enumerate(d_laps.iterrows()):
                        row_y[(driver, dfidx)] = ys[i]
                    current_y += n * (BAR_H + BAR_GAP) + DRIVER_GAP
                    separator_ys.append(current_y - DRIVER_GAP / 2)

                total_y = current_y
                fig_h = max(8, total_y * 0.55 + 2)
                fig_w = 20

                fig, ax = plt.subplots(figsize=(fig_w, fig_h))
                fig.patch.set_facecolor(BG)
                ax.set_facecolor(PANEL)
                for spine in ax.spines.values():
                    spine.set_edgecolor("#333333")

                # ── Plot bars ─────────────────────────────────────────────────
                x_max = pitlap_df['LapTimeSec'].max()
                for driver in driver_order:
                    d_laps = pitlap_df[pitlap_df['Driver'] == driver].sort_values('LapNumber')
                    for dfidx, row in d_laps.iterrows():
                        y    = row_y[(driver, dfidx)]
                        lt   = row['LapTimeSec']
                        comp = row['Compound']
                        color = PIT_COMPOUNDS.get(comp, '#888888')

                        ax.barh(y, lt, height=BAR_H, left=0, color=color,
                                edgecolor='#00000066', linewidth=0.4, alpha=0.9, zorder=3)

                        # Lap number inside bar at left edge
                        ax.text(1.5, y, f"L{int(row['LapNumber'])}",
                                va='center', ha='left', fontsize=7.5,
                                color='#000000CC', fontweight='bold', zorder=5)

                        # Compound pill above bar centre
                        pill_color = PIT_COMPOUNDS.get(comp, '#888888')
                        ax.text(lt / 2, y + BAR_H / 2 + 0.04,
                                comp.capitalize(),
                                va='bottom', ha='center', fontsize=7,
                                color=pill_color, fontweight='bold', zorder=5,
                                bbox=dict(boxstyle='round,pad=0.15',
                                          fc='#0E0E0ECC', ec=pill_color,
                                          linewidth=0.5, alpha=0.85))

                        # Lap time annotation at bar end
                        lap_str = row['LapTimeStr']
                        if lap_str:
                            ax.text(lt + x_max * 0.005, y, lap_str,
                                    va='center', ha='left', fontsize=8,
                                    color=TEXT_C, zorder=5)

                # ── Separator lines between drivers ───────────────────────────
                for sy in separator_ys[:-1]:
                    ax.axhline(sy, color='#333333', linewidth=0.8,
                               linestyle='--', zorder=2)

                # ── Axes styling ──────────────────────────────────────────────
                def fmt_pitstop_time(val, _pos=None):
                    try:
                        v = float(val)
                        if np.isnan(v): return ""
                        m, s = divmod(v, 60)
                        return f"{int(m)}:{s:06.3f}"
                    except Exception:
                        return ""

                ax.xaxis.set_major_formatter(FuncFormatter(fmt_pitstop_time))
                ax.tick_params(axis='x', colors=TICK_C, labelsize=8)
                ax.tick_params(axis='y', colors=TICK_C, length=0)
                ax.set_xlabel('Lap Time', color=TICK_C, fontsize=9, labelpad=8)
                ax.grid(True, axis='x', color=GRID, linewidth=0.7, zorder=0)
                ax.set_axisbelow(True)
                ax.set_xlim(0, x_max * 1.12)
                ax.set_ylim(-DRIVER_GAP / 2, total_y)

                # Y-axis: driver labels centred on each band
                ax.set_yticks(list(driver_ytick.values()))
                ax.set_yticklabels(list(driver_ytick.keys()),
                                   color=TEXT_C, fontsize=10, fontweight='bold')
                ax.invert_yaxis()

                # ── Title ─────────────────────────────────────────────────────
                ax.set_title(
                    f'{event_name.replace("_", " ")}  —  Exit Laps After Pitstop (Race)',
                    color=TEXT_C, fontsize=13, fontweight='bold', pad=10,
                )

                # ── Legend ────────────────────────────────────────────────────
                legend_items = [
                    mpatches.Patch(fc=PIT_COMPOUNDS['SOFT'],         ec='#444', label='Soft'),
                    mpatches.Patch(fc=PIT_COMPOUNDS['MEDIUM'],       ec='#444', label='Medium'),
                    mpatches.Patch(fc=PIT_COMPOUNDS['HARD'],         ec='#444', label='Hard'),
                    mpatches.Patch(fc=PIT_COMPOUNDS['INTERMEDIATE'], ec='#444', label='Intermediate'),
                    mpatches.Patch(fc=PIT_COMPOUNDS['WET'],          ec='#444', label='Wet'),
                ]
                fig.legend(handles=legend_items, loc='lower right',
                           frameon=True, facecolor='#1A1A1A',
                           edgecolor='#444444', labelcolor=TEXT_C, fontsize=9,
                           title='Compound', title_fontsize=9,
                           bbox_to_anchor=(0.99, 0.01))

                plt.tight_layout()
                pit_png = f'visualizations/{year}/{event_name}/Race/first_lap_after_pitstop.png'
                os.makedirs(os.path.dirname(pit_png), exist_ok=True)
                plt.savefig(pit_png, dpi=150, bbox_inches='tight',
                            facecolor=fig.get_facecolor())
                plt.close(fig)
                print(f"[OK] Saved: {pit_png}")

    # ============================================
    # AGG 23: MINI-SECTOR ANALYSIS
    # ============================================
    print("\n[AGGREGATING] Creating mini-sector analysis...")
    
    mini_sectors = []
    for driver in drivers:
        telemetry_file = str(session_dir / f"{event_name}_{session_name}_{driver}_telemetry.csv")
        if os.path.exists(telemetry_file):
            telemetry_df = pd.read_csv(telemetry_file)
            
            if 'Distance' in telemetry_df.columns and 'SessionTime' in telemetry_df.columns:
                telemetry_df = telemetry_df.sort_values('Distance')
                
                # Create 20 mini-sectors
                max_distance = telemetry_df['Distance'].max()
                sector_size = max_distance / 20
                telemetry_df['MiniSector'] = (telemetry_df['Distance'] // sector_size).astype(int)
                
                mini_sector_agg = telemetry_df.groupby('MiniSector').agg({
                    'Speed': 'mean',
                    'Throttle': 'mean',
                    'Distance': 'mean'
                }).reset_index()
                
                mini_sector_agg['Driver'] = driver
                mini_sectors.append(mini_sector_agg)
    
    if mini_sectors:
        combined_mini = pd.concat(mini_sectors, ignore_index=True)
        combined_mini['EventName'] = session_meta['event_name']
        
        write_to_snowflake(combined_mini, 'viz_mini_sectors', snow_conn)
        print(f"  [OK] viz_mini_sectors ({len(combined_mini):,} rows)")
        print(f"    Use for: Detailed micro-sector time gain/loss")
    
    # ============================================
    # AGG 24: RACING LINE COMPARISON
    # ============================================
    print("\n[AGGREGATING] Analyzing racing lines...")
    
    racing_lines = []
    for driver in drivers:
        telemetry_file = str(session_dir / f"{event_name}_{session_name}_{driver}_telemetry.csv")
        if os.path.exists(telemetry_file):
            telemetry_df = pd.read_csv(telemetry_file)
            
            if 'X' in telemetry_df.columns and 'Y' in telemetry_df.columns:
                # Sample every 10 meters for racing line
                telemetry_df = telemetry_df.sort_values('Distance')
                telemetry_df['DistanceBucket'] = (telemetry_df['Distance'] // 10) * 10
                
                line_data = telemetry_df.groupby('DistanceBucket').agg({
                    'X': 'mean',
                    'Y': 'mean',
                    'Speed': 'mean'
                }).reset_index()
                
                line_data['Driver'] = driver
                racing_lines.append(line_data)
    
    if racing_lines:
        combined_lines = pd.concat(racing_lines, ignore_index=True)
        combined_lines['EventName'] = session_meta['event_name']
        
        write_to_snowflake(combined_lines, 'viz_racing_lines', snow_conn)
        print(f"  [OK] viz_racing_lines ({len(combined_lines):,} rows)")
        print(f"    Use for: Track position overlay, racing line deviation")
    
    # ============================================
    # AGG 25: RPM ANALYSIS
    # ============================================
    print("\n[AGGREGATING] Analyzing RPM patterns...")
    
    rpm_analysis = []
    for driver in drivers:
        telemetry_file = str(session_dir / f"{event_name}_{session_name}_{driver}_telemetry.csv")
        if os.path.exists(telemetry_file):
            telemetry_df = pd.read_csv(telemetry_file)
            
            if 'RPM' in telemetry_df.columns and 'nGear' in telemetry_df.columns:
                rpm_by_gear = telemetry_df.groupby('nGear').agg({
                    'RPM': ['mean', 'max', 'min']
                }).reset_index()
                
                rpm_by_gear.columns = ['Gear', 'AvgRPM', 'MaxRPM', 'MinRPM']
                rpm_by_gear['Driver'] = driver
                rpm_analysis.append(rpm_by_gear)
    
    if rpm_analysis:
        combined_rpm = pd.concat(rpm_analysis, ignore_index=True)
        combined_rpm['EventName'] = session_meta['event_name']
        
        write_to_snowflake(combined_rpm, 'viz_rpm_analysis', snow_conn)
        print(f"  [OK] viz_rpm_analysis ({len(combined_rpm)} rows)")
        print(f"    Use for: Shift point comparison, engine usage")
    
    # ============================================
    # STORE SESSION METADATA
    # ============================================
    
    print("\n[METADATA] Storing session metadata...")
    
    # Store session metadata
    session_meta_df = pd.DataFrame([session_meta])
    write_to_snowflake(session_meta_df, 'session_metadata', snow_conn)
    print("  [OK] Session metadata stored")
    
    # ============================================
    # AGG 26: WEATHER CONDITIONS
    # ============================================
    print("\n[WEATHER] Storing weather conditions...")
    
    try:
        # Check if weather data exists
        weather_file = str(session_dir / 'weather_data.json')
        if os.path.exists(weather_file):
            with open(weather_file, 'r') as f:
                weather_data = json.load(f)
            
            # Create weather dataframe
            weather_df = pd.DataFrame([{
                'EventName': session_meta['event_name'],
                'SessionName': session_meta['session_name'],
                'Year': session_meta['year'],
                'Temperature': weather_data.get('temperature'),
                'FeelsLike': weather_data.get('feels_like'),
                'Pressure': weather_data.get('pressure'),
                'Humidity': weather_data.get('humidity'),
                'WindSpeed': weather_data.get('wind_speed'),
                'WindDirection': weather_data.get('wind_deg'),
                'Clouds': weather_data.get('clouds'),
                'Rain1h': weather_data.get('rain_1h'),
                'WeatherMain': weather_data.get('weather_main'),
                'WeatherDescription': weather_data.get('weather_description'),
            }])
            
            write_to_snowflake(weather_df, 'viz_weather_conditions', snow_conn)
            print(f"  [OK] viz_weather_conditions ({len(weather_df)} rows)")
            print(f"    Temperature: {weather_data.get('temperature')}°C, Humidity: {weather_data.get('humidity')}%")
        else:
            print("  [WARNING] Weather data not available (configure OPENWEATHER_API_KEY in .env)")
    except Exception as e:
        print(f"  [WARNING] Weather aggregation failed: {e}")
    
    # Close connection
    snow_conn.close()
    print("  [OK] Snowflake connection closed")
    
    # ============================================
    # SUMMARY
    # ============================================
    
    print(f"\n{'='*70}")
    print(f"[SUCCESS] Data loaded to Snowflake: {SNOWFLAKE_CONFIG['database']}.{schema_name}")
    print(f"   Account: {SNOWFLAKE_CONFIG['account']}.snowflakecomputing.com")
    print(f"   Total tables: 26 visualization-ready datasets")
    print(f"{'='*70}")
    
    print("\n[AGGREGATING] Core Performance Tables:")
    print("  • viz_lap_progression - Lap-by-lap progression")
    print("  • viz_best_laps - Best lap leaderboard")
    print("  • viz_sector_performance - Sector comparison")
    print("  • viz_driver_summary - Summary statistics")
    
    print("\n[TECHNICAL] Technical Analysis:")
    print("  • viz_throttle_brake_map - Pedal application heatmap")
    print("  • viz_gear_by_corner - Gear selection per corner")
    print("  • viz_gear_distribution - Time spent in each gear")
    print("  • viz_braking_analysis - Braking efficiency")
    print("  • viz_rpm_analysis - Shift point comparison")
    
    print("\n[SPEED] Speed & Acceleration:")
    print("  • viz_speed_trace - Speed trace overlay")
    print("  • viz_acceleration_zones - Traction analysis")
    print("  • viz_mini_sectors - Detailed micro-sectors")
    print("  • viz_racing_lines - Track position overlay")
    
    print("\n[CORNERS] Corner Analysis:")
    print("  • viz_corner_comparison - Corner-by-corner performance")
    print("  • viz_corner_heatmap - Speed matrix (Driver x Corner)")
    print("  • viz_corner_consistency - Corner variance")
    
    print("\n[TIRES] Tire Strategy:")
    print("  • viz_tire_degradation - Compound comparison")
    print("  • viz_tire_deg_curves - Degradation rate curves")
    print("  • viz_pit_stops - Pit stop timing")
    
    print("\n[HEAD-TO-HEAD] Head-to-Head:")
    print("  • viz_top2_comparison - P1 vs P2 battle")
    print("  • viz_top2_corners - P1 vs P2 corner analysis")
    print("  • viz_teammate_comparison - Intra-team best laps")
    print("  • viz_teammate_progression - Teammate lap-by-lap")
    print("  • viz_teammate_corners - Teammate corner comparison")
    
    print("\n[METRICS] Advanced Metrics:")
    print("  • viz_consistency_metrics - Driver consistency scores")
    print("  • viz_fuel_corrected_pace - True pace without fuel")
    print("  • viz_ideal_lap - Theoretical vs actual best")
    
    print("\n[WEATHER] Weather & Context:")
    print("  • viz_weather_conditions - Weather conditions during session")
    print("  • session_metadata - Session information")
    
    print(f"\n[INFO] Connect Power BI/Tableau to Snowflake:")
    print(f"   Account: {SNOWFLAKE_CONFIG['account']}.snowflakecomputing.com")
    print(f"   Database: {SNOWFLAKE_CONFIG['database']}")
    print(f"   Schema: {schema_name}")
    print(f"   Warehouse: {SNOWFLAKE_CONFIG['warehouse']}")
    print(f"{'='*70}\n")


if __name__ == '__main__':
    aggregate_for_visualizations('telemetry_out')


