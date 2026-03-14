# F1 Telemetry Pipeline - Commands & Expected Outcomes

## 📋 Quick Reference Guide

This document contains all commands to run, test, and deploy the F1 telemetry pipeline, along with expected outputs.

---

## 🚀 Initial Setup

### Install Dependencies

```bash
pip install -r requirements.txt
```

**Expected Outcome:**
```
Successfully installed fastf1-3.4.0 pandas-2.x numpy-1.x scipy-1.x matplotlib-3.x
snowflake-connector-python-3.x requests-2.x flask-3.x flask-cors-4.x tqdm-4.x
```

### Configure Environment

Edit `.env` file with your credentials:
```bash
notepad .env
```

**Required Variables:**
```
SNOWFLAKE_ACCOUNT=your_account
SNOWFLAKE_USER=your_username
SNOWFLAKE_PASSWORD=your_password
OPENWEATHER_API_KEY=your_api_key
```

---

## 🏁 Run Pipeline Manually

### Fetch Most Recent Session

```bash
python fetch_pipeline.py
```

**Expected Outcome:**
```
======================================================================
F1 TELEMETRY PIPELINE STARTED
======================================================================
Searching for the most recent session...
✓ Found: Abu Dhabi Grand Prix 2025 - R

Processing telemetry for Abu Dhabi Grand Prix Race
Driver  1/20: ALB
Driver  2/20: ALO
...
Driver 20/20: VER

✅ Data extraction complete!

🌤️  Fetching weather data...
✓ Weather: 24.5°C, clear sky
  Humidity: 45%, Wind: 3.2 m/s

🔄 Running aggregation for BI tools...
======================================================================
Creating visualization-ready aggregations
Session: Abu Dhabi Grand Prix - Race (2025)
Target Schema: Abu_Dhabi_Grand_Prix_Race_2025
======================================================================

✓ Connected to Snowflake database: F1DATA
✓ Schema created: Abu_Dhabi_Grand_Prix_Race_2025

📊 Aggregating lap time progression...
  ✓ viz_lap_progression (1234 rows)
📊 Aggregating best laps...
  ✓ viz_best_laps (20 rows)
...
🌤️  Storing weather conditions...
  ✓ viz_weather_conditions (1 rows)
  Temperature: 24.5°C, Humidity: 45%

🔖 Flagging qualifying-style laps (classifier)
  • Running: `python tools/qual_vs_race_classifier.py --input-dir telemetry_out --output telemetry_out/qual_flags.csv`
  ✓ Saved flags: `telemetry_out/qual_flags.csv` (driver, lap, predicted label, probability)

✅ Data loaded to Snowflake: F1DATA.Abu_Dhabi_Grand_Prix_Race_2025
   Total tables: 26 visualization-ready datasets

🔄 Running cross-session analytics...
✅ Cross-session analytics complete

✅ PIPELINE COMPLETED SUCCESSFULLY
```

**Files Created:**
- `telemetry_out/Abu_Dhabi_Grand_Prix_Race_*_laps.csv`
- `telemetry_out/Abu_Dhabi_Grand_Prix_Race_*_telemetry.csv`
- `telemetry_out/Abu_Dhabi_Grand_Prix_Race_*_corners.csv`
- `telemetry_out/session_meta.json`
- `telemetry_out/weather_data.json`
- `telemetry_out/qual_flags.csv`  # classifier output: predicted labels and probabilities
- `logs/pipeline_YYYYMMDD.log`
- `logs/run_summaries/run_YYYYMMDD_HHMMSS.json`

---

## 📊 Run Aggregation Only

If you already have CSV files and want to re-aggregate:

```bash
python aggregate_for_viz.py
```

**Expected Outcome:**
```
======================================================================
Creating visualization-ready aggregations
Session: Abu Dhabi Grand Prix - Race (2025)
Target Schema: Abu_Dhabi_Grand_Prix_Race_2025
======================================================================

Processing 20 drivers: ALB, ALO, ANT, BEA, BOR, COL, GAS, HAD, HAM...

✓ viz_lap_progression (1234 rows)
✓ viz_best_laps (20 rows)
✓ viz_sector_performance (60 rows)
... (26 tables total)
✓ viz_weather_conditions (1 rows)

✅ Data loaded to Snowflake: F1DATA.Abu_Dhabi_Grand_Prix_Race_2025
```

