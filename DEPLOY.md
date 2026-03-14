# 🏎️ F1 Telemetry Pipeline - Production Deployment Guide

## ⚡ Quick Deploy (5 minutes)

```bash
# 1. Configure environment
cp env.example .env
nano .env  # Add your Snowflake credentials

# 2. Test everything
./test_production.sh  # Linux/Mac
# OR
.\test_production.ps1  # Windows

# 3. Deploy!
docker-compose up -d

# 4. Verify
docker exec f1-telemetry-scheduler python docker_validate.py
```

That's it! Your container is now running and will automatically process all F1 sessions.

---

## 📋 Detailed Deployment Steps

### Step 1: Prerequisites

**Required:**
- ✅ Docker and Docker Compose installed
- ✅ Snowflake account with F1DATA database
- ✅ 10GB+ free disk space
- ✅ Stable internet connection

**Optional but Recommended:**
- ✅ OpenWeatherMap API key (free tier)
- ✅ Email SMTP credentials for notifications

### Step 2: Configuration

**Copy and edit environment file:**
```bash
cp env.example .env
```

**Edit `.env` with your credentials:**
```plaintext
# REQUIRED - Get from your Snowflake account
SNOWFLAKE_ACCOUNT=xyz12345.us-east-1
SNOWFLAKE_USER=your_username
SNOWFLAKE_PASSWORD=your_secure_password

# OPTIONAL - Recommended
SNOWFLAKE_DATABASE=F1DATA
SNOWFLAKE_WAREHOUSE=COMPUTE_WH
SNOWFLAKE_ROLE=ACCOUNTADMIN
OPENWEATHER_API_KEY=your_api_key  # Get from openweathermap.org
TIMEZONE=UTC
```

### Step 3: Pre-Production Testing

**Run the test suite:**

**Linux/Mac:**
```bash
chmod +x test_production.sh
./test_production.sh
```

**Windows:**
```powershell
.\test_production.ps1
```

**Expected output:**
```
========================================================================
F1 PIPELINE - PRE-PRODUCTION TEST SUITE
========================================================================

[Test 1] Checking .env configuration...
✓ PASS: .env file exists
✓ PASS: Snowflake credentials configured

[Test 2] Checking schedule configuration...
✓ PASS: Schedule loaded (24 events)

[Test 3] Checking required files...
✓ PASS: All required files present

[Test 4] Building Docker image...
✓ PASS: Docker image built successfully

[Test 5] Starting container...
✓ PASS: Container started

[Test 6] Running container validation...
✓ PASS: All container validation checks passed

[Test 7] Testing FastF1 schedule loading...
✓ PASS: FastF1 schedule loaded (24 events)

[Test 8] Checking log file creation...
✓ PASS: Log files created

[Test 9] Checking health check functionality...
✓ PASS: Health check working

========================================================================
✓ ALL TESTS PASSED
========================================================================

Your F1 pipeline is ready for production!
```

### Step 4: Deploy to Production

```bash
docker-compose up -d
```

**Verify deployment:**
```bash
# Check container is running
docker ps | grep f1-telemetry

# Run validation
docker exec f1-telemetry-scheduler python docker_validate.py

# Watch logs for a minute
docker-compose logs -f
```

**You should see:**
```
========================================================================
F1 TELEMETRY SCHEDULER - AUTONOMOUS MODE
========================================================================

Container will:
  ✓ Monitor F1 schedule automatically (FastF1 EventSchedule)
  ✓ Sleep until sessions end + 10 minute buffer
  ✓ Check for telemetry availability every 5 minutes
  ✓ Process telemetry when available
  ✓ Load results to Snowflake
  ✓ Go back to sleep until next session

========================================================================
Initializing FastF1 cache and loading 2026 schedule...
========================================================================
✓ Loaded 24 events for 2026 season

Starting scheduler with 10-minute buffer...
========================================================================

[INFO] Starting F1 Session Scheduler (Smart Mode)
[INFO] Data buffer: 10 minutes after session
[INFO] Telemetry check interval: 5 minutes

======================================================================
Next session: Australian Grand Prix - FP1
Session ends: 2026-03-06 10:30 UTC
Will check at: 2026-03-06 10:40 UTC
Sleeping for: 1234.5 hours
Wake up time: 2026-03-06 10:25:00
======================================================================
```

