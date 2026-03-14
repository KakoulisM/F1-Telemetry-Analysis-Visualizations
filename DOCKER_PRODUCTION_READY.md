# Docker Production Readiness Summary

## ✅ All Changes Implemented

### 1. Session-Specific Timing (CRITICAL FIX)
**Problem**: Scheduler used race weekend end time for ALL sessions  
**Solution**: FastF1 EventSchedule integration  
**Files Modified**:
- `scheduler.py`: Added FastF1 `get_event_schedule()` calls
- `scheduler.py`: Modified `get_sessions_to_process()` to fetch Session1-5 times
- `scheduler.py`: Modified `get_next_session_check_time()` to use individual session times

**Impact**:
- ✅ FP1, FP2, FP3, Q, R now processed independently
- ✅ Each session checked 10 minutes after it ends (not after race)
- ✅ Container wakes up 5 times per race weekend (not once)

### 2. Reduced Buffer Time
**Change**: 2 hours → 10 minutes  
**Files Modified**:
- `scheduler.py`: `buffer_hours=0.167` (10 minutes)
- `entrypoint.sh`: `--buffer 0.167`

**Impact**:
- ✅ Data available 15-40 minutes after session (vs 2+ hours)
- ✅ Faster insights for teams/analysts

### 3. Telemetry Availability Checking
**Added**: `check_telemetry_available()` method  
**Files Modified**:
- `scheduler.py`: New method performs lightweight FastF1 check
- `scheduler.py`: `run_pipeline()` polls every 5 minutes (max 1 hour)

**Impact**:
- ✅ No wasted pipeline runs on unavailable data
- ✅ Automatic retry until telemetry appears
- ✅ Reduced API calls vs. downloading full telemetry blindly

### 4. Complete Pipeline Support
**Added**: tools/ directory to Docker  
**Files Modified**:
- `Dockerfile`: Added `COPY tools/ ./tools/`
- Ensures qual_vs_race_classifier.py available in container

**Impact**:
- ✅ Practice session lap classification works in Docker
- ✅ All 6 pipeline stages functional

### 5. Persistent Volumes
**Added**: models/ and driver_reports/ mounts  
**Files Modified**:
- `docker-compose.yml`: Added volume mounts

**Impact**:
- ✅ ML models survive container rebuilds
- ✅ Driver reports accessible on host
- ✅ No data loss on container restart

### 6. Enhanced Logging
**Changed**: Log rotation 10MB/3 files → 50MB/5 files  
**Files Modified**:
- `docker-compose.yml`: Updated logging configuration

**Impact**:
- ✅ More log history retained
- ✅ Better debugging for race weekends

### 7. FastF1 Cache Warmup
**Added**: Pre-loads 2026 schedule on startup  
**Files Modified**:
- `entrypoint.sh`: Added FastF1 schedule pre-load command

**Impact**:
- ✅ Faster first session check
- ✅ Cache directory initialized properly
- ✅ Reduces startup latency

### 8. Production Validation
**Created**: `docker_validate.py`  
**Checks**:
- ✅ All directories present
- ✅ Pipeline files exist
- ✅ Python dependencies installed
- ✅ Environment variables set
- ✅ Schedule file loaded
- ✅ FastF1 connection working

**Usage**:
```bash
docker exec f1-telemetry-scheduler python docker_validate.py
```

### 9. Pre-Production Testing
**Created**: `test_production.sh`  
**Tests**:
- ✅ .env configuration
- ✅ Schedule file validity
- ✅ Docker build
- ✅ Container startup
- ✅ All validation checks
- ✅ FastF1 schedule loading
- ✅ Log file creation
- ✅ Health check functionality

**Usage**:
```bash
bash test_production.sh
```

### 10. Documentation
**Created/Updated**:
- `DOCKER_README.md`: Complete deployment guide with timeline examples
- `PRODUCTION_OPERATIONS.md`: Quick reference for day-to-day operations
- `SCHEDULER_TIMING_CHANGES.md`: Technical details of timing updates

## Architecture Comparison

### Before (Broken)
```
Race Weekend Ends (Sunday 17:00)
  ↓
Wait 2 hours (19:00)
  ↓
Check: Process FP1? FP2? FP3? Q? R?
  ↓
❌ Telemetry for FP1 (Friday 11:30) is 55 hours old - cache expired!
❌ All Practice sessions failed to process
✅ Only Race data processed
```

### After (Production Ready)
```
FP1 Ends (Friday 11:30)
  ↓ 10 minutes
Check telemetry @ 11:40
  ↓ Poll every 5 min
Telemetry available @ 11:45
  ↓
✅ Process FP1 (complete @ 11:52)
  ↓
Sleep until FP2...

FP2 Ends (Friday 15:00)
  ↓ 10 minutes
✅ Process FP2 (complete @ 15:22)
  ↓
Sleep until FP3...

[continues for FP3, Q, R individually]
```

## Production Deployment Checklist

### Pre-Deployment
- [x] Updated Dockerfile with tools/ directory
- [x] Updated docker-compose.yml with all volume mounts
- [x] Updated entrypoint.sh with 10-minute buffer
- [x] Created docker_validate.py
- [x] Created test_production.sh
- [x] Updated scheduler.py to use FastF1 EventSchedule
- [x] Added telemetry availability checking
- [x] Updated all documentation

