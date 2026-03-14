"""
Manual Testing Session Pipeline Runner
Run this script during testing weeks (Feb 11-13 and Feb 18-20) to manually fetch and process testing data
"""
import sys
import json
import argparse
import subprocess
from pathlib import Path
from datetime import datetime
import logging

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def load_testing_config():
    """Load testing sessions configuration"""
    config_file = Path('config/testing_sessions_2026.json')
    if not config_file.exists():
        logger.error(f"Testing config not found: {config_file}")
        return None
    
    with open(config_file, 'r') as f:
        return json.load(f)


def list_testing_sessions():
    """Display all available testing sessions"""
    config = load_testing_config()
    if not config:
        return
    
    print("\n" + "="*70)
    print("AVAILABLE TESTING SESSIONS")
    print("="*70)
    
    for test_event in config['testing_events']:
        print(f"\nTest {test_event['test_number']}: {test_event['name']}")
        print(f"  Location: {test_event['location']}")
        print(f"  Dates: {test_event['dates']}")
        print(f"  Sessions:")
        
        for session in test_event['sessions']:
            print(f"    Day {session['day']}: {session['date']} "
                  f"{session['start_time']} - {session['end_time']} "
                  f"(Session Key: {session['openf1_session_key']})")
    
    print("\n" + "="*70)
    print("To process a session, run:")
    print("  python run_testing_pipeline.py --test 1 --day 1")
    print("  python run_testing_pipeline.py --test 2 --day 3")
    print("="*70 + "\n")


def check_data_available(session_key):
    """Check if OpenF1 data is available for a session"""
    try:
        from openf1_fetcher import OpenF1Fetcher
        
        fetcher = OpenF1Fetcher()
        available = fetcher.check_data_available(session_key)
        
        if available:
            logger.info(f"✓ OpenF1 data is available for session {session_key}")
        else:
            logger.warning(f"✗ OpenF1 data not yet available for session {session_key}")
        
        return available
    except Exception as e:
        logger.error(f"Error checking data availability: {e}")
        return False


def check_local_data_exists():
    """Check if telemetry data already exists locally"""
    telemetry_dir = Path('telemetry_out')
    if not telemetry_dir.exists():
        return False
    
    # Check if session metadata exists
    meta_file = telemetry_dir / 'session_meta.json'
    if not meta_file.exists():
        return False
    
    # Check if lap files exist
    lap_files = list(telemetry_dir.glob('*_laps.csv'))
    return len(lap_files) > 0


def fetch_testing_data(test_number, day, force_fetch=False, skip_check=False):
    """Fetch testing data from OpenF1"""
    config = load_testing_config()
    if not config:
        return False
    
    # Check if data already exists locally
    if not force_fetch and check_local_data_exists():
        logger.info("Local telemetry data already exists in telemetry_out/")
        response = input("Use existing data? (y/n) [y]: ").strip().lower()
        if response in ('', 'y', 'yes'):
            logger.info("✓ Using existing local data")
            return True
        logger.info("Fetching fresh data from OpenF1...")
    
    # Find the requested session
    test_event = None
    for event in config['testing_events']:
        if event['test_number'] == test_number:
            test_event = event
            break
    
    if not test_event:
        logger.error(f"Test {test_number} not found in config")
        return False
    
    # Find the session day
    session_info = None
    for session in test_event['sessions']:
        if session['day'] == day:
            session_info = session
            break
    
    if not session_info:
        logger.error(f"Day {day} not found in Test {test_number}")
        return False
    
    # Check data availability
    logger.info(f"Checking OpenF1 data availability for session {session_info['openf1_session_key']}...")
    
    if not skip_check:
        if not check_data_available(session_info['openf1_session_key']):
            logger.warning(f"OpenF1 data not yet available for Test {test_number} Day {day}")
            logger.warning(f"Session date: {session_info['date']}")
            logger.warning(f"Data typically appears 12-24 hours after session ends")
            logger.warning(f"TIP: Use --skip-check to try fetching anyway")
            return False
    else:
        logger.info("Skipping availability check (--skip-check enabled)")
    
    logger.info(f"Fetching data from OpenF1...")
    
    # Fetch the data
    from openf1_fetcher import fetch_testing_session
    
    success = fetch_testing_session(
        session_key=session_info['openf1_session_key'],
        test_number=test_number,
        day=day,
        event_name=test_event['name'],
        date=session_info['date']
    )
    
    if not success:
        logger.error("Failed to fetch testing data")
        return False
    
    logger.info(f"✓ Successfully fetched testing data to telemetry_out/")
    return True