---

## 🔄 Cross-Session Analytics

Generate championship standings and historical analysis:

```bash
python cross_session_analytics.py
```

**Expected Outcome:**
```
======================================================================
CROSS-SESSION ANALYTICS - Aggregating Historical Data
======================================================================

Scanning for session schemas...
✓ Found 15 session schemas

Generating ANALYTICS_SEASON_STANDINGS...
  ✓ 20 drivers with championship points

Generating ANALYTICS_DRIVER_TRENDS...
  ✓ 300 driver-event records

Generating ANALYTICS_CIRCUIT_RECORDS...
  ✓ 15 circuit fastest laps

Generating ANALYTICS_HEAD_TO_HEAD...
  ✓ 190 head-to-head matchups

Generating ANALYTICS_TIRE_STRATEGY...
  ✓ 100 tire compound analyses

✅ Cross-session analytics complete
   5 tables created in PUBLIC schema
```

---

## 🕐 Automated Scheduler

### Run Scheduler Continuously

```bash
python scheduler.py --mode continuous --buffer 2
```

**Expected Outcome:**
```
======================================================================
F1 SESSION SCHEDULER - CONTINUOUS MODE
======================================================================

Loading schedule from: schedule/schedule_2026.json
Loaded 24 events for 2026 season

Checking for sessions to process...
No sessions need processing (must be 2-24 hours after completion)

Next session: Bahrain Grand Prix - Race
Session ends: 2026-03-02T17:00:00Z
Check time: 2026-03-02T19:15:00Z (end + 2hr buffer + 15min wake)

Sleeping until 2026-03-02T19:15:00Z
Sleep duration: 36 days, 23 hours, 45 minutes

💤 Scheduler sleeping... (will wake automatically)
```

**What Happens Next:**
1. Scheduler sleeps until session + buffer time
2. Wakes up automatically
3. Runs `fetch_pipeline.py`
4. Processes telemetry data
5. Loads to Snowflake
6. Goes back to sleep until next session

### Check Scheduler Status

```bash
python -c "import json; print(json.dumps(json.load(open('logs/health.json')), indent=2))"
```

**Expected Outcome:**
```json
{
  "status": "sleeping",
  "last_check": "2026-01-24T18:30:00+00:00",
  "next_check": "2026-03-02T19:15:00+00:00",
  "last_session_processed": "Abu Dhabi Grand Prix Race 2025",
  "processed_count": 15
}
```

---

## 🌐 Status API & Dashboard

### Start API Server

```bash
python status_api.py
```

**Expected Outcome:**
```
======================================================================
F1 Telemetry Pipeline Status API
======================================================================
Dashboard: http://localhost:5000
API Endpoints:
  • http://localhost:5000/api/health
  • http://localhost:5000/api/status
  • http://localhost:5000/api/latest-run
  • http://localhost:5000/api/snowflake
  • http://localhost:5000/api/schedule
  • http://localhost:5000/api/errors
  • http://localhost:5000/api/schemas
  • http://localhost:5000/api/live
======================================================================

 * Running on http://127.0.0.1:5000
```

**Access Dashboard:**
Open browser to http://localhost:5000

**Test API Endpoints:**

```bash
# Health check
curl http://localhost:5000/api/health
```
```json
{
  "status": "healthy",
  "last_check": "2026-01-24T18:30:00+00:00",
  "minutes_since_update": 2.3,
  "scheduler_status": "sleeping",
  "last_session_processed": "Abu Dhabi Grand Prix Race 2025"
}
```

```bash
# Full status
curl http://localhost:5000/api/status
```
```json
{
  "timestamp": "2026-01-24T18:35:00+00:00",
  "health": {...},
  "latest_run": {...},
  "snowflake": {"connected": true},
  "upcoming_sessions": [...]
}
```

