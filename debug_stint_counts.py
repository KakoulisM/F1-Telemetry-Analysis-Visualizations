from visualize_team_laps import load_all_laps

combined = load_all_laps('telemetry_out')
if combined.empty:
    print('No data loaded')
else:
    print('Days present and file counts:')
    print(combined['Day'].value_counts(dropna=False))
    days = sorted([d for d in combined['Day'].dropna().unique()])
    for day in days:
        print('\nDay', day)
        df_day = combined[combined['Day'] == day]
        print(' Total rows:', len(df_day))
        drivers = df_day['Driver'].unique()
        print(' Drivers present:', len(drivers))
        counts = df_day.groupby('Driver')['Stint'].nunique()
        print(' Stint counts per driver (sample):')
        print(counts.sort_values().head(50))
        print(' Full list:')
        for drv, n in counts.sort_values().items():
            print(f'  {drv}: {n}')
