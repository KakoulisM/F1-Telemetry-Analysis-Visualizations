# F1 Pipeline Orchestration - Complete Automation Guide

## 🎯 Overview

This is a **fully automated F1 analytics pipeline** that runs on the official F1 race calendar. Every session is automatically processed 2 hours after completion (when FastF1 data becomes available).

## 📊 What Gets Automated

### Complete Pipeline Execution (6-8 minutes):

1. **Telemetry Extraction** → Fetches lap times, sectors, corners, speeds for all 20 drivers
2. **Warehouse Loading** → Loads 26 analytics tables to Snowflake cloud
3. **Racing Line Analysis** → Identifies optimal lines + driver performance gaps
4. **Tire Degradation ML** → Retrains model + generates predictions
5. **Driver Reports** → Creates individual folders with analysis per driver
6. **Summary** → Logs execution status and errors

### Output Structure

```
driver_reports/
├── 2025_Abu_Dhabi_Race/
│   ├── VER/
│   │   ├── racing_line_analysis.json      # Corner-by-corner analysis
│   │   ├── corner_analysis.png            # Visual heatmap
│   │   └── summary.json                   # Quick stats
│   ├── HAM/
│   ├── LEC/
│   └── ... (all 20 drivers)
├── 2026_Bahrain_Race/
└── ...
```

---

## 🚀 How to Use

### Option 1: Automated Schedule (Production)

**Set it and forget it** - runs automatically on F1 calendar

#### Windows Service Setup:
```powershell
# Install scheduler as Windows service
./setup_scheduler.ps1

# Check status
Get-Service "F1TelemetryScheduler"

# View logs
Get-Content logs/scheduler_*.log -Tail 50 -Wait
```

#### Manual Scheduler (Testing):
```powershell
# Run scheduler in foreground
python scheduler.py
```

**How it works:**
- Scheduler checks every **15 minutes** if a session completed
- Waits **2 hours** after session end (FastF1 data availability)
- Executes `master_pipeline.py` automatically
- Tracks processed sessions to avoid duplicates
- Retries 3 times if errors occur

---

### Option 2: Manual Execution (Testing/Debugging)

#### Run Full Pipeline Now:
```powershell
# Process latest session
python run_pipeline_now.py

# Process specific session
python run_pipeline_now.py --session Abu_Dhabi_Grand_Prix_Race
```

#### Run Individual Stages:
```powershell
# Stage 1: Telemetry only
python fetch_pipeline.py

# Stage 2: Warehouse only
python aggregate_for_viz.py

# Stage 3: Racing lines
python racing_line_analyzer.py Abu_Dhabi_Grand_Prix_Race

# Stage 4: Tire model training
python tire_degradation_model.py train

# Stage 5: Tire predictions
python tire_degradation_model.py predict Abu_Dhabi_Grand_Prix_Race 5
```

---

## 📅 F1 Schedule Configuration

### Edit Schedule File: `schedule/schedule_2026.json`

```json
{
  "season": 2026,
  "races": [
    {
      "round": 1,
      "event_name": "Bahrain Grand Prix",
      "location": "Sakhir",
      "date": "2026-03-01",
      "sessions": {
        "FP1": "2026-02-28T11:30:00Z",
        "FP2": "2026-02-28T15:00:00Z",
        "FP3": "2026-03-01T12:00:00Z",
        "Qualifying": "2026-03-01T15:00:00Z",
        "Race": "2026-03-02T15:00:00Z"
      }
    }
  ]
}
```

**Scheduler automatically:**
- Adds 2-hour buffer after each session
- Checks if data is available
- Processes in order: FP1 → FP2 → FP3 → Qualifying → Race

---

## 🔄 Pipeline Flow Details

### Stage 1: Telemetry Extraction
**File:** `fetch_pipeline.py`  
**Duration:** 2-3 minutes  
**Output:**
- `telemetry_out/*.csv` - Raw lap data, corners, telemetry for all drivers
- `session_meta.json` - Event metadata
- `weather_data.json` - Track conditions

### Stage 2: Warehouse Loading
**File:** `aggregate_for_viz.py`  
**Duration:** 1-2 minutes  
**Output:**
- Snowflake tables: `F1DATA.PUBLIC.*`
  - VIZ_LAP_PROGRESSION
  - VIZ_BEST_LAPS
  - VIZ_SECTOR_PERFORMANCE
  - VIZ_TIRE_DEGRADATION
  - VIZ_CORNER_COMPARISON
  - ... (26 total tables)

### Stage 3: Racing Line Analysis
**File:** `racing_line_analyzer.py`  
**Duration:** 2-3 minutes  
**Output:**
- Snowflake tables:
  - RACING_LINE_OPTIMAL_CORNERS
  - RACING_LINE_DRIVER_DELTAS
- `telemetry_out/viz/*_corner_analysis.png` - Visualizations

### Stage 4: ML Training
**File:** `tire_degradation_model.py train`  
**Duration:** 1-2 minutes  
**Output:**
- `models/tire_deg_model.pkl` - Updated ML model
- Cumulative training on all historical sessions

### Stage 5: ML Predictions
**File:** `tire_degradation_model.py predict`  
**Duration:** 30-60 seconds  
**Output:**
- Snowflake table: TIRE_DEGRADATION_PREDICTIONS
- Pit window recommendations per driver

### Stage 6: Driver Reports
**File:** `master_pipeline.py` (internal)  
**Duration:** 30 seconds  
**Output:**
- `driver_reports/<session>/<driver>/` folders
- JSON reports + visualizations per driver

---

## 📁 Directory Structure