```bash
# Upcoming sessions
curl http://localhost:5000/api/schedule
```
```json
{
  "upcoming_sessions": [
    {
      "event": "Bahrain Grand Prix",
      "session": "RACE",
      "end_time": "2026-03-02T17:00:00Z",
      "hours_until": 912.5
    }
  ]
}
```

```bash
# List all schemas
curl http://localhost:5000/api/schemas
```
```json
{
  "schemas": [
    {"schema": "Abu_Dhabi_Grand_Prix_Race_2025", "created": "2025-12-08"},
    {"schema": "Abu_Dhabi_Grand_Prix_Qualifying_2025", "created": "2025-12-07"}
  ],
  "count": 15
}
```

---

## 📡 Live Session Monitoring

### Monitor Live Session (During Race)

```bash
python live_session_monitor.py 2026 "Bahrain Grand Prix" R 30
```

**Expected Outcome:**
```
Starting live monitor for Bahrain Grand Prix R
Update interval: 30s

Session loaded: Bahrain Grand Prix Race

=== Update #1 ===
Status: active
Progress: 15.2%
Laps: 8

New laps: 3 | Total: 8
Saved 160 laps to live_laps.json

Sleeping 30s until next update...

=== Update #2 ===
Status: active
Progress: 28.4%
Laps: 15

New laps: 7 | Total: 15
...

=== Update #58 ===
Status: finished
Progress: 100.0%
Laps: 57

Session finished, stopping monitor
Live monitoring complete
```

**Files Created:**
- `telemetry_out/live/live_status.json`
- `telemetry_out/live/live_laps.json`

**Dashboard Updates Automatically:**
Dashboard at http://localhost:5000 shows LIVE badge and real-time positions.

---

## 🐳 Docker Deployment

### Build Docker Image

```bash
docker-compose build
```

**Expected Outcome:**
```
Building f1-scheduler
[+] Building 45.2s (18/18) FINISHED
 => [internal] load build definition from Dockerfile
 => [internal] load .dockerignore
 => [internal] load metadata for docker.io/library/python:3.11-slim
 => [1/9] FROM docker.io/library/python:3.11-slim
 => [2/9] WORKDIR /app
 => [3/9] COPY requirements.txt .
 => [4/9] RUN pip install --no-cache-dir -r requirements.txt
 => [5/9] COPY fetch_pipeline.py .
 ...
 => exporting to image
 => => naming to docker.io/library/formula-1_f1-scheduler
 => => naming to docker.io/library/formula-1_status-api
```

### Start Containers

```bash
docker-compose up -d
```

**Expected Outcome:**
```
Creating network "formula-1_default" with the default driver
Creating f1-telemetry-scheduler ... done
Creating f1-status-api          ... done
```

### View Logs

```bash
docker-compose logs -f
```

**Expected Outcome:**
```
f1-scheduler    | ======================================================================
f1-scheduler    | F1 TELEMETRY SCHEDULER - AUTONOMOUS MODE
f1-scheduler    | ======================================================================
f1-scheduler    | Loading schedule from: schedule/schedule_2026.json
f1-scheduler    | Next session: Bahrain Grand Prix - Race
f1-scheduler    | Sleeping until 2026-03-02T19:15:00Z
f1-scheduler    | 💤 Scheduler sleeping...
f1-scheduler    | 
status-api      | ======================================================================
status-api      | F1 Telemetry Pipeline Status API
status-api      | ======================================================================
status-api      | Dashboard: http://localhost:5000
status-api      |  * Running on http://0.0.0.0:5000
```

### Check Container Health

```bash
docker-compose ps
```

**Expected Outcome:**
```
         Name                   Command          State               Ports
-----------------------------------------------------------------------------------
f1-telemetry-scheduler   ./entrypoint.sh      Up (healthy)
f1-status-api           python status_api.py  Up (healthy)   0.0.0.0:5000->5000/tcp
```

### Access Dashboard (Docker)

Open browser to http://localhost:5000

### Stop Containers

```bash
docker-compose down
```

