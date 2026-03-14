"""
Status API - REST endpoint for monitoring F1 telemetry pipeline
Provides health checks, run status, and error reporting
"""
from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
import json
import os
from datetime import datetime, timezone
from pathlib import Path
import snowflake.connector
from snowflake_config import SNOWFLAKE_CONFIG

# Load .env file
env_file = Path(__file__).parent / '.env'
if env_file.exists():
    with open(env_file, 'r') as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                os.environ[key] = value

app = Flask(__name__, static_folder='static')
CORS(app)  # Enable CORS for web dashboards

LOGS_DIR = Path('logs')
HEALTH_FILE = LOGS_DIR / 'health.json'
RUN_SUMMARY_DIR = LOGS_DIR / 'run_summaries'
LIVE_DATA_DIR = Path('telemetry_out/live')


def load_live_status():
    """Load live session status"""
    try:
        live_file = LIVE_DATA_DIR / 'live_status.json'
        if live_file.exists():
            with open(live_file, 'r') as f:
                return json.load(f)
        return None
    except Exception as e:
        return {'error': str(e)}


def load_health_status():
    """Load health check data"""
    try:
        if HEALTH_FILE.exists():
            with open(HEALTH_FILE, 'r') as f:
                return json.load(f)
        return None
    except Exception as e:
        return {'error': str(e)}


def load_latest_run():
    """Load most recent run summary"""
    try:
        if not RUN_SUMMARY_DIR.exists():
            return None
        
        summaries = sorted(RUN_SUMMARY_DIR.glob('*.json'), reverse=True)
        if summaries:
            with open(summaries[0], 'r') as f:
                return json.load(f)
        return None
    except Exception as e:
        return {'error': str(e)}


def check_snowflake_connection():
    """Test Snowflake connectivity"""
    try:
        conn = snowflake.connector.connect(
            account=SNOWFLAKE_CONFIG['account'],
            user=SNOWFLAKE_CONFIG['user'],
            password=SNOWFLAKE_CONFIG['password'],
            database=SNOWFLAKE_CONFIG['database'],
            warehouse=SNOWFLAKE_CONFIG['warehouse']
        )
        cursor = conn.cursor()
        cursor.execute("SELECT CURRENT_VERSION()")
        version = cursor.fetchone()[0]
        conn.close()
        return {
            'connected': True,
            'version': version,
            'account': SNOWFLAKE_CONFIG['account'],
            'database': SNOWFLAKE_CONFIG['database']
        }
    except Exception as e:
        return {
            'connected': False,
            'error': str(e)
        }


def get_next_sessions():
    """Get upcoming sessions from schedule"""
    try:
        schedule_file = Path('schedule/schedule_2026.json')
        if not schedule_file.exists():
            return []
        
        with open(schedule_file, 'r') as f:
            schedule = json.load(f)
        
        now = datetime.now(timezone.utc)
        upcoming = []
        
        for event in schedule:
            for session_type in ['fp1', 'fp2', 'fp3', 'qualifying', 'sprint', 'race']:
                session = event.get(session_type)
                if session and 'end_timestamp_utc' in session:
                    end_time = datetime.fromisoformat(session['end_timestamp_utc'].replace('Z', '+00:00'))
                    if end_time > now:
                        upcoming.append({
                            'event': event['event_name'],
                            'session': session_type.upper(),
                            'end_time': session['end_timestamp_utc'],
                            'hours_until': (end_time - now).total_seconds() / 3600
                        })
        
        return sorted(upcoming, key=lambda x: x['hours_until'])[:5]
    except Exception as e:
        return [{'error': str(e)}]


@app.route('/')
def index():
    """Serve dashboard"""
    return send_from_directory('static', 'dashboard.html')


@app.route('/api')
def api_docs():
    """API documentation"""
    return jsonify({
        'name': 'F1 Telemetry Pipeline Status API',
        'version': '1.0',
        'endpoints': {
            '/': 'Interactive dashboard',
            '/api': 'API documentation',
            '/api/health': 'Health check status',
            '/api/status': 'Complete pipeline status',
            '/api/latest-run': 'Most recent pipeline run details',
            '/api/snowflake': 'Snowflake connection status',
            '/api/schedule': 'Upcoming F1 sessions',
            '/api/errors': 'Recent errors and warnings',
            '/api/live': 'Live session status (if active)',
            '/api/live/laps': 'Live lap data',
        }
    })


@app.route('/api/health')
def health():
    """Health check endpoint"""
    health_data = load_health_status()
    
    if not health_data:
        return jsonify({
            'status': 'unknown',
            'message': 'No health data available'
        }), 503
    
    if 'error' in health_data:
        return jsonify({
            'status': 'error',
            'error': health_data['error']
        }), 500
    
    # Check if last update is recent (within 6 minutes)
    last_check = datetime.fromisoformat(health_data.get('last_check', '2000-01-01T00:00:00+00:00'))
    now = datetime.now(timezone.utc)
    minutes_since = (now - last_check).total_seconds() / 60
    
    is_healthy = minutes_since < 6
    
    return jsonify({
        'status': 'healthy' if is_healthy else 'stale',
        'last_check': health_data.get('last_check'),
        'minutes_since_update': round(minutes_since, 1),
        'next_check': health_data.get('next_check'),
        'scheduler_status': health_data.get('status'),
        'last_session_processed': health_data.get('last_session_processed')
    }), 200 if is_healthy else 503