```
C:\repositories\formula 1\
├── master_pipeline.py          # Main orchestrator
├── run_pipeline_now.py         # Manual execution script
├── scheduler.py                # Automated scheduler
│
├── fetch_pipeline.py           # Stage 1
├── aggregate_for_viz.py        # Stage 2
├── racing_line_analyzer.py     # Stage 3
├── tire_degradation_model.py   # Stage 4 & 5
│
├── schedule/
│   └── schedule_2026.json      # F1 calendar
│
├── logs/
│   ├── scheduler_*.log         # Scheduler execution logs
│   ├── processed_sessions.json # Tracking processed sessions
│   └── pipeline_executions/    # Per-run summaries
│       └── pipeline_20260125_160000.json
│
├── driver_reports/             # AUTOMATED OUTPUT
│   └── 2025_Abu_Dhabi_Race/
│       ├── VER/
│       ├── HAM/
│       └── ...
│
├── telemetry_out/              # Raw CSV data
├── models/                     # ML models
└── f1_cache/                   # FastF1 cache
```

---

## 🛠 Customization

### Change 2-Hour Delay Buffer

Edit `scheduler.py`:
```python
def __init__(self, schedule_file='schedule/schedule_2026.json', buffer_hours=2):
    self.buffer_hours = buffer_hours  # Change to 1, 3, etc.
```

### Change Check Interval

Edit `scheduler.py`:
```python
def run_continuous(self):
    while True:
        self.run_once()
        time.sleep(900)  # Change 900s (15 min) to desired interval
```

### Add Custom Stages

Edit `master_pipeline.py`, add new stage method:
```python
def stage_7_custom_analysis(self) -> bool:
    return self.run_stage(
        "Custom Analysis",
        [self.python_exe, "my_custom_script.py"],
        timeout=120
    )
```

Then add to `run_full_pipeline()`:
```python
stages = [
    # ... existing stages ...
    ("7/7", self.stage_7_custom_analysis),
]
```

---

## 📊 Monitoring & Logs

### Check Pipeline Status
```powershell
# View latest execution
Get-Content logs/pipeline_executions/*.json | ConvertFrom-Json | Select -Last 1

# Monitor scheduler
Get-Content logs/scheduler_*.log -Tail 50 -Wait

# Check for errors
Get-Content logs/scheduler_*.log | Select-String "ERROR"
```

### Execution Summary Format
```json
{
  "execution_id": "2026-01-25T16:00:00",
  "session": "Abu_Dhabi_Grand_Prix_Race",
  "total_duration": "387.2s",
  "stages": {
    "Telemetry Extraction": {
      "status": "SUCCESS",
      "duration": "142.1s"
    },
    "Snowflake Warehouse Loading": {
      "status": "SUCCESS",
      "duration": "87.3s"
    }
  },
  "errors": [],
  "status": "SUCCESS"
}
```

---

## 🔧 Troubleshooting

### Pipeline Fails to Start
```powershell
# Check Python environment
& "C:/repositories/formula 1/.venv/Scripts/python.exe" --version

# Test individual stage
python fetch_pipeline.py

# Check Snowflake connection
python -c "from snowflake_config import SNOWFLAKE_CONFIG; print('OK')"
```

### Scheduler Not Running
```powershell
# Check Windows service
Get-Service "F1TelemetryScheduler"

# Restart service
Restart-Service "F1TelemetryScheduler"

# Run manually
python scheduler.py
```

### Driver Reports Not Generated
```powershell
# Check if telemetry exists
dir telemetry_out/*.csv

# Run report stage manually
python master_pipeline.py --session Abu_Dhabi_Grand_Prix_Race
```

### Snowflake Connection Issues
1. Check `.env` file has valid credentials
2. Verify warehouse is running in Snowflake UI
3. Test connection: `python status_api.py` → visit http://localhost:5000/api/snowflake/test

---

## 🎓 Example Workflow

### Scenario: Bahrain Grand Prix Sunday

**14:00 UTC** - Race starts  
**16:00 UTC** - Race finishes  
**18:00 UTC** - FastF1 data becomes available  

**Scheduler automatically:**

1. **18:00** - Detects Bahrain Race completed + 2hr passed
2. **18:00** - Starts `master_pipeline.py`
3. **18:03** - Telemetry extracted (1156 laps)
4. **18:04** - Snowflake tables updated
5. **18:06** - Racing line analysis complete
6. **18:07** - Tire model retrained
7. **18:08** - Driver reports generated
8. **18:08** - Pipeline completes successfully

**Result:**
- `driver_reports/2026_Bahrain_Race/` created with 20 driver folders
- Snowflake has latest data for Power BI dashboards
- ML model learned from Bahrain for Saudi Arabia predictions
- All logs saved to `logs/pipeline_executions/`

---

## 📈 Production Recommendations

1. **Run as Windows Service** - Use `setup_scheduler.ps1` for automatic startup
2. **Monitor Logs** - Set up daily log review or alerts
3. **Backup Reports** - Copy `driver_reports/` to cloud storage regularly
4. **Snowflake Monitoring** - Check warehouse usage and costs
5. **Model Versioning** - Archive `models/tire_deg_model.pkl` after each season

---

## 🚨 Emergency Stop

```powershell
# Stop Windows service
Stop-Service "F1TelemetryScheduler"

# Kill running pipeline
Get-Process python | Where-Object {$_.CommandLine -like "*master_pipeline*"} | Stop-Process
```

---

## 📞 Support

- Check logs: `logs/`
- Review execution summaries: `logs/pipeline_executions/`
- Test individual scripts before running full pipeline
- Use `run_pipeline_now.py` for testing before production
