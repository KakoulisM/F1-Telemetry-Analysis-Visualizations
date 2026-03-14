"""Fetch lap data from OpenF1 for Bahrain testing"""
import requests
import pandas as pd
import os

BASE_URL = "https://api.openf1.org/v1"

def fetch_session_laps(session_key, session_name):
    """Fetch all lap data for a session"""
    print(f"\n{'='*70}")
    print(f"Fetching: {session_name} (Session {session_key})")
    print('='*70)
    
    try:
        # Get drivers
        print("Fetching drivers...")
        resp = requests.get(f"{BASE_URL}/drivers?session_key={session_key}", timeout=10)
        resp.raise_for_status()
        drivers = resp.json()
        
        df_drivers = pd.DataFrame(drivers)
        print(f"  ✓ {len(drivers)} drivers")
        
        # Get laps
        print("Fetching laps...")
        resp = requests.get(f"{BASE_URL}/laps?session_key={session_key}", timeout=30)
        resp.raise_for_status()
        laps = resp.json()
        
        df_laps = pd.DataFrame(laps)
        print(f"  ✓ {len(laps)} laps")
        
        if len(df_laps) == 0:
            print("  [SKIP] No lap data available")
            return None
        
        # Show columns
        print(f"\nAvailable columns: {list(df_laps.columns)}")
        
        # Show summary stats
        print(f"\nSummary:")
        print(f"  Drivers with laps: {df_laps['driver_number'].nunique()}")
        print(f"  Total laps: {len(df_laps)}")
        
        if 'lap_duration' in df_laps.columns:
            # Filter valid laps (not outlaps/inlaps)
            valid_laps = df_laps[df_laps['lap_duration'].notna() & (df_laps['lap_duration'] > 80) & (df_laps['lap_duration'] < 120)]
            if len(valid_laps) > 0:
                print(f"  Valid racing laps: {len(valid_laps)}")
                print(f"  Fastest lap: {valid_laps['lap_duration'].min():.3f}s")
                fastest = valid_laps.loc[valid_laps['lap_duration'].idxmin()]
                print(f"    By driver {fastest.get('driver_number')}: {fastest.get('lap_duration'):.3f}s")
        
        if 'is_pit_out_lap' in df_laps.columns:
            pit_laps = df_laps['is_pit_out_lap'].sum()
            print(f"  Pit out laps: {pit_laps}")
        
        # Save to CSV
        output_dir = "openf1_testing_data"
        os.makedirs(output_dir, exist_ok=True)
        
        # Save laps
        lap_file = os.path.join(output_dir, f"session_{session_key}_laps.csv")
        df_laps.to_csv(lap_file, index=False)
        print(f"\n✓ Saved laps: {lap_file}")
        
        # Save drivers
        driver_file = os.path.join(output_dir, f"session_{session_key}_drivers.csv")
        df_drivers.to_csv(driver_file, index=False)
        print(f"✓ Saved drivers: {driver_file}")
        
        # Create summary by driver
        if 'lap_duration' in df_laps.columns and 'driver_number' in df_laps.columns:
            valid = df_laps[df_laps['lap_duration'].notna() & (df_laps['lap_duration'] > 80) & (df_laps['lap_duration'] < 120)]
            
            if len(valid) > 0:
                summary = valid.groupby('driver_number').agg({
                    'lap_duration': ['count', 'min', 'mean', 'median'],
                    'lap_number': 'max'
                }).round(3)
                
                summary.columns = ['_'.join(col).strip() for col in summary.columns.values]
                summary = summary.sort_values('lap_duration_min')
                
                print(f"\nTop 10 drivers by fastest lap:")
                print(summary.head(10).to_string())
                
                summary_file = os.path.join(output_dir, f"session_{session_key}_summary.csv")
                summary.to_csv(summary_file)
                print(f"\n✓ Saved summary: {summary_file}")
        
        return {'laps': df_laps, 'drivers': df_drivers}
        
    except Exception as e:
        print(f"[ERROR] {e}")
        import traceback
        traceback.print_exc()
        return None


def main():
    print("\n" + "="*70)
    print("FETCHING BAHRAIN TESTING DATA FROM OPENF1")
    print("="*70)
    
    sessions = [
        (11465, "Feb 11 - Day 1 (First Test)"),
        (11466, "Feb 12 - Day 2 (First Test)"),
        (11467, "Feb 13 - Day 3 (First Test)"),
    ]
    
    results = []
    
    for session_key, name in sessions:
        data = fetch_session_laps(session_key, name)
        if data:
            results.append((session_key, name, data))
    
    print("\n" + "="*70)
    print("FETCH COMPLETE")
    print("="*70)
    print(f"\nSuccessfully fetched {len(results)} sessions")
    print(f"Data saved to: openf1_testing_data/")
    
    print("\nWhat you have:")
    print("  ✓ Lap times for all drivers")
    print("  ✓ Lap numbers, sectors, compounds (if available)")
    print("  ✓ Pit stop information")
    print("\nWhat's missing:")
    print("  ✗ Detailed telemetry (speed, throttle, brake, GPS)")
    print("  ✗ Corner speeds and racing line analysis")
    
    print("\n[NOTE] This is the same limitation as FastF1 - detailed telemetry")
    print("not published by F1 for testing sessions.")


if __name__ == '__main__':
    main()