**Expected Outcome:**
```
Stopping f1-status-api          ... done
Stopping f1-telemetry-scheduler ... done
Removing f1-status-api          ... done
Removing f1-telemetry-scheduler ... done
```

---

## 🔍 Testing & Validation

### Test Snowflake Connection

```bash
python -c "from snowflake_config import SNOWFLAKE_CONFIG; import snowflake.connector; conn = snowflake.connector.connect(**SNOWFLAKE_CONFIG); print('✓ Connected to Snowflake'); conn.close()"
```

**Expected Outcome:**
```
✓ Connected to Snowflake
```

### Test Weather API

```bash
python -c "from weather_integration import WeatherDataFetcher; f = WeatherDataFetcher(); w = f.get_session_weather('Silverstone Circuit'); print(f'Weather: {w[\"temperature\"]}°C, {w[\"weather_description\"]}' if w else 'Failed')"
```

**Expected Outcome:**
```
Weather: 18.3°C, scattered clouds
```

### Validate Latest Run

```bash
python -c "import json; run = json.load(open(sorted(__import__('pathlib').Path('logs/run_summaries').glob('*.json'))[-1])); print(f'Session: {run[\"session\"]}\nTables: {run[\"tables_created\"]}\nRows: {run[\"total_rows\"]}\nStatus: {run[\"status\"]}')"
```

**Expected Outcome:**
```
Session: Abu Dhabi Grand Prix Race 2025
Tables: 26
Rows: 15,234
Status: completed
```

### Check Scheduler Syntax

```bash
python -m py_compile scheduler.py
echo $?
```

**Expected Outcome:**
```
0
```
(No output means success)

---

## 📊 Snowflake Queries

### Connect to Snowflake

Use SnowSQL, Power BI, or any SQL client:
```
Account: SRAFERN-PH02712.snowflakecomputing.com
Database: F1DATA
Warehouse: COMPUTE_WH
```

### List All Session Schemas

```sql
SHOW SCHEMAS IN DATABASE F1DATA;
```

**Expected Result:**
```
Abu_Dhabi_Grand_Prix_Race_2025
Abu_Dhabi_Grand_Prix_Qualifying_2025
...
```

### Query Lap Times

```sql
SELECT 
    Driver,
    LapNumber,
    LapTimeSeconds
FROM Abu_Dhabi_Grand_Prix_Race_2025.viz_lap_progression
WHERE LapTimeSeconds < 90
ORDER BY LapTimeSeconds
LIMIT 10;
```

**Expected Result:**
```
VER    12    88.234
NOR    15    88.456
...
```

### Check Weather Impact

```sql
SELECT 
    l.Driver,
    AVG(l.LapTimeSeconds) as AvgLapTime,
    w.Temperature,
    w.WeatherMain
FROM Abu_Dhabi_Grand_Prix_Race_2025.viz_lap_progression l
CROSS JOIN Abu_Dhabi_Grand_Prix_Race_2025.viz_weather_conditions w
GROUP BY l.Driver, w.Temperature, w.WeatherMain
ORDER BY AvgLapTime;
```

### Championship Standings

```sql
SELECT 
    Driver,
    TotalPoints,
    Wins,
    Podiums
FROM PUBLIC.ANALYTICS_SEASON_STANDINGS
ORDER BY TotalPoints DESC;
```

---

## 🛠️ Troubleshooting

### Pipeline Fails

```bash
# Check logs
cat logs/pipeline_$(date +%Y%m%d).log

# Check error summary
python -c "import json; run = json.load(open(sorted(__import__('pathlib').Path('logs/run_summaries').glob('*.json'))[-1])); print('\n'.join(run.get('errors', [])))"
```

### Scheduler Not Running

```bash
# Check if process is running
ps aux | grep scheduler.py

# Check health file
cat logs/health.json

# Restart manually
python scheduler.py --mode continuous
```

### Snowflake Connection Error

