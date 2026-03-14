#!/bin/bash
# Container startup script - runs automatically when container starts

echo "========================================================================"
echo "F1 TELEMETRY SCHEDULER - AUTONOMOUS MODE"
echo "========================================================================"
echo ""
echo "Container will:"
echo "  ✓ Monitor F1 schedule automatically (FastF1 EventSchedule)"
echo "  ✓ Sleep until sessions end + 10 minute buffer"
echo "  ✓ Check for telemetry availability every 5 minutes"
echo "  ✓ Process telemetry when available (all sessions: FP1, FP2, FP3, Q, R)"
echo "  ✓ Load results to Snowflake"
echo "  ✓ Go back to sleep until next session"
echo ""
echo "Session Processing:"
echo "  - Practice (FP1/FP2/FP3): Telemetry + Racing Line + Qual/Race Classifier"
echo "  - Qualifying: Telemetry + Racing Line + Driver Reports"
echo "  - Race: Full Pipeline (6 stages) + Tire Degradation ML"
echo ""
echo "Health check: /app/logs/health.json (updated every check cycle)"
echo "Logs: /app/logs/"
echo "Data: /app/telemetry_out/"
echo ""
echo "========================================================================"
echo "Initializing FastF1 cache and loading 2026 schedule..."
echo "========================================================================"

# Ensure directories exist
mkdir -p /app/logs /app/f1_cache /app/telemetry_out /app/models /app/driver_reports

# Warm up FastF1 cache by loading event schedule
# This prevents delays on first session check
echo "Loading FastF1 2026 event schedule..."
python -c "import fastf1; import warnings; warnings.filterwarnings('ignore'); fastf1.Cache.enable_cache('/app/f1_cache'); schedule = fastf1.get_event_schedule(2026); print(f'✓ Loaded {len(schedule)} events for 2026 season')" 2>/dev/null || echo "⚠ Could not pre-load schedule (will load on first check)"

echo ""
echo "Starting scheduler with 10-minute buffer..."
echo "========================================================================"
echo ""

# Run scheduler in unbuffered mode (for immediate log output)
exec python -u scheduler.py --mode continuous --buffer 0.167 --check-interval 5
