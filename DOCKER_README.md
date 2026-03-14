# Running F1 Pipeline in Docker Container

## Quick Start - Production Deployment

### 1. Pre-Deployment Checklist
- ✅ Snowflake account with F1DATA database created
- ✅ OpenWeatherMap API key (optional, but recommended)
- ✅ Docker and Docker Compose installed
- ✅ Host machine has stable internet connection
- ✅ At least 10GB free disk space for telemetry cache

### 2. Configure Environment
```bash
# Copy example env file
cp env.example .env

# Edit .env with your Snowflake credentials
nano .env
```

**Required variables in `.env`:**
```plaintext
SNOWFLAKE_ACCOUNT=your_account_here
SNOWFLAKE_USER=your_username_here
SNOWFLAKE_PASSWORD=your_password_here
SNOWFLAKE_DATABASE=F1DATA
SNOWFLAKE_WAREHOUSE=COMPUTE_WH
SNOWFLAKE_ROLE=ACCOUNTADMIN
```

### 3. Build and Deploy
```bash
# Build the container
docker-compose build

# Start the scheduler (runs in background)
docker-compose up -d

# Validate deployment
docker exec f1-telemetry-scheduler python docker_validate.py
```

**Expected output:**
```
======================================================================
F1 TELEMETRY PIPELINE - DOCKER CONTAINER VALIDATION
======================================================================

[Directories]
✓ All required directories present

[Pipeline Files]
✓ All 8 pipeline scripts present

[Dependencies]
✓ All 7 required packages installed

[Environment]
✓ Snowflake credentials configured
✓ Weather API key configured

[Schedule Config]
✓ Schedule loaded: 24 events for 2026 season
✓ First event: Australian Grand Prix (2026-03-08T12:59:00+00:00)

[FastF1 Integration]
✓ FastF1 loaded: 24 events
✓ Cache enabled at: f1_cache/
✓ Session timing columns present

======================================================================
✓ ALL CHECKS PASSED (6/6)
Container is ready for production!
======================================================================
```

### 4. Monitor First Run
```bash
# Watch container logs in real-time
docker-compose logs -f f1-scheduler

# Check scheduler status
docker exec f1-telemetry-scheduler cat logs/health.json
```

## What Changed for Containers?

### ✅ Automated Session Detection
- **Previous**: 2-hour buffer after race weekend ends
- **Current**: FastF1 EventSchedule fetches actual session times
- **Behavior**: 
  - FP1, FP2, FP3, Qualifying, and Race processed individually
  - Checks 10 minutes after each session ends
  - Polls for telemetry every 5 minutes until available

### ✅ Scheduling Mechanism
- **Windows**: Task Scheduler (`.ps1` script)
- **Docker**: Continuous monitoring with smart sleep
- **Process**: Container wakes for each session, processes data, then sleeps until next session

### ✅ Configuration
- **Windows**: `snowflake_config.py` with hardcoded credentials
- **Docker**: Environment variables from `.env` file
- **Docker Advantage**: No code changes needed for credential updates

### ✅ File Persistence
- **Windows**: Local filesystem
- **Docker**: Volumes mounted to host (survives container rebuilds)
- **Persistent Volumes**:
  - `logs/` - Scheduler and pipeline execution logs
  - `telemetry_out/` - Extracted telemetry CSVs
  - `f1_cache/` - FastF1 cache (speeds up repeated queries)
  - `models/` - Trained ML models (tire degradation)
  - `driver_reports/` - Generated driver performance reports

### ✅ Restart Behavior
- **Windows**: Task Scheduler manages restarts
- **Docker**: `restart: unless-stopped` policy (auto-restart on crash/reboot)

## Container Architecture

```
Container Lifecycle:
├── Startup
│   ├── Load 2026 F1 schedule from FastF1
│   ├── Warm up cache with event schedule
│   └── Initialize health check (logs/health.json)
│
├── Continuous Monitoring Loop
│   ├── Check FastF1 for session end times
│   │   ├── FP1 ended? → Check telemetry at end + 10 min
│   │   ├── FP2 ended? → Check telemetry at end + 10 min
│   │   ├── FP3 ended? → Check telemetry at end + 10 min
│   │   ├── Qualifying ended? → Check telemetry at end + 10 min
│   │   └── Race ended? → Check telemetry at end + 10 min
│   │
│   ├── Telemetry Available?
│   │   ├── YES → Run pipeline immediately
│   │   └── NO → Wait 5 minutes, check again (max 12 attempts)
│   │
│   ├── Pipeline Execution
│   │   ├── Stage 1: Fetch telemetry (fetch_pipeline.py)
│   │   ├── Stage 1.5: Classify practice laps (qual_vs_race_classifier.py)
│   │   ├── Stage 2: Load to Snowflake (master_pipeline.py)
│   │   ├── Stage 3: Racing line analysis (racing_line_analyzer.py)
│   │   ├── Stage 4: Tire degradation ML (tire_degradation_model.py - Race only)
│   │   ├── Stage 5: Aggregate for viz (aggregate_for_viz.py)
│   │   └── Stage 6: Driver reports (cross_session_analytics.py)
│   │
│   └── Sleep Until Next Session
│       └── Calculate next session time, sleep dynamically
│
└── Volumes (Persistent Storage)
    ├── /app/logs → ./logs
    ├── /app/telemetry_out → ./telemetry_out
    ├── /app/f1_cache → ./f1_cache
    ├── /app/models → ./models
    └── /app/driver_reports → ./driver_reports
```

