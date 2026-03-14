"""
F1 Session Scheduler - Automatically runs pipeline after sessions complete
"""
import json
import datetime
import time
import subprocess
import logging
from pathlib import Path
from typing import Optional
import pandas as pd
import sys

# Setup logging
log_dir = Path('logs')
log_dir.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(log_dir / f'scheduler_{datetime.datetime.now().strftime("%Y%m%d")}.log'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)


class F1SessionScheduler:
    def __init__(self, schedule_file='schedule/schedule_2026.json', buffer_hours=0.167, check_interval_minutes=5,
                 enable_testing=False):
        self.schedule_file = Path(schedule_file)
        self.buffer_hours = buffer_hours  # Hours to wait after session for data availability (10 minutes)
        self.check_interval_minutes = check_interval_minutes  # Check every 5 minutes for data
        self.processed_sessions = self.load_processed_sessions()
        self.enable_testing = enable_testing  # Whether to monitor testing sessions
        
    def load_schedule(self):
        """Load the F1 schedule from JSON"""
        try:
            with open(self.schedule_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Failed to load schedule: {e}")
            return []
    
    def load_testing_schedule(self, year=2026):
        """Load testing sessions schedule from config"""
        testing_file = Path(f'config/testing_sessions_{year}.json')
        if not testing_file.exists():
            return []
        
        try:
            with open(testing_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return data.get('testing_events', [])
        except Exception as e:
            logger.error(f"Failed to load testing schedule: {e}")
            return []
    
    def load_processed_sessions(self):
        """Load record of already processed sessions"""
        processed_file = Path('logs/processed_sessions.json')
        if processed_file.exists():
            try:
                with open(processed_file, 'r') as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"Could not load processed sessions: {e}")
        return {}
    
    def save_processed_session(self, event_name, session_type, timestamp):
        """Record that a session has been processed"""
        session_key = f"{event_name}_{session_type}"
        self.processed_sessions[session_key] = {
            'processed_at': timestamp.isoformat(),
            'event': event_name,
            'session': session_type
        }
        
        processed_file = Path('logs/processed_sessions.json')
        try:
            with open(processed_file, 'w') as f:
                json.dump(self.processed_sessions, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save processed sessions: {e}")
    
    def is_session_processed(self, event_name, session_type):
        """Check if session has already been processed"""
        session_key = f"{event_name}_{session_type}"
        return session_key in self.processed_sessions
    
    def get_next_session_check_time(self):
        """Calculate when the next session check should occur using FastF1 for accurate session times"""
        schedule = self.load_schedule()
        now = datetime.datetime.now(datetime.timezone.utc)
        
        upcoming_checks = []
        
        for event in schedule:
            try:
                # Parse event end timestamp to get year
                end_time = datetime.datetime.fromisoformat(event['end_timestamp_utc'].replace('+00:00', ''))
                end_time = end_time.replace(tzinfo=datetime.timezone.utc)
                year = end_time.year
                
                # Only check events within next 45 days
                days_until = (end_time - now).total_seconds() / 86400
                if not (0 < days_until <= 45):
                    continue
                
                # Get actual session times from FastF1
                import fastf1
                event_schedule = fastf1.get_event_schedule(year)
                event_row = event_schedule[event_schedule['EventName'] == event['name']]
                
                if event_row.empty:
                    continue

                # Detect and persist event format based on SessionN name strings
                try:
                    self._detect_and_save_event_format(event['name'], event_row)
                except Exception:
                    pass
                
                    # Derive session->SessionN mapping for this event based on event formats
                    try:
                        tag_file = Path('config/session_tags.json')
                        if tag_file.exists():
                            with open(tag_file, 'r', encoding='utf-8') as f:
                                all_session_tags = json.load(f)
                        else:
                            all_session_tags = {}
                    except Exception:
                        all_session_tags = {}

                    # Collect SessionN timestamp columns (SessionNDateUtc or SessionNDate)
                    available = []
                    for i in range(1, 6):
                        col = f"Session{i}"
                        col_date_utc = f"{col}DateUtc"
                        col_date = f"{col}Date"
                        ts = None
                        if col_date_utc in event_row.columns:
                            ts = event_row[col_date_utc].iloc[0]
                        elif col_date in event_row.columns:
                            ts = event_row[col_date].iloc[0]

                        if ts is not None and pd.notna(ts):
                            # FastF1 returns strings for session names and datetimes for date columns
                            if isinstance(ts, pd.Timestamp):
                                available.append((col, ts.to_pydatetime()))
                            else:
                                try:
                                    # Try to parse string to datetime
                                    parsed = pd.to_datetime(ts)
                                    available.append((col, parsed.to_pydatetime()))
                                except Exception:
                                    continue

                    if not available:
                        continue

                    # Sort by datetime to get the chronological order of sessions
                    available.sort(key=lambda x: x[1])

                    # Load event format mappings
                    try:
                        with open(Path('config/event_formats.json'), 'r', encoding='utf-8') as ef:
                            event_formats = json.load(ef)
                    except Exception:
                        event_formats = {
                            'conventional': ['FP1', 'FP2', 'FP3', 'Q', 'R'],
                            'sprint': ['FP1', 'Q', 'FP2', 'S', 'R'],
                            'sprint_shootout': ['FP1', 'Q', 'SS', 'S', 'R'],
                            'sprint_qualifying': ['FP1', 'SQ', 'S', 'Q', 'R'],
                            'testing': []
                        }
                    
                    # Check if this is a testing event (only practice sessions)
                    is_testing = False
                    try:
                        # Quick check: read Session1-Session5 names to detect testing
                        session_names = []
                        for i in range(1, 6):
                            col = f"Session{i}"
                            if col in event_row.columns:
                                val = event_row[col].iloc[0]
                                if isinstance(val, str) and val.strip():
                                    session_names.append(val.lower())
                        
                        has_practice = any('practice' in s for s in session_names)
                        has_race = any('race' in s for s in session_names)
                        has_qual = any('qualifying' in s for s in session_names)
                        
                        if has_practice and not has_race and not has_qual:
                            is_testing = True
                    except Exception:
                        pass
                    
                    # Skip testing events - no telemetry data available
                    if is_testing:
                        continue

                    # Choose a format with matching number of sessions, prefer 'conventional' if available
                    candidate_formats = [name for name, tags in event_formats.items() if len(tags) == len(available) and len(tags) > 0]
                    chosen_format = None
                    
                    if 'conventional' in candidate_formats:
                        chosen_format = 'conventional'
                        format_tags = event_formats.get(chosen_format, ['FP1', 'FP2', 'FP3', 'Q', 'R'])
                    elif candidate_formats:
                        chosen_format = candidate_formats[0]
                        format_tags = event_formats.get(chosen_format, ['FP1', 'FP2', 'FP3', 'Q', 'R'])
                    else:
                        # Fallback: assign tags by common order (conventional) truncated/padded
                        chosen_format = 'conventional'
                        format_tags = event_formats.get(chosen_format, ['FP1', 'FP2', 'FP3', 'Q', 'R'])

                    # Build mapping of tag -> (col, start_time, duration)
                    mapping = {}
                    for (col, ts), tag in zip(available, format_tags):
                        info = all_session_tags.get(tag, {})
                        duration = info.get('duration_hours', 1)
                        mapping[tag] = {'col': col, 'start_time': ts, 'duration_hours': duration}

                    for session_type, meta in mapping.items():
                        session_col = meta.get('col')
                        duration = meta.get('duration_hours', 1)

                        session_time = event_row[session_col].iloc[0]
                        session_end = session_time.to_pydatetime()
                        if session_end.tzinfo is None:
                            session_end = session_end.replace(tzinfo=datetime.timezone.utc)
                        session_end += datetime.timedelta(hours=duration)

                        # Check time = session end + buffer
                        check_time = session_end + datetime.timedelta(hours=self.buffer_hours)
                        hours_until_check = (check_time - now).total_seconds() / 3600

                        if 0 < hours_until_check <= 720:  # Future checks within 30 days
                            upcoming_checks.append({
                                'event': event['name'],
                                'session': session_type,
                                'end_time': session_end,
                                'check_time': check_time,
                                'hours_until': hours_until_check
                            })
                
            except Exception as e:
                logger.warning(f"Error calculating check time for {event.get('name', 'unknown')}: {e}")
                continue
        
        # Add testing sessions if enabled
        if self.enable_testing:
            try:
                testing_events = self.load_testing_schedule()
                for test_event in testing_events:
                    for session in test_event.get('sessions', []):
                        # Parse session end time
                        date_str = session['date']
                        end_time_str = session['end_time']
                        
                        # Combine date and time
                        end_dt_str = f"{date_str}T{end_time_str}"
                        end_dt = datetime.datetime.fromisoformat(end_dt_str)
                        
                        if end_dt.tzinfo is None:
                            end_dt = end_dt.replace(tzinfo=datetime.timezone.utc)
                        
                        # Check time = session end + buffer (24 hours for testing to allow data publication)
                        testing_buffer_hours = 24  # Testing data takes longer to appear
                        check_time = end_dt + datetime.timedelta(hours=testing_buffer_hours)
                        hours_until_check = (check_time - now).total_seconds() / 3600
                        
                        if 0 < hours_until_check <= 720:  # Future checks within 30 days
                            session_label = f"Test {test_event['test_number']} - {session['label']}"
                            upcoming_checks.append({
                                'event': test_event['name'],
                                'session': session_label,
                                'end_time': end_dt,
                                'check_time': check_time,
                                'hours_until': hours_until_check,
                                'is_testing': True,
                                'openf1_session_key': session['openf1_session_key'],
                                'test_number': test_event['test_number'],
                                'day': session['day']
                            })
            except Exception as e:
                logger.warning(f"Error loading testing sessions: {e}")
        
        # Sort by check time
        upcoming_checks.sort(key=lambda x: x['check_time'])
        
        return upcoming_checks[0] if upcoming_checks else None
    
    def get_sessions_to_process(self):
        """Find sessions that have finished but not yet processed"""
        schedule = self.load_schedule()
        now = datetime.datetime.now(datetime.timezone.utc)
        
        sessions_to_process = []
        
        for event in schedule:
            # Parse event end timestamp to determine year
            try:
                end_time = datetime.datetime.fromisoformat(event['end_timestamp_utc'].replace('+00:00', ''))
                end_time = end_time.replace(tzinfo=datetime.timezone.utc)
                year = end_time.year
            except Exception as e:
                logger.warning(f"Invalid timestamp for {event['name']}: {e}")
                continue
            
            # Only check events within reasonable time window (last 7 days to next 7 days)
            days_since_event_end = (now - end_time).total_seconds() / 86400  # days
            if not (-7 <= days_since_event_end <= 7):
                continue
            
            # Get actual session times from FastF1
            try:
                import fastf1
                event_schedule = fastf1.get_event_schedule(year)
                event_row = event_schedule[event_schedule['EventName'] == event['name']]
                
                if event_row.empty:
                    logger.warning(f"Event not found in FastF1: {event['name']}")
                    continue

                # Detect and persist event format based on SessionN name strings
                try:
                    self._detect_and_save_event_format(event['name'], event_row)
                except Exception:
                    pass
                
                # Derive session->SessionN mapping for this event based on event formats
                try:
                    tag_file = Path('config/session_tags.json')
                    if tag_file.exists():
                        with open(tag_file, 'r', encoding='utf-8') as f:
                            all_session_tags = json.load(f)
                    else:
                        all_session_tags = {}
                except Exception:
                    all_session_tags = {}

                available = []
                for i in range(1, 6):
                    col = f"Session{i}"
                    col_date_utc = f"{col}DateUtc"
                    col_date = f"{col}Date"
                    ts = None
                    if col_date_utc in event_row.columns:
                        ts = event_row[col_date_utc].iloc[0]
                    elif col_date in event_row.columns:
                        ts = event_row[col_date].iloc[0]

                    if ts is not None and pd.notna(ts):
                        if isinstance(ts, pd.Timestamp):
                            available.append((col, ts.to_pydatetime()))
                        else:
                            try:
                                parsed = pd.to_datetime(ts)
                                available.append((col, parsed.to_pydatetime()))
                            except Exception:
                                continue

                if not available:
                    continue

                available.sort(key=lambda x: x[1])

                try:
                    with open(Path('config/event_formats.json'), 'r', encoding='utf-8') as ef:
                        event_formats = json.load(ef)
                except Exception:
                    event_formats = {
                        'conventional': ['FP1', 'FP2', 'FP3', 'Q', 'R'],
                        'sprint': ['FP1', 'Q', 'FP2', 'S', 'R'],
                        'sprint_shootout': ['FP1', 'Q', 'SS', 'S', 'R'],
                        'sprint_qualifying': ['FP1', 'SQ', 'S', 'Q', 'R'],
                        'testing': []
                    }
                
                # Check if this is a testing event (only practice sessions)
                is_testing = False
                try:
                    # Quick check: read Session1-Session5 names to detect testing
                    session_names = []
                    for i in range(1, 6):
                        col = f"Session{i}"
                        if col in event_row.columns:
                            val = event_row[col].iloc[0]
                            if isinstance(val, str) and val.strip():
                                session_names.append(val.lower())
                    
                    has_practice = any('practice' in s for s in session_names)
                    has_race = any('race' in s for s in session_names)
                    has_qual = any('qualifying' in s for s in session_names)
                    
                    if has_practice and not has_race and not has_qual:
                        is_testing = True
                except Exception:
                    pass

                # Check if this is a testing event (only practice sessions)
                is_testing = False
                try:
                    # Quick check: read Session1-Session5 names to detect testing
                    session_names = []
                    for i in range(1, 6):
                        col = f"Session{i}"
                        if col in event_row.columns:
                            val = event_row[col].iloc[0]
                            if isinstance(val, str) and val.strip():
                                session_names.append(val.lower())
                    
                    has_practice = any('practice' in s for s in session_names)
                    has_race = any('race' in s for s in session_names)
                    has_qual = any('qualifying' in s for s in session_names)
                    
                    if has_practice and not has_race and not has_qual:
                        is_testing = True
                except Exception:
                    pass
                
                # Skip testing events - F1 API doesn't provide telemetry data for pre-season testing
                # FastF1 also blocks RoundNumber=0 access which is used for testing events
                if is_testing:
                    logger.info(f"Skipping testing event '{event['name']}' - no telemetry data available")
                    continue
                
                candidate_formats = [name for name, tags in event_formats.items() if len(tags) == len(available) and len(tags) > 0]
                chosen_format = None
                
                if 'conventional' in candidate_formats:
                    chosen_format = 'conventional'
                    format_tags = event_formats.get(chosen_format, ['FP1', 'FP2', 'FP3', 'Q', 'R'])
                elif candidate_formats:
                    chosen_format = candidate_formats[0]
                    format_tags = event_formats.get(chosen_format, ['FP1', 'FP2', 'FP3', 'Q', 'R'])
                else:
                    chosen_format = 'conventional'
                    format_tags = event_formats.get(chosen_format, ['FP1', 'FP2', 'FP3', 'Q', 'R'])

                mapping = {}
                for (col, ts), tag in zip(available, format_tags):
                    info = all_session_tags.get(tag, {})
                    duration = info.get('duration_hours', 1)
                    mapping[tag] = {'col': col, 'start_time': ts, 'duration_hours': duration}

                for session_type, meta in mapping.items():
                    session_col = meta.get('col')
                    duration = meta.get('duration_hours', 1)

                    session_time = event_row[session_col].iloc[0]

                    # Skip if session doesn't exist (e.g., Sprint format)
                    if pd.isna(session_time):
                        continue

                    # Convert to timezone-aware datetime
                    if not isinstance(session_time, pd.Timestamp):
                        continue

                    session_end = session_time.to_pydatetime()
                    if session_end.tzinfo is None:
                        session_end = session_end.replace(tzinfo=datetime.timezone.utc)

                    # Session end = start + configured duration
                    session_end += datetime.timedelta(hours=duration)

                    # Check if session ended and give it buffer for data availability
                    time_since_end = (now - session_end).total_seconds() / 3600  # hours

                    if self.buffer_hours <= time_since_end <= 24:  # Between buffer and 24 hours after session
                        if not self.is_session_processed(event['name'], session_type):
                            sessions_to_process.append({
                                'event': event['name'],
                                'session_type': session_type,
                                'end_time': session_end
                            })
                            logger.info(f"[PENDING] {event['name']} {session_type} ended at {session_end}, processing now")
                
            except Exception as e:
                logger.error(f"Error getting FastF1 schedule for {event['name']}: {e}")
                continue
        
        # Add testing sessions if enabled
        if self.enable_testing:
            try:
                testing_events = self.load_testing_schedule()
                for test_event in testing_events:
                    for session in test_event.get('sessions', []):
                        # Parse session end time
                        date_str = session['date']
                        end_time_str = session['end_time']
                        
                        # Combine date and time
                        end_dt_str = f"{date_str}T{end_time_str}"
                        end_dt = datetime.datetime.fromisoformat(end_dt_str)
                        
                        if end_dt.tzinfo is None:
                            end_dt = end_dt.replace(tzinfo=datetime.timezone.utc)
                        
                        # Testing data needs 24+ hours to appear on OpenF1
                        testing_buffer_hours = 24
                        time_since_end = (now - end_dt).total_seconds() / 3600
                        
                        # Check if ready to process (24-168 hours after session - 1 week window)
                        if testing_buffer_hours <= time_since_end <= 168:
                            session_label = f"Test {test_event['test_number']} - {session['label']}"
                            event_name = test_event['name']
                            
                            if not self.is_session_processed(event_name, session_label):
                                sessions_to_process.append({
                                    'event': event_name,
                                    'session_type': session_label,
                                    'end_time': end_dt,
                                    'is_testing': True,
                                    'openf1_session_key': session['openf1_session_key'],
                                    'test_number': test_event['test_number'],
                                    'day': session['day'],
                                    'date': session['date']
                                })
                                logger.info(f"[PENDING TESTING] {event_name} {session_label} ended at {end_dt}, processing now")
            except Exception as e:
                logger.error(f"Error loading testing sessions for processing: {e}")
        
        return sessions_to_process
    
    def check_telemetry_available(self, event_name, session_type):
        """Quick check if telemetry data is available by attempting to load session"""
        try:
            import fastf1
            # Parse event name to get year
            year = 2026  # Default to current year
            for processed in self.processed_sessions.values():
                if processed['event'] == event_name:
                    year = 2026
                    break
            
            # Try to load session metadata (lightweight check)
            session = fastf1.get_session(year, event_name, session_type)
            session.load(telemetry=False, laps=True, weather=False, messages=False)
            
            # Check if we have lap data
            if session.laps is not None and not session.laps.empty:
                logger.info(f"[OK] Telemetry available for {event_name} - {session_type}")
                return True
            else:
                logger.info(f"[WAIT] Telemetry not yet available for {event_name} - {session_type}")
                return False
        except Exception as e:
            logger.info(f"[WAIT] Telemetry not available: {str(e)[:100]}")
            return False
    
    def run_pipeline(self, event_name, session_type, max_retries=12, is_testing=False, 
                     openf1_session_key=None, test_number=None, day=None, date=None):
        """Execute the master_pipeline.py script with telemetry availability checking"""
        logger.info(f"Starting pipeline for {event_name} - {session_type}")
        
        if is_testing:
            # For testing sessions, check OpenF1 data availability
            logger.info(f"Testing session detected - checking OpenF1 data availability...")
            try:
                from openf1_fetcher import OpenF1Fetcher
                fetcher = OpenF1Fetcher()
                
                if not fetcher.check_data_available(openf1_session_key):
                    logger.error(f"[ERROR] OpenF1 data not available for session {openf1_session_key}")
                    return False
                
                logger.info(f"[OK] OpenF1 data available - fetching...")
                
                # Fetch the testing data
                success = fetcher.fetch_session(
                    session_key=openf1_session_key,
                    test_number=test_number,
                    day=day,
                    event_name=event_name,
                    date=date
                )
                
                if not success:
                    logger.error(f"[ERROR] Failed to fetch OpenF1 data")
                    return False
                
                logger.info(f"[OK] Testing data fetched successfully")
                
            except Exception as e:
                logger.error(f"[ERROR] Failed to fetch testing data: {e}", exc_info=True)
                return False
        else:
            # Regular race weekend - check FastF1 telemetry availability
            telemetry_attempts = 0
            max_telemetry_checks = 12  # 12 checks × 5 minutes = 1 hour
            
            while telemetry_attempts < max_telemetry_checks:
                telemetry_attempts += 1
                logger.info(f"Checking telemetry availability (attempt {telemetry_attempts}/{max_telemetry_checks})...")
                
                if self.check_telemetry_available(event_name, session_type):
                    logger.info(f"[OK] Telemetry ready! Starting pipeline execution...")
                    break
                
                if telemetry_attempts < max_telemetry_checks:
                    logger.info(f"[WAIT] Telemetry not ready. Checking again in {self.check_interval_minutes} minutes...")
                    time.sleep(self.check_interval_minutes * 60)
            else:
                logger.error(f"[ERROR] Telemetry not available after {max_telemetry_checks * self.check_interval_minutes} minutes")
                return False
        
        # Now attempt to run the pipeline
        for attempt in range(1, max_retries + 1):
            try:
                # Run the pipeline
                # Run MASTER PIPELINE (includes telemetry + analytics + ML)
                result = subprocess.run(
                    ['python', 'master_pipeline.py', '--session', event_name.replace(' ', '_') + '_' + session_type],
                    capture_output=True,
                    text=True,
                    timeout=1800  # 30 minute timeout
                )
                
                if result.returncode == 0:
                    logger.info(f"[SUCCESS] Complete pipeline executed for {event_name} - {session_type}")
                    logger.info(f"Output: {result.stdout[-500:]}")  # Last 500 chars
                    return True
                else:
                    logger.error(f"Pipeline failed (attempt {attempt}/{max_retries})")
                    logger.error(f"Error: {result.stderr}")
                    
                    if attempt < max_retries:
                        wait_time = self.check_interval_minutes * 60  # Wait 5 minutes between retries
                        logger.info(f"Retrying in {wait_time/60} minutes...")
                        time.sleep(wait_time)
                    
            except subprocess.TimeoutExpired:
                logger.error(f"Pipeline timeout (attempt {attempt}/{max_retries})")
                if attempt < max_retries:
                    logger.info("Retrying...")
                    time.sleep(self.check_interval_minutes * 60)
                    
            except Exception as e:
                logger.error(f"Unexpected error (attempt {attempt}/{max_retries}): {e}")
                if attempt < max_retries:
                    time.sleep(self.check_interval_minutes * 60)
        
        logger.error(f"[ERROR] Pipeline failed after {max_retries} attempts")
        return False
    
    def run_once(self):
        """Check for pending sessions and process them"""
        logger.info("Checking for sessions to process...")
        
        sessions = self.get_sessions_to_process()
        
        if not sessions:
            logger.info("No sessions to process at this time")
            return
        
        logger.info(f"Found {len(sessions)} session(s) to process")
        
        for session in sessions:
            event = session['event']
            session_type = session['session_type']
            
            logger.info(f"\n{'='*70}")
            logger.info(f"Processing: {event} - {session_type}")
            logger.info(f"Session ended: {session['end_time']}")
            logger.info(f"{'='*70}")
            
            # Check if this is a testing session
            is_testing = session.get('is_testing', False)
            
            if is_testing:
                success = self.run_pipeline(
                    event, session_type,
                    is_testing=True,
                    openf1_session_key=session.get('openf1_session_key'),
                    test_number=session.get('test_number'),
                    day=session.get('day'),
                    date=session.get('date')
                )
            else:
                success = self.run_pipeline(event, session_type)
            
            if success:
                self.save_processed_session(event, session_type, datetime.datetime.now(datetime.timezone.utc))
                logger.info(f"[OK] Marked {event} {session_type} as processed")
            else:
                logger.warning(f"[WARNING] {event} {session_type} failed but will retry on next check")
    
    def update_health_check(self):
        """Update health check file for container monitoring"""
        health_file = Path('logs/health.json')
        try:
            health_data = {
                'last_check': datetime.datetime.now(datetime.timezone.utc).isoformat(),
                'status': 'healthy',
                'processed_sessions': len(self.processed_sessions)
            }
            with open(health_file, 'w') as f:
                json.dump(health_data, f)
        except Exception as e:
            logger.warning(f"Failed to update health check: {e}")
    
    def run_continuous(self):
        """Run scheduler in continuous mode with smart sleep based on schedule"""
        logger.info(f"Starting F1 Session Scheduler (Smart Mode)")
        logger.info(f"Data buffer: {int(self.buffer_hours * 60)} minutes after session")
        logger.info(f"Telemetry check interval: {self.check_interval_minutes} minutes")
        logger.info(f"Schedule file: {self.schedule_file}")
        logger.info("Running autonomously - container will manage lifecycle\n")
        
        # Initial health check
        self.update_health_check()
        
        try:
            while True:
                try:
                    # Update health status
                    self.update_health_check()
                    
                    # Run check for current sessions
                    self.run_once()
                    
                    # Calculate when to wake up next
                    next_check = self.get_next_session_check_time()
                    
                    if next_check:
                        hours_until = next_check['hours_until']
                        
                        # If next session is more than 1 hour away, sleep until then
                        # Otherwise, check again in 1 hour
                        if hours_until > 1:
                            sleep_hours = max(hours_until - 0.25, 1)  # Wake 15 min before, min 1 hour
                            sleep_seconds = sleep_hours * 3600
                            
                            logger.info(f"\n{'='*70}")
                            logger.info(f"Next session: {next_check['event']} - {next_check['session']}")
                            logger.info(f"Session ends: {next_check['end_time'].strftime('%Y-%m-%d %H:%M UTC')}")
                            logger.info(f"Will check at: {next_check['check_time'].strftime('%Y-%m-%d %H:%M UTC')}")
                            logger.info(f"Sleeping for: {sleep_hours:.1f} hours")
                            logger.info(f"Wake up time: {(datetime.datetime.now() + datetime.timedelta(seconds=sleep_seconds)).strftime('%Y-%m-%d %H:%M:%S')}")
                            logger.info(f"{'='*70}\n")
                            
                            time.sleep(sleep_seconds)
                        else:
                            # Session is soon, check every hour
                            logger.info(f"\n{next_check['event']} - {next_check['session']} within 1 hour - checking again in 1 hour...")
                            time.sleep(3600)
                    else:
                        # No upcoming sessions in next 30 days, check daily
                        logger.info(f"\nNo sessions scheduled in next 30 days")
                        logger.info(f"Checking again in 24 hours...")
                        time.sleep(86400)
                
                except Exception as e:
                    logger.error(f"Error in scheduler loop: {e}", exc_info=True)
                    # On error, wait 1 hour before retrying
                    time.sleep(3600)
                
        except KeyboardInterrupt:
            logger.info("\n\nScheduler stopped by user")

    def _detect_and_save_event_format(self, event_name: str, event_row):
        """Infer event format from SessionN name fields and persist to logs/detected_event_formats.json.

        Example mappings detected:
          - ['Practice 1','Sprint Qualifying','Sprint','Qualifying','Race'] -> 'sprint_qualifying'
          - ['Practice 1','Practice 2','Practice 3','Qualifying','Race'] -> 'conventional'
          - ['Practice 1','Practice 2','Practice 3'] -> 'testing' (no qualifying/race)
        """
        try:
            # Read session name strings from Session1..Session5 if present
            tags = []
            for i in range(1, 6):
                col = f"Session{i}"
                if col in event_row.columns:
                    val = event_row[col].iloc[0]
                    if isinstance(val, str) and val.strip():
                        v = val.strip().lower()
                        # Map common names to short tags
                        if 'practice 1' in v or (v.startswith('practice') and '1' in v):
                            tags.append('FP1')
                        elif 'practice 2' in v or (v.startswith('practice') and '2' in v):
                            tags.append('FP2')
                        elif 'practice 3' in v or (v.startswith('practice') and '3' in v):
                            tags.append('FP3')
                        elif 'sprint qualifying' in v or 'sprint shootout' in v or 'sprintqualifying' in v:
                            tags.append('SQ')
                        elif 'sprint qualifying' in v or 'sprintqualifying' in v:
                            tags.append('SQ')
                        elif 'sprint' in v and 'qual' not in v and 'shootout' not in v:
                            tags.append('S')
                        elif 'qualifying' in v:
                            tags.append('Q')
                        elif 'race' in v:
                            tags.append('R')
                        else:
                            # Fallback: try to detect 'practice' occurrences
                            if 'practice' in v:
                                # map to FP generic if number missing
                                if '1' in v:
                                    tags.append('FP1')
                                elif '2' in v:
                                    tags.append('FP2')
                                elif '3' in v:
                                    tags.append('FP3')
                                else:
                                    tags.append('FP')
                            else:
                                tags.append(v.upper())

            # Normalize tags sequence
            normalized = [t for t in tags]

            # Load known formats
            fmt_file = Path('config/event_formats.json')
            try:
                if fmt_file.exists():
                    with open(fmt_file, 'r', encoding='utf-8') as f:
                        formats = json.load(f)
                else:
                    formats = {}
            except Exception:
                formats = {}

            detected = None
            
            # Special detection for testing: only practice sessions, no qualifying or race
            has_practice = any(t.startswith('FP') for t in normalized)
            has_race = 'R' in normalized
            has_qualifying = 'Q' in normalized or 'SQ' in normalized
            
            if has_practice and not has_race and not has_qualifying:
                detected = 'testing'
            else:
                # Match against known formats
                for name, seq in formats.items():
                    if name == 'testing':  # Skip testing in format matching
                        continue
                    if len(seq) == len(normalized) and all(a == b or (a.startswith('FP') and b.startswith('FP')) for a, b in zip(seq, normalized)):
                        detected = name
                        break

            # Persist detection
            out_file = Path('logs/detected_event_formats.json')
            try:
                if out_file.exists():
                    with open(out_file, 'r', encoding='utf-8') as f:
                        existing = json.load(f)
                else:
                    existing = {}
            except Exception:
                existing = {}

            existing[event_name] = {
                'detected_format': detected or 'unknown',
                'tags': normalized,
                'detected_at': datetime.datetime.now(datetime.timezone.utc).isoformat()
            }

            try:
                out_file.parent.mkdir(parents=True, exist_ok=True)
                with open(out_file, 'w', encoding='utf-8') as f:
                    json.dump(existing, f, indent=2)
            except Exception:
                pass
        except Exception:
            return


def main():
    import argparse
    
    parser = argparse.ArgumentParser(description='F1 Session Scheduler')
    parser.add_argument('--mode', choices=['once', 'continuous'], default='continuous',
                      help='Run once or in continuous mode (default: continuous)')
    parser.add_argument('--buffer', type=float, default=0.167,
                      help='Hours to wait after session end for data availability (default: 0.167 = 10 minutes)')
    parser.add_argument('--check-interval', type=int, default=5,
                      help='Minutes between telemetry availability checks (default: 5)')
    parser.add_argument('--schedule', type=str, default='schedule/schedule_2026.json',
                      help='Path to schedule JSON file')
    parser.add_argument('--enable-testing', action='store_true',
                      help='Enable monitoring and processing of testing sessions (OpenF1 data)')
    
    args = parser.parse_args()
    
    scheduler = F1SessionScheduler(
        schedule_file=args.schedule,
        buffer_hours=args.buffer,
        check_interval_minutes=args.check_interval,
        enable_testing=args.enable_testing
    )
    
    logger.info(f"Starting F1 Session Scheduler")
    logger.info(f"Mode: {args.mode}")
    logger.info(f"Buffer: {args.buffer} hours")
    logger.info(f"Check interval: {args.check_interval} minutes")
    logger.info(f"Testing sessions: {'ENABLED' if args.enable_testing else 'DISABLED'}")
    
    if args.mode == 'once':
        scheduler.run_once()
    else:
        scheduler.run_continuous()


if __name__ == '__main__':
    main()