def run_pipeline():
    """Run the master pipeline on fetched data"""
    logger.info("Running master pipeline on testing data...")
    
    try:
        result = subprocess.run(
            ['python', 'master_pipeline.py', '--testing'],
            capture_output=True,
            text=True,
            timeout=1800  # 30 minute timeout
        )
        
        if result.returncode == 0:
            logger.info("✓ Pipeline completed successfully")
            print("\n" + result.stdout)
            return True
        else:
            logger.error("✗ Pipeline failed")
            print(result.stderr)
            return False
            
    except subprocess.TimeoutExpired:
        logger.error("Pipeline timeout after 30 minutes")
        return False
    except Exception as e:
        logger.error(f"Error running pipeline: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(
        description='Manual Testing Session Pipeline Runner',
        epilog='Example: python run_testing_pipeline.py --test 1 --day 2'
    )
    
    parser.add_argument('--test', type=int, choices=[1, 2],
                      help='Test number (1 or 2)')
    parser.add_argument('--day', type=int, choices=[1, 2, 3],
                      help='Day of test (1, 2, or 3)')
    parser.add_argument('--list', action='store_true',
                      help='List all available testing sessions')
    parser.add_argument('--fetch-only', action='store_true',
                      help='Only fetch data, do not run pipeline')
    parser.add_argument('--pipeline-only', action='store_true',
                      help='Only run pipeline on existing data (skip fetch)')
    parser.add_argument('--force-fetch', action='store_true',
                      help='Force fetch even if data already exists locally')
    parser.add_argument('--skip-check', action='store_true',
                      help='Skip OpenF1 availability check and try to fetch anyway')
    
    args = parser.parse_args()
    
    # List sessions if requested
    if args.list:
        list_testing_sessions()
        return
    
    # Validate required arguments
    if not args.pipeline_only:
        if not args.test or not args.day:
            parser.error('--test and --day are required (or use --list to see available sessions)')
    
    print("\n" + "="*70)
    print("F1 TESTING SESSION PIPELINE RUNNER")
    print("="*70)
    
    if not args.pipeline_only:
        print(f"\nTarget: Test {args.test}, Day {args.day}")
        print(f"Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print("="*70 + "\n")
        
        # Step 1: Fetch data
        logger.info("Step 1: Fetching testing data from OpenF1...")
        success = fetch_testing_data(
            args.test, 
            args.day,
            force_fetch=args.force_fetch,
            skip_check=args.skip_check
        )
        
        if not success:
            logger.error("Failed to fetch testing data")
            sys.exit(1)
        
        if args.fetch_only:
            logger.info("\n✓ Data fetched successfully (--fetch-only mode, skipping pipeline)")
            return
    
    # Step 2: Run pipeline
    logger.info("\nStep 2: Running analytics pipeline...")
    success = run_pipeline()
    
    if success:
        print("\n" + "="*70)
        print("✓ TESTING SESSION PROCESSED SUCCESSFULLY")
        print("="*70)
        print("\nOutputs:")
        print("  - Telemetry data: telemetry_out/")
        print("  - Driver reports: driver_reports/")
        print("  - Logs: logs/")
        print("="*70 + "\n")
    else:
        print("\n" + "="*70)
        print("✗ PIPELINE FAILED")
        print("="*70 + "\n")
        sys.exit(1)


if __name__ == '__main__':
    main()