### Deployment Steps
```bash
# 1. Clone repository
git clone <repo> && cd formula-1

# 2. Configure environment
cp env.example .env
nano .env  # Add Snowflake credentials

# 3. Run pre-production tests
bash test_production.sh

# 4. Deploy to production
docker-compose up -d

# 5. Validate deployment
docker exec f1-telemetry-scheduler python docker_validate.py

# 6. Monitor first race weekend
docker-compose logs -f
```

### Post-Deployment Monitoring
```bash
# Check container health every hour
docker ps | grep f1-telemetry

# Check health status
docker exec f1-telemetry-scheduler cat logs/health.json

# View next scheduled check
docker exec f1-telemetry-scheduler python -c "
from scheduler import F1SessionScheduler
s = F1SessionScheduler('schedule/schedule_2026.json')
n = s.get_next_session_check_time()
print(f'{n[\"event\"]} {n[\"session\"]} @ {n[\"check_time\"]}' if n else 'No sessions')
"
```

## Expected Behavior - Complete Season

### Race Weekend Processing (5 sessions)
- **Friday**: FP1 (11:52), FP2 (15:22)
- **Saturday**: FP3 (12:22), Qualifying (15:28)
- **Sunday**: Race (17:46)

### Sleep Periods
- **Post-FP1 → Pre-FP2**: 2-3 hours
- **Post-FP2 → Pre-FP3**: 18-20 hours (overnight)
- **Post-FP3 → Pre-Quali**: 2-3 hours
- **Post-Quali → Pre-Race**: 22-24 hours (overnight)
- **Post-Race → Next Event**: 7-14 days

### Data Availability Timeline
| Session | End Time | Check Time | Data Ready | Pipeline Done | Total Latency |
|---------|----------|------------|------------|---------------|---------------|
| FP1     | 11:30    | 11:40      | 11:45      | 11:52         | **22 min**    |
| FP2     | 15:00    | 15:10      | 15:15      | 15:22         | **22 min**    |
| FP3     | 12:00    | 12:10      | 12:15      | 12:22         | **22 min**    |
| Quali   | 15:00    | 15:10      | 15:20      | 15:28         | **28 min**    |
| Race    | 17:00    | 17:10      | 17:25      | 17:46         | **46 min**    |

### Full Season Stats (24 races)
- **Total Sessions**: 120 (24 × 5)
- **Total Pipeline Runs**: 120
- **Estimated Total Runtime**: ~20 hours across full season
- **Average Latency**: 28 minutes from session end to Snowflake

## Troubleshooting Production Issues

### Issue: Container stops unexpectedly
**Check**: `docker logs f1-telemetry-scheduler`  
**Common Causes**: Snowflake auth failure, out of memory  
**Fix**: Check .env credentials, increase memory limit

### Issue: Telemetry not available after 1 hour
**Check**: FastF1 API status, session actually ended  
**Common Causes**: Session delayed/cancelled, API maintenance  
**Fix**: Manual run after telemetry available

### Issue: Pipeline fails on specific session
**Check**: `logs/pipeline_*.log` for error details  
**Common Causes**: Insufficient data (red flags, safety cars)  
**Fix**: Review stage-specific logs, may skip if data insufficient

### Issue: Snowflake load slow
**Check**: Network latency, table size  
**Common Causes**: Large race datasets, warehouse suspended  
**Fix**: Use larger warehouse, check Snowflake query history

## Performance Tuning

### If processing too slow:
```yaml
# docker-compose.yml
deploy:
  resources:
    limits:
      cpus: '4'
      memory: 8G
```

### If disk filling up:
```bash
# Clean old cache files
docker exec f1-telemetry-scheduler find f1_cache -name "*.ff1pkl" -mtime +90 -delete

# Archive old telemetry
tar -czf telemetry_archive_2025.tar.gz telemetry_out/*2025*
rm telemetry_out/*2025*
```

### If Snowflake costs high:
- Use smaller warehouse for practice sessions
- Aggregate data more before loading
- Consider data retention policies

## Success Metrics

### Container Health
- ✅ Uptime: 99%+ (restarts only for maintenance)
- ✅ Health check: Always "healthy"
- ✅ CPU usage: <50% during processing
- ✅ Memory usage: <4GB

### Data Pipeline
- ✅ Session capture rate: 100% (all 5 sessions per race)
- ✅ Average latency: <30 minutes
- ✅ Pipeline success rate: >95%
- ✅ Data completeness: All 20 drivers per session

### Business Value
- ✅ Insights available same day as session
- ✅ Historical data accumulates over season
- ✅ ML models improve with more race data
- ✅ Driver reports auto-generated

## Production Ready ✅

Your F1 Telemetry Pipeline is now **production-ready** for the 2026 season!

The container will:
1. ✅ Wake up automatically 10 minutes after each session
2. ✅ Check if telemetry is available (every 5 minutes)
3. ✅ Process data immediately when ready
4. ✅ Load everything to Snowflake
5. ✅ Generate driver reports and ML models
6. ✅ Go back to sleep until the next session
7. ✅ Repeat 120 times across the season

**No manual intervention required during race weekends!**