### Step 5: Monitor First Race Weekend

**Best Practice:** Monitor the first race weekend manually to verify everything works.

**During Friday FP1 (first session of season):**

```bash
# Watch logs in real-time
docker-compose logs -f

# In separate terminal, check status
docker exec f1-telemetry-scheduler cat logs/health.json
```

**Expected timeline for FP1:**
```
10:30 - FP1 session starts
11:30 - FP1 session ends
11:40 - Container wakes up
11:40 - [CHECK] Verifying telemetry availability...
11:45 - [SUCCESS] Telemetry is ready!
11:45 - [Stage 1] Extracting telemetry data...
11:47 - [Stage 1.5] Classifying practice laps...
11:48 - [Stage 2] Loading to Snowflake...
11:50 - [Stage 3] Racing line analysis...
11:52 - [Stage 5] Aggregating for visualization...
11:53 - [Stage 6] Generating driver reports...
11:54 - [SUCCESS] Complete pipeline executed
11:54 - Going to sleep until FP2...
```

**Verify in Snowflake:**
```sql
-- Check data loaded
SELECT COUNT(*) FROM F1DATA.PUBLIC.LAPS WHERE EVENT = 'Australian Grand Prix' AND SESSION = 'Practice 1';

-- Check driver reports exist
SELECT * FROM F1DATA.PUBLIC.DRIVER_PERFORMANCE_REPORTS WHERE EVENT = 'Australian Grand Prix';
```

---

## 🎯 What Happens During Each Session

### Practice Sessions (FP1, FP2, FP3)
**Pipeline Stages:** 1, 1.5, 2, 3, 5, 6  
**Duration:** ~7 minutes  
**Output:**
- Telemetry CSVs (laps, corners, telemetry per driver)
- Lap classifications (qual runs vs race sims)
- Snowflake tables populated
- Racing line analysis
- Aggregated metrics
- Driver performance reports

### Qualifying
**Pipeline Stages:** 1, 2, 3, 5, 6 (skips 1.5 - no lap classification)  
**Duration:** ~8 minutes  
**Output:**
- Telemetry CSVs
- Snowflake tables populated
- Racing line analysis for quali laps
- Aggregated metrics
- Driver reports with quali analysis

### Race
**Pipeline Stages:** All 6 stages  
**Duration:** ~20 minutes (includes ML model training)  
**Output:**
- Full telemetry CSVs
- Snowflake tables populated
- Racing line analysis
- **Tire degradation ML model** (trained on race data)
- Aggregated metrics
- Comprehensive driver reports

---

## 📊 Monitoring Commands

### Health Check
```bash
# Simple health check
docker exec f1-telemetry-scheduler cat logs/health.json

# Pretty printed
docker exec f1-telemetry-scheduler python -c "
import json
with open('logs/health.json') as f:
    print(json.dumps(json.load(f), indent=2))
"
```

### Next Session
```bash
docker exec f1-telemetry-scheduler python -c "
from scheduler import F1SessionScheduler
s = F1SessionScheduler('schedule/schedule_2026.json')
n = s.get_next_session_check_time()
if n:
    print(f'Next: {n[\"event\"]} - {n[\"session\"]}')
    print(f'Ends: {n[\"end_time\"]}')
    print(f'Check: {n[\"check_time\"]}')
    print(f'In: {n[\"hours_until\"]:.1f} hours')
"
```

### Processed Sessions
```bash
docker exec f1-telemetry-scheduler python -c "
import json
with open('logs/processed_sessions.json') as f:
    sessions = json.load(f)
    print(f'Total processed: {len(sessions)}')
    for key in sorted(sessions.keys()):
        print(f'  ✓ {key}')
"
```

### Disk Usage
```bash
docker exec f1-telemetry-scheduler du -sh /app/telemetry_out /app/f1_cache /app/logs /app/models
```

---

## 🔧 Maintenance

### Update Credentials
```bash
# Edit .env file
nano .env

# Restart container to pick up changes
docker-compose restart
```

