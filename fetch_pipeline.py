import fastf1
import fastf1.core
import pandas as pd
import numpy as np
import os
import json
import datetime
from tqdm import tqdm
from pipeline_logger import get_logger, DataValidator
from weather_integration import add_weather_to_session
from openf1_fetcher import build_openf1_telemetry_dicts

cache_dir = 'f1_cache'
os.makedirs(cache_dir, exist_ok=True)
fastf1.Cache.enable_cache(cache_dir)

def _no_op_add_driver_ahead(self, *args, **kwargs):
    return self

fastf1.core.Telemetry.add_driver_ahead = _no_op_add_driver_ahead

def find_most_recent_session(years_back=1):
    now = datetime.datetime.now()
    current_year = now.year
    # Include both regular session types and testing session types
    session_types = ['R', 'Q', 'FP3', 'FP2', 'FP1', 'Practice 3', 'Practice 2', 'Practice 1']
    
    print(f"Searching for the most recent session (current date: {now.strftime('%Y-%m-%d')})...")
    
    for year in range(current_year, current_year - years_back - 1, -1):
        print(f"  Checking {year} events...")
        try:
            schedule = fastf1.get_event_schedule(year)
        except Exception as e:
            print(f"  Could not load {year} schedule: {e}")
            continue
        
        # First pass: Check for testing events within next 14 days (they may have data available)
        # NOTE: Testing events are currently skipped because FastF1 blocks RoundNumber=0 access
        # and F1 typically doesn't publish testing telemetry through the API
        # for _, event in schedule.iterrows():
        #     event_date = pd.to_datetime(event['EventDate'])
        #     event_format = event.get('EventFormat', 'conventional')
        #     days_until_event = (event_date - now).days
        #     
        #     # Check testing events that are upcoming (within next 14 days) or recently completed
        #     if event_format == 'testing' and -7 <= days_until_event <= 14:
        #         ... testing code disabled ...
        
        # Second pass: Check completed events in reverse chronological order
        for _, event in schedule[::-1].iterrows():
            event_date = pd.to_datetime(event['EventDate'])
            event_format = event.get('EventFormat', 'conventional')
            round_number = event.get('RoundNumber', 0)
            
            # Skip future events
            if event_date > now:
                continue
            
            # Skip testing events (RoundNumber = 0, and FastF1 blocks them)
            if event_format == 'testing' or round_number == 0:
                print(f"  Skipping testing event: {event['EventName']} (no telemetry data available)")
                continue
            
            print(f"  Checking {event['EventName']} ({event_format})...")
            
            # Check regular sessions
            check_sessions = ['R', 'Q', 'FP3', 'FP2', 'FP1']
            
            for session_type in check_sessions:
                try:
                    session = fastf1.get_session(year, round_number, session_type)
                    session.load()  # <-- Ensure this is called before accessing laps
                    
                    if not session.laps.empty:
                        print(f"  [OK] Found: {event['EventName']} {year} - {session_type}\n")
                        return session
                        
                except Exception as e:
                    # Silently continue - session might not exist
                    continue
    
    raise RuntimeError("No recent session found")