## Production Timeline Example

### Friday - Free Practice Sessions

**FP1 (10:30-11:30 local time)**
```
10:30 → Session starts
11:30 → Session ends
11:40 → Container wakes up (10 min buffer)
11:40 → Checks telemetry availability
11:45 → Telemetry ready! Starts pipeline
11:47 → Stage 1: Telemetry extracted (2 min)
11:48 → Stage 1.5: Practice laps classified (1 min)
11:50 → Stage 2: Loaded to Snowflake (2 min)
11:52 → Stage 3: Racing line analysis (2 min)
11:53 → Stage 5: Aggregations for viz (1 min)
11:54 → Stage 6: Driver reports generated (1 min)
11:54 → Pipeline complete! Goes to sleep until FP2
```

**FP2 (14:00-15:00 local time)**
```
15:00 → Session ends
15:10 → Container wakes up
15:15 → Telemetry ready, pipeline runs
15:22 → Complete (7-8 min pipeline)
15:22 → Sleep until Saturday FP3
```

### Saturday - Free Practice + Qualifying

**FP3 (11:00-12:00 local time)**
```
12:00 → Session ends
12:10 → Container wakes
12:15 → Pipeline runs
12:22 → Complete, sleep until Qualifying
```

**Qualifying (14:00-15:00 local time)**
```
15:00 → Session ends
15:10 → Container wakes
15:20 → Telemetry ready (quali takes slightly longer)
15:28 → Complete (8 min - more drivers, more laps)
15:28 → Sleep until Sunday Race
```

### Sunday - Race

**Race (15:00-17:00 local time)**
```
17:00 → Race ends
17:10 → Container wakes
17:25 → Telemetry ready (race data is larger)
17:27 → Stage 1: Telemetry extracted (2 min)
17:28 → Stage 2: Loaded to Snowflake (1 min)
17:32 → Stage 3: Racing line analysis (4 min - full race distance)
17:42 → Stage 4: Tire degradation ML training (10 min - most intensive)
17:44 → Stage 5: Aggregations (2 min)
17:46 → Stage 6: Driver reports (2 min)
17:46 → Race weekend complete! Sleep until next event (7-14 days)
```

**Total Processing Times:**
- Practice Sessions: 7-8 minutes
- Qualifying: 8-10 minutes
- Race: 20-25 minutes

**Latency (Session End → Data Available in Snowflake):**
- Practice: 15-23 minutes
- Qualifying: 20-30 minutes
- Race: 35-46 minutes

## Management Commands

### Start/Stop
```bash
# Start container
docker-compose up -d

# Stop container
docker-compose down

# Restart container
docker-compose restart
```

### Logs
```bash
# View container logs
docker-compose logs -f

# View scheduler logs
docker exec f1-telemetry-scheduler tail -f /app/logs/scheduler_$(date +%Y%m%d).log

# View pipeline logs
docker exec f1-telemetry-scheduler tail -f /app/logs/pipeline_$(date +%Y%m%d).log
```

### Manual Execution
```bash
# Run pipeline for specific session manually
docker exec f1-telemetry-scheduler python master_pipeline.py --year 2026 --event "Monaco Grand Prix" --session-type FP3

# Check for pending sessions
docker exec f1-telemetry-scheduler python scheduler.py --mode once

# Force re-process a session (deletes processed marker)
docker exec f1-telemetry-scheduler rm logs/processed_sessions.json
```

### Production Monitoring
```bash
# Check next scheduled session
docker exec f1-telemetry-scheduler python -c "
import json, sys
sys.path.insert(0, '/app')
from scheduler import F1SessionScheduler
s = F1SessionScheduler('schedule/schedule_2026.json')
next_check = s.get_next_session_check_time()
if next_check:
    print(f\"Next: {next_check['event']} - {next_check['session']}\")
    print(f\"Check at: {next_check['check_time']}\")
else:
    print('No upcoming sessions')
"

# View health status
docker exec f1-telemetry-scheduler cat logs/health.json | python -m json.tool

# Count processed sessions
docker exec f1-telemetry-scheduler python -c "
import json
with open('logs/processed_sessions.json') as f:
    data = json.load(f)
    print(f'Processed {len(data)} sessions')
    for session, info in sorted(data.items(), key=lambda x: x[1]['processed_at'])[-5:]:
        print(f\"  - {session}: {info['processed_at']}\")
"

# Check disk usage
docker exec f1-telemetry-scheduler du -sh /app/telemetry_out /app/f1_cache /app/logs
```