### Update Pipeline Code
```bash
# Pull latest code
git pull

# Rebuild and redeploy
docker-compose down
docker-compose build --no-cache
docker-compose up -d
```

### Clean Up Old Data
```bash
# Archive old season telemetry
tar -czf telemetry_2025.tar.gz telemetry_out/*2025*
rm telemetry_out/*2025*

# Clean old cache files (90+ days)
docker exec f1-telemetry-scheduler find f1_cache -name "*.ff1pkl" -mtime +90 -delete
```

### View Logs
```bash
# Real-time logs
docker-compose logs -f

# Last 100 lines
docker-compose logs --tail=100

# Specific date logs
docker exec f1-telemetry-scheduler cat logs/scheduler_20260306.log

# Pipeline execution logs
docker exec f1-telemetry-scheduler ls -lt logs/pipeline_executions/ | head -10
```

---

## 🚨 Troubleshooting

### Container won't start
```bash
# Check for errors
docker-compose logs

# Common issues:
# - Missing .env file → cp env.example .env
# - Invalid credentials → check SNOWFLAKE_* variables
# - Port conflict → change API_PORT in .env
```

### Pipeline fails
```bash
# View detailed logs
docker exec f1-telemetry-scheduler tail -100 logs/pipeline.log

# Check Snowflake connection
docker exec f1-telemetry-scheduler python -c "
import snowflake.connector, os
conn = snowflake.connector.connect(
    account=os.getenv('SNOWFLAKE_ACCOUNT'),
    user=os.getenv('SNOWFLAKE_USER'),
    password=os.getenv('SNOWFLAKE_PASSWORD')
)
print('✓ Snowflake OK')
"

# Check FastF1 API
docker exec f1-telemetry-scheduler python -c "
import fastf1
fastf1.Cache.enable_cache('f1_cache')
s = fastf1.get_event_schedule(2026)
print(f'✓ FastF1 OK: {len(s)} events')
"
```

### Telemetry not available
```bash
# This is normal - FastF1 data delayed 10-30 minutes
# Container automatically retries every 5 minutes

# Check current status
docker-compose logs --tail=20

# You'll see:
# [CHECK] Verifying telemetry availability...
# [WAIT] Telemetry not ready. Checking again in 5 minutes...
```

### Re-process a session
```bash
# Delete processed marker
docker exec f1-telemetry-scheduler python -c "
import json
with open('logs/processed_sessions.json') as f:
    data = json.load(f)
del data['Monaco Grand Prix_FP3']  # Change to your session
with open('logs/processed_sessions.json', 'w') as f:
    json.dump(data, f, indent=2)
"

# Run manually
docker exec f1-telemetry-scheduler python master_pipeline.py \
  --year 2026 --event "Monaco Grand Prix" --session-type FP3
```

---

## ✅ Success Indicators

**Your pipeline is working correctly if:**

✅ Container stays running (check with `docker ps`)  
✅ Health check shows "healthy" status  
✅ Logs show "Sleeping for X hours" between sessions  
✅ Each session automatically processed ~15-25 min after it ends  
✅ Snowflake tables populate with new data after each session  
✅ No repeated error messages in logs  
✅ Processed sessions count increases (check `logs/processed_sessions.json`)

---

## 📞 Support

**FastF1 Issues:**
- Documentation: https://theoehrly.github.io/Fast-F1/
- If telemetry unavailable >2 hours, check F1 API status

**Snowflake Issues:**
- Status page: https://status.snowflake.com/
- Check warehouse is running and not suspended

**Pipeline Issues:**
- Review logs: `docker-compose logs`
- Run validation: `docker exec f1-telemetry-scheduler python docker_validate.py`
- Check system resources: `docker stats f1-telemetry-scheduler`

---

## 🎉 You're Ready!

Your F1 Telemetry Pipeline is now **fully automated** and **production-ready** for the 2026 season!

The container will wake up for every session, process the data, and go back to sleep until the next one. No manual intervention needed!

**Total automation:** 120 sessions across 24 races, all processed automatically.

Enjoy your F1 data! 🏁
