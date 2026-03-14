# Session Start Checklist

Rules the AI assistant must follow at the beginning of every conversation in this workspace.

---

## 1. Check Both APIs for Updates

Before doing any work, verify whether FastF1 or OpenF1 have released changes that could affect the pipeline.

### FastF1 (Python library)

```powershell
# Check installed version
python -c "import fastf1; print(fastf1.__version__)"

# Check latest available version on PyPI
pip index versions fastf1 2>$null | Select-String "Available"
```

Look for:
- New session type support (e.g. Sprint Qualifying, Sprint Shootout)
- Changes to `LapStartTime` / `SessionTime` offset behaviour for SQ sessions
- New or renamed columns in `session.laps`, `session.car_data`, `session.pos_data`
- Deprecation warnings in the cache layer
- Release notes at: https://docs.fastf1.dev/changelog.html

### OpenF1 (REST API)

```powershell
# Check the live API root to confirm it is reachable and inspect version header
curl -s -I "https://api.openf1.org/v1/sessions?session_key=latest" | Select-String "x-api|content-type|status"

# Spot-check a known endpoint for schema changes
curl -s "https://api.openf1.org/v1/car_data?session_key=latest&driver_number=1" | python -c "import sys,json; d=json.load(sys.stdin); print(list(d[0].keys()) if d else 'empty')"
```

Look for:
- New or removed columns in `/car_data` (Speed, RPM, nGear, Throttle, Brake, DRS)
- New or removed columns in `/position` (X, Y, Z)
- New session keys or session type name changes
- API base URL still `https://api.openf1.org/v1`
- Changelog at: https://openf1.org/#changelog

---

## 2. Known Issues to Keep in Mind

| Issue | Detail |
|---|---|
| FastF1 SQ LapStartTime offset | SQ sessions have ~16-min offset between FastF1 lap boundaries and telemetry `SessionTime`. Always detect SQ laps from the speed profile, not from FastF1 lap start/end times. |
| OpenF1 Brake scale | Brake values are **0–104 (percentage)**, not 0/1 boolean. Y-axis must be `(0, 110)`. |
| Intra overlay wrong-session bug | `simple_best_two_overlay` uses `rglob` without a session filter — can pick up telemetry files from a different session for the same driver code. Narrow `telemetry_dir` to the session-specific path. |
| Chinese GP P1 flat lines | OpenF1 returned pit-lane-only data for Chinese GP Practice 1 (bad upstream data). |
| ANT SQ standstill | ANT had a real on-track incident in Chinese GP SQ (~11 min standstill). Not a pipeline issue. |

---

## 3. Quick Environment Sanity Checks

```powershell
# Activate virtual environment (always do this first)
& "c:\repositories\formula 1\.venv\Scripts\Activate.ps1"

# Confirm key packages
python -c "import fastf1, pandas, numpy, matplotlib, snowflake.connector; print('OK')"

# Check scheduler is running (if applicable)
Get-ScheduledTask -TaskName "F1Pipeline*" -ErrorAction SilentlyContinue | Select-Object TaskName, State
```

---

## 4. Check for Unprocessed Sessions

```powershell
# Show sessions in the current schedule that have no telemetry output yet
python check_latest_event.py
```

---

## Notes

- Always read `visualize_race.py` before editing it — the file is large and context matters.
- Do **not** change visualizations or the pipeline unless explicitly asked.
- All telemetry files live under `telemetry_out/{year}/{Event}/{Session}/`.
- Corner distance data lives alongside telemetry: `circuit_corners.csv`.
