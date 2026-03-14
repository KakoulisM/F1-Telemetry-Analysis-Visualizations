from visualize_team_laps import load_all_laps
import numpy as np

if __name__ == '__main__':
    df = load_all_laps('telemetry_out')
    if df.empty:
        print('No telemetry data loaded')
        raise SystemExit(1)
    day = 1
    d = df[df['Day'] == day]
    if d.empty:
        print(f'No data for day {day}')
        raise SystemExit(1)
    counts = d.groupby('Driver')['LapTimeSeconds'].apply(lambda s: s.notna().sum()).sort_index()
    print(counts.to_string())
    avg = float(np.mean(counts.values)) if len(counts) > 0 else float('nan')
    print(f'AVERAGE_LAPS_PER_DRIVER_DAY{day}: {avg:.6f}')