### Debugging
```bash
# Access container shell
docker exec -it f1-telemetry-scheduler bash

# Test FastF1 connection
docker exec f1-telemetry-scheduler python -c "
import fastf1
fastf1.Cache.enable_cache('f1_cache')
schedule = fastf1.get_event_schedule(2026)
print(f'FastF1 OK: {len(schedule)} events loaded')
"

# Test Snowflake connection
docker exec f1-telemetry-scheduler python -c "
import snowflake.connector
import os
conn = snowflake.connector.connect(
    account=os.getenv('SNOWFLAKE_ACCOUNT'),
    user=os.getenv('SNOWFLAKE_USER'),
    password=os.getenv('SNOWFLAKE_PASSWORD')
)
print('Snowflake OK: Connected')
conn.close()
"

# Validate entire setup
docker exec f1-telemetry-scheduler python docker_validate.py
```

## Configuration Changes

### Update Snowflake Credentials
```bash
# Edit .env file
nano .env

# Restart container to apply
docker-compose restart
```

### Change Schedule Interval
Edit `Dockerfile` cron line:
```dockerfile
# Every 30 minutes (default)
RUN echo "*/30 * * * * cd /app && /usr/local/bin/python scheduler.py --mode once >> /app/logs/cron.log 2>&1" > /etc/cron.d/f1-scheduler

# Every 15 minutes
RUN echo "*/15 * * * * cd /app && /usr/local/bin/python scheduler.py --mode once >> /app/logs/cron.log 2>&1" > /etc/cron.d/f1-scheduler

# Every hour
RUN echo "0 * * * * cd /app && /usr/local/bin/python scheduler.py --mode once >> /app/logs/cron.log 2>&1" > /etc/cron.d/f1-scheduler
```

Rebuild: `docker-compose up -d --build`

## Deployment Options

### Local Development
```bash
docker-compose up
```

### Production Server
```bash
# Run detached
docker-compose up -d

# Enable auto-restart on boot
docker update --restart=always f1-telemetry-scheduler
```

### Cloud Deployment

**AWS ECS/Fargate:**
- Use task definition with cron schedule
- Mount EFS for persistent storage

**Azure Container Instances:**
- Use container groups
- Azure Files for volume storage

**Google Cloud Run:**
- Use Cloud Scheduler to trigger
- Cloud Storage for persistence

**Kubernetes:**
```yaml
apiVersion: batch/v1
kind: CronJob
metadata:
  name: f1-scheduler
spec:
  schedule: "*/30 * * * *"
  jobTemplate:
    spec:
      template:
        spec:
          containers:
          - name: f1-pipeline
            image: your-registry/f1-telemetry:latest
            env:
            - name: SNOWFLAKE_PASSWORD
              valueFrom:
                secretKeyRef:
                  name: snowflake-creds
                  key: password
          restartPolicy: OnFailure
```

## Volume Management

### Backup Data
```bash
# Backup all data
tar -czf f1-backup-$(date +%Y%m%d).tar.gz logs/ telemetry_out/ f1_cache/
```

### Clean Old Data
```bash
# Remove old logs (keep last 30 days)
find logs/ -name "*.log" -mtime +30 -delete

# Clear cache
rm -rf f1_cache/*
```

## Troubleshooting

**Container won't start:**
```bash
# Check logs
docker-compose logs

# Verify .env file
cat .env

# Test Snowflake connection
docker exec f1-telemetry-scheduler python -c "from snowflake_config_docker import SNOWFLAKE_CONFIG; print(SNOWFLAKE_CONFIG)"
```

**Cron not running:**
```bash
# Check cron service
docker exec f1-telemetry-scheduler service cron status

# Verify crontab
docker exec f1-telemetry-scheduler crontab -l

# Test scheduler manually
docker exec f1-telemetry-scheduler python scheduler.py --mode once
```

**Permission issues:**
```bash
# Fix volume permissions
sudo chown -R $USER:$USER logs/ telemetry_out/ f1_cache/
```

## Migration from Windows

If migrating from Windows Task Scheduler:

1. ✅ Copy `logs/processed_sessions.json` to container
2. ✅ Ensure `.env` has correct Snowflake credentials
3. ✅ Stop Windows Task Scheduler task
4. ✅ Start Docker container
5. ✅ Verify logs show scheduler running

## Performance

**Resource Limits** (add to docker-compose.yml):
```yaml
services:
  f1-scheduler:
    deploy:
      resources:
        limits:
          cpus: '2'
          memory: 4G
        reservations:
          cpus: '1'
          memory: 2G
```

**Monitoring:**
```bash
# Check resource usage
docker stats f1-telemetry-scheduler
```

## Next Steps

- Set up monitoring/alerting (Prometheus, Grafana)
- Add health check endpoint
- Implement notification webhooks (email/Slack)
- Deploy to cloud for 24/7 operation
