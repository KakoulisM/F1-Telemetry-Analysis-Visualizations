

import math
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
import os

# List of drivers
DRIVERS = ['LEC', 'HAM', 'RUS', 'ANT', 'NOR', 'VER']
# List of corners to show
CORNERS = [8, 9, 10, 11]
# Session info
SESSION_DIR = 'telemetry_out/2026/Australian_Grand_Prix/Race'

# Collect lap-by-lap apex speeds for each driver and corner
all_data = []
for driver in DRIVERS:
    corners_file = os.path.join(SESSION_DIR, f'Australian_Grand_Prix_Race_{driver}_corners.csv')
    if not os.path.exists(corners_file):
        continue
    df = pd.read_csv(corners_file)
    df = df[df['Corner'].isin(CORNERS)]
    df['Driver'] = driver
    all_data.append(df[['LapNumber', 'Corner', 'EntrySpeed', 'ApexSpeed', 'ExitSpeed', 'Driver']])

if not all_data:
    print('No data found for requested drivers/corners.')
    exit()

# Combine all drivers' data
combined = pd.concat(all_data)

# Use actual laps present in data (no hardcoded total)
TOTAL_LAPS = int(combined['LapNumber'].max())
ALL_LAPS = list(range(1, TOTAL_LAPS + 1))

METRICS = [
    ('EntrySpeed', 'Entry Speed (km/h)', 'Blues',    'entry_speeds_heatmap.png'),
    ('ApexSpeed',  'Apex Speed (km/h)',  'YlOrRd',   'apex_speeds_heatmap.png'),
    ('ExitSpeed',  'Exit Speed (km/h)',  'Greens',   'exit_speeds_heatmap.png'),
]

drivers = sorted(combined['Driver'].unique())
n_drivers = len(drivers)
ncols = 2
nrows = math.ceil(n_drivers / ncols)

FIG_W = max(10, TOTAL_LAPS * 0.65)
FIG_H_PER_ROW = 4.5

for metric_col, metric_label, cmap_name, out_file in METRICS:
    fig, axes = plt.subplots(nrows, ncols,
                             figsize=(FIG_W, nrows * FIG_H_PER_ROW),
                             sharey=True)
    axes = axes.flatten()

    for i, driver in enumerate(drivers):
        df_d = combined[combined['Driver'] == driver]
        mat = (df_d
               .pivot_table(index='Corner', columns='LapNumber', values=metric_col, aggfunc='mean')
               .reindex(index=CORNERS)
               .reindex(columns=ALL_LAPS))

        # Build annotation array: numbers where data exists, empty string otherwise
        annot = mat.applymap(lambda v: f'{v:.0f}' if pd.notna(v) else '')

        sns.heatmap(mat,
                    ax=axes[i],
                    cmap=cmap_name,
                    annot=annot,
                    fmt='',
                    annot_kws={'size': 6},
                    linewidths=0.3,
                    cbar=True,
                    cbar_kws={'label': metric_label, 'shrink': 0.6})
        axes[i].set_title(driver, fontsize=12, fontweight='bold')
        axes[i].set_xlabel('Lap', fontsize=9)
        axes[i].set_ylabel('Corner', fontsize=9)
        axes[i].tick_params(axis='x', labelsize=7, rotation=90)
        axes[i].tick_params(axis='y', labelsize=8, rotation=0)

    for j in range(i + 1, len(axes)):
        fig.delaxes(axes[j])

    fig.suptitle(f'2026 Australian GP — {metric_label} | Corners 8-11, Laps 1-{TOTAL_LAPS}',
                 fontsize=14, fontweight='bold', y=1.005)
    plt.tight_layout()
    plt.savefig(out_file, dpi=150, bbox_inches='tight')
    plt.close()
    print(f'Saved: {out_file}')

# Print per lap, all drivers, entry/apex/exit speeds for corners 8-11
print('\n--- Per Lap Speeds for Turns 8-11 ---')
for driver in DRIVERS:
    df_d = combined[combined['Driver'] == driver]
    for lap in sorted(df_d['LapNumber'].unique()):
        lap_df = df_d[df_d['LapNumber'] == lap]
        print(f'\nDriver: {driver} | Lap: {lap}')
        for corner in CORNERS:
            row = lap_df[lap_df['Corner'] == corner]
            if not row.empty:
                entry = row['EntrySpeed'].values[0] if 'EntrySpeed' in row else None
                apex = row['ApexSpeed'].values[0] if 'ApexSpeed' in row else None
                exit = row['ExitSpeed'].values[0] if 'ExitSpeed' in row else None
                def fmt(val):
                    try:
                        return f'{float(val):.1f}'
                    except (TypeError, ValueError):
                        return 'N/A'
                print(f'  Corner {corner}: Entry={fmt(entry)}, Apex={fmt(apex)}, Exit={fmt(exit)}')
            else:
                print(f'  Corner {corner}: No data')
print('\nDone.')
