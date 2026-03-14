import pandas as pd
import numpy as np

drivers = ['VER', 'HAD', 'ALB', 'BEA', 'LIN', 'LEC', 'GAS', 'NOR', 'RUS', 'OCO', 'SAI', 'LAW', 'ALO', 'HUL']

for drv in drivers:
    df = pd.read_csv(f'telemetry_out/2026/Chinese_Grand_Prix/Sprint_Qualifying/Chinese_Grand_Prix_Sprint_Qualifying_{drv}_telemetry.csv')
    
    # How many rows have valid X/Y vs stuck at -8325,-7058
    stuck = df[(df['X'] == -8325.0) & (df['Y'] == -7058.0)]
    valid_pos = df[(df['X'].notna()) & (df['Y'].notna()) & ~((df['X'] == -8325.0) & (df['Y'] == -7058.0))]
    nan_pos = df[df['X'].isna()]
    
    # What is the max distance reached outside the starting box
    if len(valid_pos) > 0:
        max_dist = valid_pos['Distance'].max()
        fast_valid = valid_pos[valid_pos['Speed'] > 150]
        fast_count = len(fast_valid)
    else:
        max_dist = 0
        fast_count = 0
    
    user_status = 'OK ' if drv in ['VER','HAD','ALB','BEA','LIN'] else 'BAD'
    print(f'{user_status} {drv:>4}: stuck_rows={len(stuck):>5}  valid_pos={len(valid_pos):>5}  nan_pos={len(nan_pos):>4}  max_dist={max_dist:>10.0f}  fast+valid={fast_count}')
