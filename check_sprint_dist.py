import pandas as pd, glob
from coasting_analysis import extract_all_laps

files = sorted(glob.glob("telemetry_out/2026/Chinese_Grand_Prix/Sprint/*_telemetry.csv"))
for f in files:
    name = f.split("\\")[-1].replace("Chinese_Grand_Prix_Sprint_","").replace("_telemetry.csv","")
    df = pd.read_csv(f)
    laps = extract_all_laps(df, session_type="Sprint")
    if not laps:
        print(name, "-> no laps")
        continue
    best_n, best_df = max(laps, key=lambda x: float(pd.to_numeric(x[1].get("Speed", pd.Series()),errors="coerce").mean()) if "Speed" in x[1].columns else 0)
    d = pd.to_numeric(best_df.get("Distance", pd.Series(dtype=float)), errors="coerce")
    if d.isna().all():
        print(name, "-> ALL_NAN dist")
    else:
        print(name, "-> best lap", best_n, "dist max", round(d.max(), 2))