```bash
# Verify credentials
python -c "from snowflake_config import SNOWFLAKE_CONFIG; print(SNOWFLAKE_CONFIG)"

# Test connection
python -c "import snowflake.connector; from snowflake_config import SNOWFLAKE_CONFIG; conn = snowflake.connector.connect(**SNOWFLAKE_CONFIG); print('Connected'); conn.close()"
```

### Weather Data Not Fetching

```bash
# Check API key
python -c "import os; from pathlib import Path; env = {k:v for line in open('.env') if '=' in line for k,v in [line.strip().split('=',1)]}; print(f'API Key: {env.get(\"OPENWEATHER_API_KEY\", \"NOT SET\")[:10]}...')"

# Test API
curl "http://api.openweathermap.org/data/2.5/weather?lat=52.0786&lon=-1.0169&appid=YOUR_KEY"
```

### Dashboard Not Loading

```bash
# Check if Flask is running
curl http://localhost:5000/api/health

# Restart API
python status_api.py

# Check port availability
netstat -an | grep 5000
```

---

## 📁 File Structure Reference

```
formula 1/
├── fetch_pipeline.py           # Main telemetry extraction
├── aggregate_for_viz.py        # Creates 26 viz tables
├── cross_session_analytics.py  # Historical aggregations
├── scheduler.py                # Automated session monitoring
├── pipeline_logger.py          # Centralized logging
├── snowflake_config.py         # Snowflake credentials
├── weather_integration.py      # OpenWeatherMap API
├── status_api.py               # REST API + Dashboard
├── live_session_monitor.py     # Real-time session tracking
├── requirements.txt            # Python dependencies
├── Dockerfile                  # Container image
├── docker-compose.yml          # Multi-container setup
├── .env                        # Credentials (DO NOT COMMIT)
├── env.example                 # Template file
├── schedule/
│   └── schedule_2026.json      # F1 calendar
├── static/
│   └── dashboard.html          # Web dashboard UI
├── logs/
│   ├── pipeline_*.log          # Daily logs
│   ├── health.json             # Scheduler health
│   └── run_summaries/          # Run statistics
├── telemetry_out/
│   ├── *_laps.csv              # Lap data
│   ├── *_telemetry.csv         # Full telemetry
│   ├── *_corners.csv           # Corner analysis
│   ├── session_meta.json       # Session metadata
│   ├── weather_data.json       # Weather conditions
│   └── live/                   # Live session data
└── f1_cache/                   # FastF1 API cache
```

---

## 🎯 Common Workflows

### Process Most Recent Session
```bash
python fetch_pipeline.py
# Automatically runs aggregation and cross-session analytics
```

### Run Pipeline in Docker
```bash
docker-compose up -d
docker-compose logs -f
# Access dashboard at http://localhost:5000
```

### Monitor Live Race
```bash
# Terminal 1: Start live monitor
python live_session_monitor.py 2026 "Monaco Grand Prix" R 30

# Terminal 2: Start dashboard
python status_api.py

# Browser: Open http://localhost:5000
```

### Manual Scheduling Test
```bash
# Process single session
python scheduler.py --mode single

# Run continuous (test mode)
python scheduler.py --mode continuous --buffer 0
```

### Check Pipeline Status
```bash
# Quick health check
curl http://localhost:5000/api/health | python -m json.tool

# Full status
curl http://localhost:5000/api/status | python -m json.tool

# View dashboard
# Open http://localhost:5000
```

---

## 🚀 Production Deployment Checklist

- [ ] Update `.env` with production credentials
- [ ] Set `API_DEBUG=false` in `.env`
- [ ] Test Snowflake connection
- [ ] Verify OpenWeatherMap API key
- [ ] Build Docker images: `docker-compose build`
- [ ] Start containers: `docker-compose up -d`
- [ ] Check health: `docker-compose ps`
- [ ] View logs: `docker-compose logs -f`
- [ ] Access dashboard: http://localhost:5000
- [ ] Verify first session processes correctly
- [ ] Set up monitoring/alerts (optional)

---

**Need Help?**
- Check logs in `logs/pipeline_*.log`
- View error summary: `curl http://localhost:5000/api/errors`
- Test individual components with commands above
- Verify `.env` configuration
