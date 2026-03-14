# F1 Pipeline - Production Operations Quick Reference

## 🚀 Deployment

```bash
# Initial deployment
docker-compose build
docker-compose up -d
docker exec f1-telemetry-scheduler python docker_validate.py
```

## 📊 Monitoring

```bash
# Real-time logs
docker-compose logs -f

# Check status
docker exec f1-telemetry-scheduler cat logs/health.json

# Next scheduled session
docker exec f1-telemetry-scheduler python -c "
from scheduler import F1SessionScheduler
s = F1SessionScheduler('schedule/schedule_2026.json')
n = s.get_next_session_check_time()
print(f'{n[\"event\"]} {n[\"session\"]} at {n[\"check_time\"]}' if n else 'None')
"

# Processed sessions count
docker exec f1-telemetry-scheduler wc -l < logs/processed_sessions.json
```

## 🔧 Troubleshooting

```bash
# Container not running
docker-compose ps
docker-compose logs --tail=50

# Restart container
docker-compose restart

# Full rebuild
docker-compose down
docker-compose build --no-cache
docker-compose up -d

# Access shell
docker exec -it f1-telemetry-scheduler bash

# Test Snowflake connection
docker exec f1-telemetry-scheduler python -c "
import snowflake.connector, os
conn = snowflake.connector.connect(
    account=os.getenv('SNOWFLAKE_ACCOUNT'),
    user=os.getenv('SNOWFLAKE_USER'),
    password=os.getenv('SNOWFLAKE_PASSWORD')
)
print('✓ Snowflake connected')
conn.close()
"

# Test FastF1 API
docker exec f1-telemetry-scheduler python -c "
import fastf1
fastf1.Cache.enable_cache('f1_cache')
s = fastf1.get_event_schedule(2026)
print(f'✓ FastF1 OK: {len(s)} events')
"
```

## 🔄 Manual Operations

```bash
# Run specific session manually
docker exec f1-telemetry-scheduler python master_pipeline.py \
  --year 2026 --event "Monaco Grand Prix" --session-type FP3

# Force reprocess (delete processed marker)
docker exec f1-telemetry-scheduler rm logs/processed_sessions.json

# Check for pending sessions now
docker exec f1-telemetry-scheduler python scheduler.py --mode once
```

## 📁 Data Management

```bash
# Check disk usage
docker exec f1-telemetry-scheduler du -sh /app/telemetry_out /app/f1_cache /app/logs

# View telemetry files
docker exec f1-telemetry-scheduler ls -lh telemetry_out/*.csv | tail -20

# View latest logs
docker exec f1-telemetry-scheduler tail -50 logs/scheduler.log

# Backup telemetry data
docker cp f1-telemetry-scheduler:/app/telemetry_out ./backup_telemetry_$(date +%Y%m%d)

# Clean up old cache (if needed)
docker exec f1-telemetry-scheduler find f1_cache -name "*.ff1pkl" -mtime +90 -delete
```

## 📈 Performance

```bash
# View pipeline execution times
docker exec f1-telemetry-scheduler python -c "
import json, glob
for f in sorted(glob.glob('logs/run_summary_*.json'))[-5:]:
    with open(f) as file:
        data = json.load(file)
        print(f'{data[\"session\"]}: {data.get(\"duration_seconds\", \"N/A\")}s')
"

# Check memory usage
docker stats f1-telemetry-scheduler --no-stream

# Container resource limits (edit docker-compose.yml)
# Add under f1-scheduler service:
#   deploy:
#     resources:
#       limits:
#         cpus: '2'
#         memory: 4G
```

## 🔐 Security

```bash
# Rotate Snowflake credentials
# 1. Update .env file
# 2. Restart container
docker-compose restart

# View environment (sensitive!)
docker exec f1-telemetry-scheduler env | grep SNOWFLAKE

# Check file permissions
docker exec f1-telemetry-scheduler ls -la /app/logs
```

## 📅 Race Weekend Checklist

### Before Race Weekend
- [ ] Container running: `docker ps | grep f1-telemetry`
- [ ] Health check passing: `cat logs/health.json`
- [ ] Disk space >10GB: `df -h`
- [ ] Snowflake connection OK
- [ ] FastF1 API accessible

### During Race Weekend
- [ ] FP1 processed successfully
- [ ] FP2 processed successfully
- [ ] FP3 processed successfully
- [ ] Qualifying processed successfully
- [ ] Race processed successfully

### After Race Weekend
- [ ] Verify all data in Snowflake
- [ ] Check driver reports generated
- [ ] Review any error logs
- [ ] Container sleeping until next event

## 🚨 Common Issues

### Telemetry not available
```bash
# Check FastF1 API status
docker exec f1-telemetry-scheduler python -c "
import fastf1
session = fastf1.get_session(2026, 'Monaco', 'FP3')
session.load()
print(f'Laps: {len(session.laps)}')
"
# If empty, telemetry not yet available from F1
# Container will retry every 5 minutes automatically
```

### Pipeline fails on Race but works on Practice
```bash
# Race pipelines include tire degradation ML (Stage 4)
# Check logs for sklearn errors
docker exec f1-telemetry-scheduler tail -100 logs/pipeline.log | grep -i error
```

### Container stopped unexpectedly
```bash
# Check exit code
docker ps -a | grep f1-telemetry
# View recent logs
docker logs --tail=100 f1-telemetry-scheduler
# Restart
docker-compose up -d
```

### Snowflake connection timeout
```bash
# Check network
docker exec f1-telemetry-scheduler ping -c 3 google.com
# Verify credentials
docker exec f1-telemetry-scheduler env | grep SNOWFLAKE_ACCOUNT
# Test connection manually
docker exec f1-telemetry-scheduler python test_config.py
```

## 📞 Support Contacts

- FastF1 API: https://theoehrly.github.io/Fast-F1/
- Snowflake Status: https://status.snowflake.com/
- F1 Schedule: https://www.formula1.com/en/racing/2026.html

## 🔗 Useful Links

- Container Logs: `./logs/`
- Telemetry Data: `./telemetry_out/`
- FastF1 Cache: `./f1_cache/`
- ML Models: `./models/`
- Driver Reports: `./driver_reports/`
