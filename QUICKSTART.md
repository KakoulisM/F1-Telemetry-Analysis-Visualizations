# F1 Telemetry Scheduler - Quick Start

## Automated Container Deployment

### One Command to Rule Them All

```bash
# 1. Create .env with your Snowflake password
echo "SNOWFLAKE_PASSWORD=your_password_here" > .env

# 2. Start the container (runs automatically forever)
docker-compose up -d

# Done! Container is now running autonomously
```

### What Happens Automatically

1. **Container Starts** → Scheduler launches in smart mode
2. **Reads F1 Schedule** → Calculates next session end time
3. **Sleeps Intelligently** → Waits until session ends + 2 hours
4. **Wakes Up** → Checks if data is available
5. **Runs Pipeline** → Extracts telemetry, loads to Snowflake
6. **Repeats** → Goes back to sleep until next session

### Verify It's Working

```bash
# Check container status (should show "healthy")
docker-compose ps

# View real-time logs
docker-compose logs -f

# Check health status
docker exec f1-telemetry-scheduler cat /app/logs/health.json

# See what's scheduled next
docker-compose logs | grep "Next session"
```

### Container Features

✅ **Auto-Start** - Runs immediately when container starts  
✅ **Auto-Restart** - Restarts on crash/reboot  
✅ **Health Checks** - Monitors scheduler every 5 minutes  
✅ **Smart Sleep** - Only active around F1 sessions  
✅ **Persistent Data** - Logs/cache/data saved to host  
✅ **Zero Maintenance** - Set it and forget it  

### Health Check Logic

Every 5 minutes, Docker checks:
- ✅ Is `health.json` updated in last 6 minutes?
- ✅ Is scheduler process running?
- ❌ If fails 3 times → Container restarts automatically

### Manual Controls

```bash
# Stop the scheduler
docker-compose stop

# Start it again
docker-compose start

# Restart (applies config changes)
docker-compose restart

# View all logs
docker-compose logs

# Trigger pipeline manually
docker exec f1-telemetry-scheduler python fetch_pipeline.py

# Access container shell
docker exec -it f1-telemetry-scheduler bash
```

### Update Schedule

```bash
# Edit schedule file on host
nano schedule/schedule_2026.json

# Restart container to reload
docker-compose restart
```

### Monitoring

```bash
# Watch logs in real-time
docker-compose logs -f

# Check resource usage
docker stats f1-telemetry-scheduler

# View health status
docker inspect f1-telemetry-scheduler --format='{{.State.Health.Status}}'
```

### Troubleshooting

**Container exits immediately:**
```bash
# Check logs for error
docker-compose logs

# Verify .env file
cat .env

# Test Snowflake connection
docker-compose run f1-scheduler python -c "from snowflake_config import SNOWFLAKE_CONFIG; print(SNOWFLAKE_CONFIG)"
```

**Unhealthy status:**
```bash
# View health check logs
docker inspect f1-telemetry-scheduler | grep -A 10 Health

# Restart container
docker-compose restart
```

**No data being processed:**
```bash
# Check if sessions are scheduled
docker-compose logs | grep "Next session"

# Verify processed sessions
docker exec f1-telemetry-scheduler cat logs/processed_sessions.json

# Manually trigger check
docker exec f1-telemetry-scheduler python scheduler.py --mode once
```

### Production Deployment

Deploy to any cloud:

```bash
# AWS ECS, Azure Container Instances, Google Cloud Run, etc.
# Just needs:
# - Snowflake credentials in environment
# - Persistent volumes for logs/cache
# - restart policy: always

docker-compose up -d
```

That's it! Fully autonomous F1 telemetry pipeline in a container.
