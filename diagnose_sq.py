import pandas as pd

for drv in ['VER', 'LEC', 'ALB', 'GAS', 'NOR', 'BEA', 'LIN']:
    df = pd.read_csv(f'telemetry_out/2026/Chinese_Grand_Prix/Sprint_Qualifying/Chinese_Grand_Prix_Sprint_Qualifying_{drv}_telemetry.csv')
    nonzero = df[df['Speed'] > 0]
    first_nonzero_idx = nonzero.index[0] if len(nonzero) > 0 else -1
    print(f'=== {drv} === rows={len(df)}')
    print(df[['SessionTime','Speed','nGear','X','Y','Distance']].head(5).to_string())
    print(f'  first_nonzero_speed_row: {first_nonzero_idx}')
    if first_nonzero_idx > 0:
        print(df[['SessionTime','Speed','nGear','X','Y','Distance']].iloc[first_nonzero_idx:first_nonzero_idx+3].to_string())
    
    # Also check how many rows have Speed > 150 (truly on flying lap)
    fast = df[df['Speed'] > 150]
    print(f'  rows with Speed > 150: {len(fast)}')
    
    # Check the Distance at those fast rows
    if len(fast) > 0:
        print(f'  Distance at first fast row: {fast["Distance"].iloc[0]:.0f}')
        print(f'  Distance at last fast row: {fast["Distance"].iloc[-1]:.0f}')
    print()