def get_circuit_info_safe(session):
    try:
        import fastf1
        import warnings
        
        temp_session = fastf1.get_session(
            session.event.year,
            session.event['RoundNumber'],
            session.name
        )
        
        # Load with laps=True to avoid "marker distance" warning
        temp_session.load(telemetry=False, weather=False, messages=False, laps=True)
        
        with warnings.catch_warnings():
            warnings.filterwarnings('ignore')
            circuit_info = temp_session.get_circuit_info()
        
        if circuit_info and hasattr(circuit_info, 'corners') and circuit_info.corners is not None and not circuit_info.corners.empty:
            corners_df = circuit_info.corners.copy()
            
            if 'Distance' not in corners_df.columns or corners_df['Distance'].isna().all():
                corners_df = corners_df.sort_values('Number').reset_index(drop=True)
                corners_df['Distance'] = 0.0
                
                for i in range(1, len(corners_df)):
                    dx = corners_df.loc[i, 'X'] - corners_df.loc[i-1, 'X']
                    dy = corners_df.loc[i, 'Y'] - corners_df.loc[i-1, 'Y']
                    dist = np.sqrt(dx**2 + dy**2)
                    corners_df.loc[i, 'Distance'] = corners_df.loc[i-1, 'Distance'] + dist
            
            print(f"  [OK] Fetched {len(corners_df)} corners from API (works for any circuit)")
            
            circuit_info.corners = corners_df
            return circuit_info
    
    except Exception as e:
        print(f"  API circuit fetch failed: {e}")
    
    try:
        print("  Attempting corner detection from speed/brake telemetry...")
        
        laps = session.laps
        car_data_dict = session.car_data
        pos_data_dict = session.pos_data if hasattr(session, 'pos_data') else {}
        
        if not laps.empty and car_data_dict and pos_data_dict:
            clean_laps = laps[
                (~laps['PitInTime'].notna()) & 
                (~laps['PitOutTime'].notna()) &
                (laps['LapTime'].notna())
            ].copy()
            
            if not clean_laps.empty:
                lap = clean_laps.iloc[len(clean_laps)//4]  
                
                driver_num = str(lap['DriverNumber'])
                
                if driver_num in car_data_dict and driver_num in pos_data_dict:
                    sample_car = car_data_dict[driver_num].copy()
                    sample_pos = pos_data_dict[driver_num].copy()
                    
                    lap_start = lap['LapStartTime']
                    lap_end = lap['Time']
                    
                    lap_car = sample_car[
                        (sample_car['SessionTime'] >= lap_start) &
                        (sample_car['SessionTime'] <= lap_end)
                    ].copy()
                    
                    lap_pos = sample_pos[
                        (sample_pos['SessionTime'] >= lap_start) &
                        (sample_pos['SessionTime'] <= lap_end)
                    ].copy()
                    
                    lap_data = pd.merge_asof(
                        lap_car.sort_values('SessionTime'),
                        lap_pos[['SessionTime', 'X', 'Y']].sort_values('SessionTime'),
                        on='SessionTime',
                        direction='nearest',
                        tolerance=pd.Timedelta(seconds=0.5)
                    )
                    
                    lap_data = lap_data.dropna(subset=['X', 'Y', 'Speed']).copy()
                    
                    if len(lap_data) > 100:
                        # Calculate distance for this lap
                        lap_data['Distance'] = np.sqrt(
                            lap_data['X'].diff()**2 + 
                            lap_data['Y'].diff()**2
                        ).fillna(0).cumsum()
                        
                        lap_data['Distance'] = lap_data['Distance'] - lap_data['Distance'].min()
                        
                        lap_data['SpeedRolling'] = lap_data['Speed'].rolling(window=30, center=True, min_periods=1).mean()
                        lap_data['BrakeRolling'] = lap_data['Brake'].rolling(window=30, center=True, min_periods=1).mean()
                        
                        lap_data['SpeedMin'] = lap_data['Speed'].rolling(window=50, center=True, min_periods=1).min()
                        lap_data['IsLocalMin'] = (lap_data['Speed'] == lap_data['SpeedMin'])
                        
                        speed_threshold = lap_data['Speed'].quantile(0.70)  # Below 70th percentile
                        brake_threshold = lap_data['Brake'].quantile(0.70) if lap_data['Brake'].max() > 0 else 0
                        
                        lap_data['IsCorner'] = (
                            (lap_data['IsLocalMin'] & (lap_data['Speed'] < speed_threshold)) |
                            (lap_data['BrakeRolling'] > brake_threshold)
                        )
                        
                        corner_candidates = lap_data[lap_data['IsCorner']].copy()
                        
                        if len(corner_candidates) >= 8:
                            from scipy.cluster.hierarchy import fclusterdata
                            
                            coords = corner_candidates[['X', 'Y']].values
                            track_length = lap_data['Distance'].max()
                            cluster_distance = max(track_length * 0.05, 150)  
                            
                            clusters = fclusterdata(coords, t=cluster_distance, criterion='distance')
                            
                            corners = []
                            for i in range(1, clusters.max() + 1):
                                cluster_points = corner_candidates[clusters == i]
                                
                                min_speed_idx = cluster_points['Speed'].idxmin()
                                corner_point = lap_data.loc[min_speed_idx]
                                
                                nearby_window = 20
                                idx_pos = lap_data.index.get_loc(min_speed_idx)
                                
                                if idx_pos > nearby_window and idx_pos < len(lap_data) - nearby_window:
                                    before = lap_data.iloc[idx_pos - nearby_window:idx_pos]
                                    after = lap_data.iloc[idx_pos:idx_pos + nearby_window]
                                    
                                    dx_in = before['X'].iloc[-1] - before['X'].iloc[0]
                                    dy_in = before['Y'].iloc[-1] - before['Y'].iloc[0]
                                    dx_out = after['X'].iloc[-1] - after['X'].iloc[0]
                                    dy_out = after['Y'].iloc[-1] - after['Y'].iloc[0]
                                    
                                    angle_in = np.arctan2(dy_in, dx_in) * 180 / np.pi
                                    angle_out = np.arctan2(dy_out, dx_out) * 180 / np.pi
                                    angle = angle_out - angle_in
                                    
                                    while angle > 180:
                                        angle -= 360
                                    while angle < -180:
                                        angle += 360
                                else:
                                    angle = 0
                                
                                corners.append({
                                    'Number': i,
                                    'Letter': '',
                                    'Angle': float(angle),
                                    'X': float(corner_point['X']),
                                    'Y': float(corner_point['Y']),
                                    'Distance': float(corner_point['Distance'])
                                })
                            
                            corners = sorted(corners, key=lambda x: x['Distance'])
                            
                            for i, corner in enumerate(corners, 1):
                                corner['Number'] = i
                            
                            corners_df = pd.DataFrame(corners)
                            
                            print(f"  [OK] Detected {len(corners)} corners from speed/brake analysis")
                            
                            class CircuitData:
                                def __init__(self, corners):
                                    self.corners = corners
                                    self.marshal_lights = pd.DataFrame()
                                    self.marshal_sectors = pd.DataFrame()
                                    self.rotation = 0
                            
                            return CircuitData(corners_df)
        
    except Exception as e:
        print(f"  Speed-based detection failed: {e}")
        import traceback
        traceback.print_exc()
    
    print("  [WARNING] Could not extract circuit data")
    return None


def get_corner_zones(corners_df, track_length=None):

    if corners_df is None or corners_df.empty:
        return None
    
    if track_length is None:
        track_length = corners_df['Distance'].max() * 1.05  
    
    corner_zones = {}
    
    for _, corner in corners_df.iterrows():
        corner_num = int(corner['Number'])
        corner_distance = corner['Distance']
        
        entry_distance = corner_distance - 50
        apex_distance = corner_distance
        exit_distance = corner_distance + 50
        
        if entry_distance < 0:
            entry_distance += track_length
        if exit_distance > track_length:
            exit_distance -= track_length
        
        corner_zones[corner_num] = {
            'entry': entry_distance,
            'apex': apex_distance,
            'exit': exit_distance,
            'x': corner['X'],
            'y': corner['Y'],
            'angle': corner['Angle']
        }
    
    return corner_zones


def find_nearest_telemetry(telemetry_df, target_distance, tolerance=100):
    if 'Distance' not in telemetry_df.columns or telemetry_df.empty:
        return None
    
    distances = (telemetry_df['Distance'] - target_distance).abs()
    min_idx = distances.idxmin()
    
    if distances[min_idx] > tolerance:
        return None
    
    return telemetry_df.loc[min_idx]


def analyze_corner_performance(telemetry_df, corner_zones):
    if corner_zones is None:
        return None
    
    corner_data = []
    
    for corner_num, zones in corner_zones.items():
        entry_data = find_nearest_telemetry(telemetry_df, zones['entry'])
        apex_data = find_nearest_telemetry(telemetry_df, zones['apex'])
        exit_data = find_nearest_telemetry(telemetry_df, zones['exit'])
        
        if entry_data is not None and apex_data is not None and exit_data is not None:
            corner_info = {
                'Corner': corner_num,
                'EntrySpeed': entry_data['Speed'],
                'ApexSpeed': apex_data['Speed'],
                'ExitSpeed': exit_data['Speed'],
                'EntryThrottle': entry_data['Throttle'],
                'ApexThrottle': apex_data['Throttle'],
                'ExitThrottle': exit_data['Throttle'],
                'EntryBrake': entry_data['Brake'],
                'ApexBrake': apex_data['Brake'],
                'ExitBrake': exit_data['Brake'],
                'EntryGear': entry_data['nGear'],
                'ApexGear': apex_data['nGear'],
                'ExitGear': exit_data['nGear'],
                'EntryRPM': entry_data['RPM'],
                'ApexRPM': apex_data['RPM'],
                'ExitRPM': exit_data['RPM'],
                'CornerAngle': zones['angle']
            }
            corner_data.append(corner_info)
    
    return pd.DataFrame(corner_data) if corner_data else None


def fetch_comprehensive_telemetry(session, output_dir='telemetry_out'):
    
    # Build output directory structure: telemetry_out/{year}/{event}/{session_type}/
    event_name_clean = session.event['EventName'].replace(' ', '_')
    session_name_clean = session.name.replace(' ', '_')
    year_str = str(session.event.year)
    output_dir = os.path.join('telemetry_out', year_str, event_name_clean, session_name_clean)
    os.makedirs(output_dir, exist_ok=True)

    event_info = {
        'event_name': session.event['EventName'],
        'session_name': session.name,
        'date': str(session.event['EventDate']),
        'year': int(session.event.year),
        'country': session.event['Country'],
        'location': session.event['Location'],
        'round': int(session.event['RoundNumber'])
    }

    with open(os.path.join(output_dir, 'session_meta.json'), 'w') as f:
        json.dump(event_info, f, indent=2)
    
    print(f"\n{'='*70}")
    print(f"Session: {event_info['event_name']} - {event_info['session_name']}")
    print(f"Date: {event_info['date']}")
    print(f"Round: {event_info['round']}")
    print(f"{'='*70}\n")
    
    print("Fetching circuit information...")
    circuit_info = get_circuit_info_safe(session)
    
    corner_zones = None
    if circuit_info and circuit_info.corners is not None and not circuit_info.corners.empty:
        corners_df = circuit_info.corners
        
        corners_file = os.path.join(output_dir, 'circuit_corners.csv')
        corners_df.to_csv(corners_file, index=False)
        
        if circuit_info.marshal_sectors is not None and not circuit_info.marshal_sectors.empty:
            circuit_info.marshal_sectors.to_csv(os.path.join(output_dir, 'marshal_sectors.csv'), index=False)
        
        if circuit_info.marshal_lights is not None and not circuit_info.marshal_lights.empty:
            circuit_info.marshal_lights.to_csv(os.path.join(output_dir, 'marshal_lights.csv'), index=False)
        
        print(f"[OK] Saved {len(corners_df)} corners")
        corner_zones = get_corner_zones(corners_df)
    else:
        print("[WARNING] Circuit information not available - corner analysis will be skipped")
    
    drivers = session.laps['Driver'].unique()
    print(f"\nProcessing {len(drivers)} drivers...\n")
    
    driver_numbers = {}
    for _, lap in session.laps.iterrows():
        driver_numbers[lap['Driver']] = str(lap['DriverNumber'])
    
    try:
        car_data_dict = session.car_data
    except fastf1.core.DataNotLoadedError:
        print("[WARNING] Car telemetry data is not available for this session (session.load() completed but telemetry was unavailable). Trying OpenF1 as fallback...")
        car_data_dict = {}

    try:
        pos_data_dict = session.pos_data
    except fastf1.core.DataNotLoadedError:
        pos_data_dict = {}

    # If FastF1 has no telemetry, try OpenF1 API as fallback
    if not car_data_dict:
        print("[INFO] Attempting OpenF1 telemetry fallback...")
        car_data_dict, pos_data_dict = build_openf1_telemetry_dicts(session, driver_numbers)
        if car_data_dict:
            print(f"[OK] OpenF1 fallback: fetched telemetry for {len(car_data_dict)} drivers")
        else:
            print("[WARNING] OpenF1 fallback also returned no data — skipping per-driver car data extraction.")
    
    for driver in tqdm(drivers, desc="Processing drivers"):
        try:
            driver_num = driver_numbers.get(driver)
            if not driver_num:
                continue
            
            driver_laps = session.laps.pick_driver(driver)
            
            if driver_laps.empty:
                continue
            
            event_name = event_info['event_name'].replace(' ', '_')
            session_name = event_info['session_name'].replace(' ', '_')
            lap_file = os.path.join(output_dir, f"{event_name}_{session_name}_{driver}_laps.csv")
            
            laps_to_save = driver_laps.copy()
            for col in laps_to_save.columns:
                if pd.api.types.is_timedelta64_dtype(laps_to_save[col]):
                    laps_to_save[col] = laps_to_save[col].astype(str)
            
            laps_to_save.to_csv(lap_file, index=False)

            if driver_num not in car_data_dict:
                continue

            driver_telemetry = car_data_dict[driver_num].copy()
            
            if driver_telemetry.empty:
                continue
            
            if driver_num in pos_data_dict:
                pos_df = pos_data_dict[driver_num].copy()
                if not pos_df.empty and 'SessionTime' in driver_telemetry.columns:
                    driver_telemetry = pd.merge_asof(
                        driver_telemetry.sort_values('SessionTime'),
                        pos_df[['SessionTime', 'X', 'Y', 'Z', 'Status']].sort_values('SessionTime'),
                        on='SessionTime',
                        direction='nearest',
                        tolerance=pd.Timedelta(seconds=0.5)
                    )
            
            driver_telemetry['Driver'] = driver
            driver_telemetry['DriverNumber'] = driver_num
            
            if 'X' in driver_telemetry.columns and 'Y' in driver_telemetry.columns:
                driver_telemetry['Distance'] = np.sqrt(
                    driver_telemetry['X'].diff()**2 + 
                    driver_telemetry['Y'].diff()**2
                ).fillna(0).cumsum()
            
            telemetry_to_save = driver_telemetry.copy()
            for col in telemetry_to_save.columns:
                if pd.api.types.is_timedelta64_dtype(telemetry_to_save[col]):
                    telemetry_to_save[col] = telemetry_to_save[col].astype(str)

            # ── Data quality guard ───────────────────────────────────────────
            # Skip writing the telemetry file if speed data is clearly corrupt.
            # OpenF1 occasionally returns speed=0 for an entire session (bad
            # upstream recording).  Saving zero-speed files causes flat intra
            # diagrams and poisons the coasting-analysis dataset.
            _speed_ok = True
            if 'Speed' in telemetry_to_save.columns:
                _max_speed = pd.to_numeric(telemetry_to_save['Speed'], errors='coerce').max()
                if pd.isna(_max_speed) or _max_speed < 10:
                    print(f"  [SKIP] {driver}: Speed data is invalid "
                          f"(max={_max_speed} km/h) — likely bad upstream data. "
                          f"Telemetry file NOT written.")
                    _speed_ok = False

            if not _speed_ok:
                continue
            # ────────────────────────────────────────────────────────────────

            telemetry_file = os.path.join(output_dir, f"{event_name}_{session_name}_{driver}_telemetry.csv")
            telemetry_to_save.to_csv(telemetry_file, index=False)
            
            if corner_zones is not None:
                all_lap_corners = []
                
                for _, lap_data in driver_laps.iterrows():
                    lap_num = lap_data['LapNumber']
                    lap_start_time = lap_data['LapStartTime']
                    lap_time = lap_data['Time']
                    
                    if pd.notna(lap_start_time) and pd.notna(lap_time):
                        lap_telemetry = driver_telemetry[
                            (driver_telemetry['SessionTime'] >= lap_start_time) &
                            (driver_telemetry['SessionTime'] <= lap_time)
                        ].copy()
                        
                        if not lap_telemetry.empty and 'Distance' in lap_telemetry.columns:
                            lap_telemetry['Distance'] = lap_telemetry['Distance'] - lap_telemetry['Distance'].min()
                            
                            corner_analysis = analyze_corner_performance(lap_telemetry, corner_zones)
                            
                            if corner_analysis is not None:
                                corner_analysis['LapNumber'] = lap_num
                                corner_analysis['LapTime'] = lap_data['LapTime']
                                corner_analysis['Compound'] = lap_data['Compound']
                                corner_analysis['TyreLife'] = lap_data['TyreLife']
                                corner_analysis['IsPersonalBest'] = lap_data['IsPersonalBest']
                                all_lap_corners.append(corner_analysis)
                
                if all_lap_corners:
                    corners_df_driver = pd.concat(all_lap_corners, ignore_index=True)
                    corners_file = os.path.join(output_dir, f"{event_name}_{session_name}_{driver}_corners.csv")
                    corners_df_driver.to_csv(corners_file, index=False)
            
        except Exception as e:
            print(f"  [ERROR] {driver}: Error - {e}")
            continue
    
    print(f"\n{'='*70}")
    print(f"[OK] Data saved to: {os.path.abspath(output_dir)}")
    print(f"\n[FILES] Files generated per driver:")
    print(f"  • *_laps.csv: Lap times, sectors, compounds, speed traps")
    print(f"  • *_telemetry.csv: Speed, RPM, Throttle, Brake, Gear, X/Y/Z, Distance")
    if corner_zones:
        print(f"  • *_corners.csv: Entry/Apex/Exit analysis for all corners")
    print(f"\n[DATA] Circuit data:")
    print(f"  • circuit_corners.csv: Corner locations and angles")
    if circuit_info and circuit_info.marshal_sectors is not None:
        print(f"  • marshal_sectors.csv & marshal_lights.csv")
    print(f"{'='*70}")


def main(year=None, event=None, session_type=None):
    logger = get_logger()
    
    try:
        logger.info("="*70)
        logger.info("F1 TELEMETRY PIPELINE STARTED")
        logger.info("="*70)
        
        if year and event and session_type:
            # Load specific session
            logger.info(f"Loading specific session: {event} {year} - {session_type}")
            session = fastf1.get_session(year, event, session_type)
            try:
                session.load()
            except Exception as e:
                print(f"[ERROR] Failed to load session data: {e}")
                return
            # Robust check: ensure laps are loaded
            try:
                laps_loaded = not session.laps.empty
            except fastf1.core.DataNotLoadedError:
                print(f"[WARNING] Lap data is not yet available for {event} {year} {session_type}. "
                      f"The session may not have been processed by F1's servers yet. "
                      f"Try again in a few hours.")
                return
            except Exception as e:
                print(f"[ERROR] Could not access session.laps: {e}")
                return
            if not laps_loaded:
                print(f"[WARNING] No laps found for {event} {year} {session_type}. Aborting pipeline.")
                return
            logger.info(f"[OK] Loaded: {session.event['EventName']} {year} - {session_type}\n")
        else:
            # Auto-detect most recent session
            session = find_most_recent_session(years_back=1)
        
        fetch_comprehensive_telemetry(session)

        logger.info("\n[SUCCESS] Data extraction complete!")

        # Add weather data
        logger.info("\n[WEATHER] Fetching weather data...")
        try:
            add_weather_to_session(session)
        except Exception as e:
            logger.warning(f"[WARNING] Weather data failed: {e}")

        # Automatically run visualizations for every session
        try:
            import subprocess
            event_name_clean = session.event['EventName'].replace(' ', '_')
            session_name_clean = session.name.replace(' ', '_')
            year_str = str(session.event.year)
            subprocess.run([
                sys.executable,
                'visualize_race.py',
                '--session', f'{event_name_clean}_{session_name_clean}',
                '--year', year_str,
                '--source', 'local'
            ], check=True)
            logger.info("\n[SUCCESS] Visualizations generated!")
        except Exception as e:
            logger.warning(f"[WARNING] Visualization step failed: {e}")

    except Exception as e:
        logger.error(f"\n[ERROR] Pipeline Error: {e}")
        import traceback
        logger.error(traceback.format_exc())
        raise


if __name__ == '__main__':
    import argparse
    import sys

    parser = argparse.ArgumentParser(description='F1 Telemetry Data Extraction Pipeline')
    parser.add_argument('--year', type=int, help='Year of the event')
    parser.add_argument('--event', help='Event name or round number')
    parser.add_argument('--session-type', choices=['FP1', 'FP2', 'FP3', 'Qualifying', 'Q', 'Sprint', 'S', 'SQ', 'Race', 'R'],
                       help='Session type (SQ = Sprint Qualifying)')
    parser.add_argument('--fetch-only', action='store_true',
                       help='Only fetch telemetry; skip aggregation and visualization (used by master_pipeline)')

    args = parser.parse_args()

    logger = get_logger()
    
    try:
        main(year=args.year, event=args.event, session_type=args.session_type)

        if args.fetch_only:
            logger.info("\n[INFO] --fetch-only mode: skipping aggregation and visualizations (handled by master_pipeline)")
        else:
            # Standalone usage: run aggregation, visualization, and cross-session analytics

            # Auto-run aggregation for visualizations
            logger.info("\n[AGGREGATING] Running aggregation for BI tools...")
            try:
                from aggregate_for_viz import aggregate_for_visualizations
                aggregate_for_visualizations()
                logger.info("\n[SUCCESS] Session aggregation complete")
            except Exception as e:
                logger.error(f"[WARNING] Aggregation failed: {e}")
                logger.error("Run manually: python aggregate_for_viz.py")
                raise

            # Automatically run visualizations for every session AFTER aggregation
            try:
                import subprocess
                # Reconstruct session info for visualization
                if args.year and args.event and args.session_type:
                    event_name_clean = str(args.event).replace(' ', '_')
                    session_type_map = {'FP1': 'Practice_1', 'FP2': 'Practice_2', 'FP3': 'Practice_3'}
                    session_type_clean = session_type_map.get(str(args.session_type), str(args.session_type))
                    year_str = str(args.year)
                    subprocess.run([
                        sys.executable,
                        'visualize_race.py',
                        '--session', f'{event_name_clean}_{session_type_clean}',
                        '--year', year_str,
                        '--source', 'local'
                    ], check=True)
                    logger.info("\n[SUCCESS] Visualizations generated!")
                else:
                    logger.warning("[WARNING] Visualization step skipped: session info missing")
            except Exception as e:
                logger.warning(f"[WARNING] Visualization step failed: {e}")

            # Run cross-session analytics
            logger.info("\n[AGGREGATING] Running cross-session analytics...")
            try:
                from cross_session_analytics import generate_cross_session_analytics
                generate_cross_session_analytics()
                logger.info("\n[SUCCESS] Cross-session analytics complete")
            except Exception as e:
                logger.warning(f"[WARNING] Cross-session analytics failed: {e}")
                logger.warning("Run manually: python cross_session_analytics.py")
                # Don't raise - cross-session is optional
        
        logger.info("\n[SUCCESS] PIPELINE COMPLETED SUCCESSFULLY")
    
    finally:
        # Save run summary
        logger.save_run_summary()
        logger.info("="*70)



#& "C:/repositories/formula 1/.venv/Scripts/python.exe" fetch_pipeline.py