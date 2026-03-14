# F1 Testing Sessions Integration Guide

## Overview
Your pipeline now supports fetching and processing F1 pre-season testing data from the OpenF1 API during testing weeks (Feb 11-13 and Feb 18-20, 2026).

## What Changed

### New Files Created
1. **`config/testing_sessions_2026.json`** - Testing sessions schedule with Copenhagen timezone (CET UTC+1)
2. **`openf1_fetcher.py`** - OpenF1 API integration module for fetching testing lap data
3. **`run_testing_pipeline.py`** - Manual testing pipeline runner

### Modified Files
1. **`scheduler.py`** - Added `--enable-testing` flag to monitor testing sessions
2. **`master_pipeline.py`** - Added `--testing` flag to process testing data

## Testing Sessions Schedule

### Test 1: Feb 11-13, 2026
- **Day 1**: Feb 11, 08:00-17:00 CET (Session Key: 11465)
- **Day 2**: Feb 12, 08:00-17:00 CET (Session Key: 11466)  
- **Day 3**: Feb 13, 08:00-17:00 CET (Session Key: 11467)

### Test 2: Feb 18-20, 2026
- **Day 1**: Feb 18, 08:00-17:00 CET (Session Key: 11470)
- **Day 2**: Feb 19, 08:00-17:00 CET (Session Key: 11469)
- **Day 3**: Feb 20, 08:00-17:00 CET (Session Key: 11468)

## Data Availability
- **OpenF1 data**: Available in REAL-TIME during sessions
- **Typical delay**: 12-24 hours after session ends for complete data
- **What's included**: Lap times, sector times, speed traps (i1, i2, finish line)
- **What's missing**: Detailed telemetry (GPS, throttle, brake, RPM)

## Usage

### Option 1: Manual Processing (Recommended for Testing Weeks)

List available sessions:
```powershell
python run_testing_pipeline.py --list
```

Process a specific testing session:
```powershell
# Test 1, Day 1
python run_testing_pipeline.py --test 1 --day 1

# Test 2, Day 3
python run_testing_pipeline.py --test 2 --day 3
```

Fetch data only (without running pipeline):
```powershell
python run_testing_pipeline.py --test 1 --day 1 --fetch-only
```

Run pipeline on already-fetched data:
```powershell
python run_testing_pipeline.py --pipeline-only
```

### Option 2: Automatic Scheduler Monitoring

Enable testing sessions in scheduler:
```powershell
python scheduler.py --mode continuous --enable-testing
```

The scheduler will:
- Check for testing sessions 24 hours after they end
- Auto-fetch data from OpenF1
- Run master pipeline automatically
- Mark sessions as processed

### Option 3: Direct OpenF1 Fetch + Pipeline

Fetch testing data directly:
```powershell
python openf1_fetcher.py 11465 1 1
```
Parameters: `<session_key> <test_number> <day>`

Then run pipeline:
```powershell
python master_pipeline.py --testing
```

## Docker Usage

Update `entrypoint.sh` to enable testing in container:
```bash
exec python -u scheduler.py --mode continuous --buffer 0.167 --check-interval 5 --enable-testing
```

Or rebuild and run:
```powershell
docker-compose up --build
```

## Workflow Example

### For Next Week's Testing (Feb 18-20)

**Day 1 (Feb 18)**
```powershell
# Wait until Feb 19 morning (12-24 hours after session)
python run_testing_pipeline.py --test 2 --day 1
```

**Day 2 (Feb 19)**
```powershell
# Wait until Feb 20 morning
python run_testing_pipeline.py --test 2 --day 2
```

**Day 3 (Feb 20)**  
```powershell
# Wait until Feb 21 morning
python run_testing_pipeline.py --test 2 --day 3
```

## Output

After processing, you'll find:

1. **Telemetry data**: `telemetry_out/`
   - `*_laps.csv` - Lap times and sectors per driver
   - `session_meta.json` - Session metadata

2. **Driver reports**: `driver_reports/Pre-Season_Testing_*/`
   - Lap time analysis
   - Speed trap comparisons
   - Stint analysis (limited without detailed telemetry)

3. **Logs**: `logs/run_summary_*.json`

## Limitations

Testing data from OpenF1 has limitations compared to race weekend telemetry:

| Feature | OpenF1 Testing | FastF1 Race Weekend |
|---------|----------------|---------------------|
| Lap times | ✅ Available | ✅ Available |
| Sector times | ✅ Available | ✅ Available |
| Speed traps | ✅ Available | ✅ Available |
| GPS coordinates | ❌ Not available | ✅ Available |
| Throttle/Brake | ❌ Not available | ✅ Available |
| RPM/Gear | ❌ Not available | ✅ Available |
| Racing line analysis | ❌ Not possible | ✅ Available |
| Tire degradation | ⚠️ Limited | ✅ Full analysis |

## Troubleshooting

### "OpenF1 data not yet available"
- **Cause**: Data hasn't been published yet
- **Solution**: Wait 12-24 hours after session ends, then retry

### "No testing session metadata found"
- **Cause**: Need to fetch data first
- **Solution**: Run `openf1_fetcher.py` or `run_testing_pipeline.py --test X --day Y`

### "Scheduler skipping testing sessions"
- **Cause**: Testing not enabled
- **Solution**: Add `--enable-testing` flag to scheduler command

## Configuration

Edit `config/testing_sessions_2026.json` to:
- Add more testing events
- Update session times (currently Copenhagen timezone CET UTC+1)
- Modify OpenF1 session keys

## Notes

- All times in config are Copenhagen timezone (CET = UTC+1 for February)
- OpenF1 data appears to be published in real-time during sessions
- Complete data typically available 12-24 hours after session ends
- Scheduler buffer for testing: 24 hours (vs 10 minutes for races)
- Testing sessions marked with `is_testing: true` flag internally

## Next Steps

1. **For Feb 18-20 testing**: Start running `run_testing_pipeline.py` on Feb 19 morning
2. **Monitor data**: Check OpenF1 availability with `--list` command
3. **Review outputs**: Analyze lap times and speed traps in generated reports
4. **Adjust config**: Update timing windows if data appears faster/slower than expected