@app.route('/api/status')
def status():
    """Complete pipeline status"""
    health_data = load_health_status()
    latest_run = load_latest_run()
    snowflake_status = check_snowflake_connection()
    upcoming_sessions = get_next_sessions()
    
    return jsonify({
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'health': health_data,
        'latest_run': latest_run,
        'snowflake': snowflake_status,
        'upcoming_sessions': upcoming_sessions
    })


@app.route('/api/latest-run')
def latest_run():
    """Most recent pipeline run"""
    run_data = load_latest_run()
    
    if not run_data:
        return jsonify({
            'status': 'no_runs',
            'message': 'No pipeline runs found'
        }), 404
    
    return jsonify(run_data)


@app.route('/api/snowflake')
def snowflake_status():
    """Snowflake connection test"""
    return jsonify(check_snowflake_connection())


@app.route('/api/schedule')
def schedule():
    """Upcoming sessions"""
    return jsonify({
        'upcoming_sessions': get_next_sessions()
    })


@app.route('/api/errors')
def errors():
    """Recent errors from logs"""
    try:
        errors_list = []
        warnings_list = []
        
        # Read latest run summary
        run_data = load_latest_run()
        if run_data:
            errors_list = run_data.get('errors', [])
            warnings_list = run_data.get('warnings', [])
        
        # Also check latest pipeline log
        log_files = sorted(LOGS_DIR.glob('pipeline_*.log'), reverse=True)
        if log_files:
            try:
                with open(log_files[0], 'r', encoding='utf-8', errors='ignore') as f:
                    lines = f.readlines()[-100:]  # Last 100 lines
                    
                for line in lines:
                    if 'ERROR' in line:
                        errors_list.append(line.strip())
                    elif 'WARNING' in line:
                        warnings_list.append(line.strip())
            except Exception as e:
                # If we can't read the log file, just skip it
                pass
        
        return jsonify({
            'errors': errors_list[-20:] if errors_list else [],  # Last 20 errors
            'warnings': warnings_list[-20:] if warnings_list else [],  # Last 20 warnings
            'error_count': len(errors_list),
            'warning_count': len(warnings_list)
        })
    except Exception as e:
        return jsonify({
            'error': str(e)
        }), 500


@app.route('/api/schemas')
def schemas():
    """List all session schemas in Snowflake"""
    try:
        conn = snowflake.connector.connect(
            account=SNOWFLAKE_CONFIG['account'],
            user=SNOWFLAKE_CONFIG['user'],
            password=SNOWFLAKE_CONFIG['password'],
            database=SNOWFLAKE_CONFIG['database'],
            warehouse=SNOWFLAKE_CONFIG['warehouse']
        )
        cursor = conn.cursor()
        cursor.execute("""
            SELECT SCHEMA_NAME, CREATED
            FROM INFORMATION_SCHEMA.SCHEMATA
            WHERE SCHEMA_NAME NOT IN ('INFORMATION_SCHEMA', 'PUBLIC')
            ORDER BY CREATED DESC
        """)
        schemas = [{'schema': row[0], 'created': str(row[1])} for row in cursor.fetchall()]
        conn.close()
        
        return jsonify({
            'schemas': schemas,
            'count': len(schemas)
        })
    except Exception as e:
        return jsonify({
            'error': str(e)
        }), 500


@app.route('/api/live')
def live_session():
    """Get live session status"""
    live_status = load_live_status()
    
    if not live_status:
        return jsonify({
            'status': 'no_live_session',
            'message': 'No live session currently running'
        }), 404
    
    return jsonify(live_status)


@app.route('/api/live/laps')
def live_laps():
    """Get live lap data"""
    try:
        laps_file = LIVE_DATA_DIR / 'live_laps.json'
        if not laps_file.exists():
            return jsonify({
                'status': 'no_data',
                'message': 'No live lap data available'
            }), 404
        
        with open(laps_file, 'r') as f:
            laps = json.load(f)
        
        return jsonify({
            'laps': laps,
            'count': len(laps)
        })
    except Exception as e:
        return jsonify({
            'error': str(e)
        }), 500


if __name__ == '__main__':
    # Create logs directory if needed
    LOGS_DIR.mkdir(exist_ok=True)
    
    # Run API server
    port = int(os.getenv('API_PORT', 5000))
    debug = os.getenv('API_DEBUG', 'false').lower() == 'true'
    
    print(f"\n{'='*70}")
    print(f"F1 Telemetry Pipeline Status API")
    print(f"{'='*70}")
    print(f"Dashboard: http://localhost:{port}")
    print(f"API Endpoints:")
    print(f"  • http://localhost:{port}/api/health")
    print(f"  • http://localhost:{port}/api/status")
    print(f"  • http://localhost:{port}/api/latest-run")
    print(f"  • http://localhost:{port}/api/snowflake")
    print(f"  • http://localhost:{port}/api/schedule")
    print(f"  • http://localhost:{port}/api/errors")
    print(f"  • http://localhost:{port}/api/schemas")
    print(f"  • http://localhost:{port}/api/live")
    print(f"{'='*70}\n")
    
    app.run(host='0.0.0.0', port=port, debug=debug)
