# F1 Telemetry Pipeline - Docker Container
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY fetch_pipeline.py .
COPY aggregate_for_viz.py .
COPY cross_session_analytics.py .
COPY scheduler.py .
COPY pipeline_logger.py .
COPY snowflake_config.py .
COPY weather_integration.py .
COPY status_api.py .
COPY master_pipeline.py .
COPY racing_line_analyzer.py .
COPY tire_degradation_model.py .
COPY tools/ ./tools/
COPY schedule/ ./schedule/
COPY static/ ./static/
COPY docker_validate.py .
COPY entrypoint.sh .

# Make entrypoint executable
RUN chmod +x entrypoint.sh

# Create necessary directories
RUN mkdir -p /app/logs /app/f1_cache /app/telemetry_out /app/models /app/driver_reports

# Health check - verify scheduler is running and responsive
HEALTHCHECK --interval=5m --timeout=10s --start-period=30s --retries=3 \
    CMD python -c "import json, sys, datetime; data = json.load(open('/app/logs/health.json')); last = datetime.datetime.fromisoformat(data['last_check']); sys.exit(0 if (datetime.datetime.now(datetime.timezone.utc) - last).seconds < 360 else 1)" || exit 1

# Expose volumes for logs, data, and ML models
VOLUME ["/app/logs", "/app/telemetry_out", "/app/f1_cache", "/app/models", "/app/driver_reports"]

# Run the scheduler automatically via entrypoint
ENTRYPOINT ["./entrypoint.sh"]
