import pandas as pd
import fastf1

fastf1.Cache.enable_cache('f1_cache')

print('Loading Chinese GP Practice 1...')
session = fastf1.get_session(2026, 'Chinese Grand Prix', 'FP1')
session.load()
print('Session loaded')
print('Laps empty?', session.laps.empty)
print('Laps shape:', session.laps.shape)

print()
try:
    car = session.car_data
    print('car_data type:', type(car))
    if car:
        first_key = list(car.keys())[0]
        df = car[first_key]
        print(f'Driver {first_key} car_data shape: {df.shape}')
        sp = df['Speed']
        print(f'Driver {first_key} Speed: min={sp.min()}, max={sp.max()}, mean={sp.mean():.1f}')
        print(f'nGear unique:', df['nGear'].unique()[:10])
        print(f'Sample:')
        print(df.head())
    else:
        print('car_data is empty dict!')
except Exception as e:
    print('car_data error:', type(e).__name__, e)

print()
try:
    pos = session.pos_data
    print('pos_data type:', type(pos))
    if pos:
        first_key = list(pos.keys())[0]
        df = pos[first_key]
        print(f'Driver {first_key} pos_data shape: {df.shape}')
        print(f'Sample:')
        print(df.head())
    else:
        print('pos_data is empty dict!')
except Exception as e:
    print('pos_data error:', type(e).__name__, e)
