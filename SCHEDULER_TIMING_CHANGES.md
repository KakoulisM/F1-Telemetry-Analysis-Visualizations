# Scheduler Timing Changes

## Overview
Updated the F1 Session Scheduler to check for telemetry availability more aggressively, reducing pipeline execution latency from 2+ hours to 10-20 minutes.

## Changes Made

### 1. Reduced Buffer Time
- **Before**: 2 hours after session end
- **After**: 10 minutes after session end
- **Rationale**: FastF1 API typically makes data available within 10-30 minutes, not 2 hours

### 2. Added Telemetry Availability Checking
- **New Method**: `check_telemetry_available(year, event_name, session_type)`
  - Performs lightweight FastF1 session load
  - Checks if lap data exists without downloading full telemetry
  - Returns True/False based on data availability

### 3. Polling Mechanism
- **Check Interval**: Every 5 minutes
- **Maximum Checks**: 12 attempts (1 hour total)
- **Process**:
  1. Wait 10 minutes after session end
  2. Check telemetry availability
  3. If not available, wait 5 minutes and check again
  4. Repeat up to 12 times (1 hour)
  5. Once available, execute pipeline immediately

### 4. Retry Intervals
- **Before**: Exponential backoff (5, 10, 15 minutes)
- **After**: Fixed 5-minute intervals
- **Applies to**:
  - Pipeline execution retries
  - Telemetry check retries
  - Exception handling retries

### 5. Updated CLI Arguments
```bash
python scheduler.py --mode continuous \
  --buffer 0.167 \              # 10 minutes (default)
  --check-interval 5 \          # 5 minutes (default)
  --schedule schedule/schedule_2026.json
```

## Expected Behavior

### Practice Session (FP1/FP2/FP3)
1. Session ends at 14:00
2. Scheduler detects session should be processed at 14:10
3. Checks telemetry availability at 14:10, 14:15, 14:20... until available
4. Telemetry typically available around 14:15-14:25
5. Pipeline executes immediately once data detected
6. **Total Latency**: 15-25 minutes from session end

### Qualifying Session
1. Session ends at 16:00
2. Scheduler checks at 16:10, 16:15, 16:20...
3. Telemetry available ~16:20
4. Pipeline runs with all 6 stages
5. **Total Latency**: 20-30 minutes from session end

### Race Session
1. Session ends at 17:00
2. Scheduler checks at 17:10, 17:15, 17:20...
3. Race telemetry may take longer (more data)
4. Telemetry available ~17:25-17:35
5. Pipeline executes full race analysis
6. **Total Latency**: 25-40 minutes from session end

## Code Locations

### Modified Files
- `scheduler.py`:
  - Line 11: `buffer_hours=0.167` (10 minutes)
  - Line 12: `check_interval_minutes=5`
  - Lines 145-184: `check_telemetry_available()` method
  - Lines 149-191: Telemetry polling loop in `run_pipeline()`
  - Lines 342-344: Updated argparse defaults

### Key Parameters
```python
# Default initialization
F1SessionScheduler(
    buffer_hours=0.167,           # 10 minutes
    check_interval_minutes=5       # 5-minute checks
)

# Telemetry checking
max_telemetry_checks = 12          # Up to 1 hour of checks
```

## Testing Recommendations

1. **Test with Recent Session**:
   ```bash
   python scheduler.py --mode once --buffer 0 --check-interval 1
   ```
   - Forces immediate check with 1-minute intervals
   - Use for testing with past sessions

2. **Monitor Logs**:
   ```bash
   tail -f logs/scheduler.log
   ```
   - Watch for "[CHECK] Verifying telemetry availability..."
   - Look for successful data detection
   - Verify pipeline execution timing

3. **Docker Testing**:
   ```bash
   docker-compose up -d
   docker logs -f f1-scheduler
   ```
   - Test in containerized environment
   - Verify cron scheduling works with new timing

## Performance Impact

### Benefits
- **Faster Data Processing**: 15-40 minutes vs 2+ hours
- **More Responsive**: Catches data as soon as available
- **Better Resource Usage**: No long idle waits

### Considerations
- **Increased API Calls**: Checks every 5 minutes (12 max per session)
- **FastF1 Rate Limits**: Lightweight checks minimize impact
- **Failed Session Handling**: Still retries 3 times with 5-minute intervals

## Monitoring

Watch for these log messages:
- `[CHECK] Verifying telemetry availability for...` - Starting check
- `[SUCCESS] Telemetry is ready!` - Data detected
- `[WAIT] Telemetry not ready. Checking again in 5 minutes...` - Still waiting
- `[ERROR] Telemetry not available after 60 minutes` - Max retries reached
- `[SUCCESS] Complete pipeline executed for...` - Pipeline completed

## Rollback Instructions

If needed, revert to previous behavior:
```bash
python scheduler.py --mode continuous \
  --buffer 2 \                  # 2 hours
  --check-interval 15           # 15 minutes
```

Or modify defaults in `scheduler.py`:
```python
def __init__(self, schedule_file, buffer_hours=2, check_interval_minutes=15):
```
