import pandas as pd, numpy as np
from coasting_analysis import extract_best_lap, extract_all_laps
from pathlib import Path

CHECK = ['HAM','SAI','HAD','LIN','LEC','VER','ALB','LAW']
tele_dir = Path('telemetry_out/2026/Chinese_Grand_Prix/Qualifying')

for drv in CHECK:
    hits = list(tele_dir.glob(f'*_{drv}_telemetry.csv'))
    if not hits:
        print(drv, '- NO FILE')
        continue
    df = pd.read_csv(hits[0])
    df['SessionTime'] = pd.to_timedelta(df['SessionTime'], errors='coerce')

    # What extract_best_lap returns
    best = extract_best_lap(df, session_type='Qualifying')
    dist_max = pd.to_numeric(best['Distance'], errors='coerce').max()
    spd_mean = pd.to_numeric(best['Speed'], errors='coerce').mean()
    print(f'{drv}  bestlap rows={len(best)}  dist_max={dist_max:.0f}  spd_mean={spd_mean:.0f}')

    # What extract_all_laps returns
    laps = extract_all_laps(df, session_type='Qualifying')
    if laps:
        best_of = max(laps, key=lambda x: pd.to_numeric(x[1]['Speed'], errors='coerce').mean())
        b = best_of[1]
        dm = pd.to_numeric(b['Distance'], errors='coerce').max()
        sm = pd.to_numeric(b['Speed'], errors='coerce').mean()
        print(f'       all_laps n={len(laps)}  best#{best_of[0]}  dist_max={dm:.0f}  spd_mean={sm:.0f}')
    else:
        print('       all_laps: none found')
