"""
Logging and validation module for F1 telemetry pipeline
"""
import logging
import json
import datetime
from pathlib import Path
from typing import Dict, List, Optional
import sys


class PipelineLogger:
    """Centralized logging for the F1 pipeline"""
    
    def __init__(self, log_name='pipeline'):
        self.log_dir = Path('logs')
        self.log_dir.mkdir(exist_ok=True)
        
        # Create logger
        self.logger = logging.getLogger(log_name)
        self.logger.setLevel(logging.INFO)
        
        # Remove existing handlers
        self.logger.handlers.clear()
        
        # File handler - daily log file
        log_file = self.log_dir / f'{log_name}_{datetime.datetime.now().strftime("%Y%m%d")}.log'
        file_handler = logging.FileHandler(log_file)
        file_handler.setLevel(logging.DEBUG)
        
        # Console handler
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(logging.INFO)
        
        # Formatter
        formatter = logging.Formatter(
            '%(asctime)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        file_handler.setFormatter(formatter)
        console_handler.setFormatter(formatter)
        
        self.logger.addHandler(file_handler)
        self.logger.addHandler(console_handler)
        
        # Run statistics
        self.run_stats = {
            'start_time': datetime.datetime.now().isoformat(),
            'event': None,
            'session': None,
            'drivers_processed': 0,
            'tables_created': 0,
            'total_rows': 0,
            'errors': [],
            'warnings': []
        }
    
    def info(self, message):
        self.logger.info(message)
    
    def warning(self, message):
        self.logger.warning(message)
        self.run_stats['warnings'].append(message)
    
    def error(self, message):
        self.logger.error(message)
        self.run_stats['errors'].append(message)
    
    def debug(self, message):
        self.logger.debug(message)
    
    def set_session_info(self, event_name, session_name, year):
        """Record session information"""
        self.run_stats['event'] = event_name
        self.run_stats['session'] = session_name
        self.run_stats['year'] = year
    
    def increment_drivers(self):
        self.run_stats['drivers_processed'] += 1
    
    def increment_tables(self, row_count=0):
        self.run_stats['tables_created'] += 1
        self.run_stats['total_rows'] += row_count
    
    def save_run_summary(self):
        """Save summary of the pipeline run"""
        self.run_stats['end_time'] = datetime.datetime.now().isoformat()
        
        summary_file = self.log_dir / f'run_summary_{datetime.datetime.now().strftime("%Y%m%d_%H%M%S")}.json'
        
        try:
            with open(summary_file, 'w') as f:
                json.dump(self.run_stats, f, indent=2)
            
            self.info(f"Run summary saved: {summary_file}")
        except Exception as e:
            self.error(f"Failed to save run summary: {e}")
    
    def get_summary(self) -> Dict:
        """Get current run statistics"""
        return self.run_stats.copy()


class DataValidator:
    """Validates pipeline data quality"""
    
    def __init__(self, logger: PipelineLogger):
        self.logger = logger
        self.min_expected_drivers = 18  # F1 grid should have at least 18 drivers
        self.min_expected_laps = 10  # Minimum laps per session
    
    def validate_session_data(self, drivers_count: int, total_laps: int) -> bool:
        """Validate session data quality"""
        is_valid = True
        
        # Check driver count
        if drivers_count < self.min_expected_drivers:
            self.logger.warning(
                f"Low driver count: {drivers_count} (expected >= {self.min_expected_drivers})"
            )
            is_valid = False
        else:
            self.logger.info(f"[OK] Driver count OK: {drivers_count}")
        
        # Check lap count
        if total_laps < self.min_expected_laps:
            self.logger.warning(
                f"Low lap count: {total_laps} (expected >= {self.min_expected_laps})"
            )
            is_valid = False
        else:
            self.logger.info(f"[OK] Lap count OK: {total_laps:,}")
        
        return is_valid
    
    def validate_driver_data(self, driver: str, laps_count: int, telemetry_count: int) -> bool:
        """Validate individual driver data"""
        if laps_count == 0:
            self.logger.warning(f"{driver}: No laps recorded")
            return False
        
        if telemetry_count == 0:
            self.logger.warning(f"{driver}: No telemetry data")
            return False
        
        return True
    
    def validate_snowflake_load(self, table_name: str, row_count: int) -> bool:
        """Validate data was loaded to Snowflake"""
        if row_count == 0:
            self.logger.warning(f"{table_name}: No rows loaded to Snowflake")
            return False
        
        self.logger.info(f"[OK] {table_name}: {row_count:,} rows loaded")
        return True


class NotificationService:
    """Send notifications about pipeline runs"""
    
    def __init__(self, logger: PipelineLogger):
        self.logger = logger
    
    def send_success_notification(self, event: str, session: str, stats: Dict):
        """Send success notification (placeholder for email/Slack integration)"""
        message = f"""
[SUCCESS] F1 Pipeline Success
Event: {event}
Session: {session}
Drivers: {stats['drivers_processed']}
Tables: {stats['tables_created']}
Total Rows: {stats['total_rows']:,}
"""
        self.logger.info(message)
        
        # TODO: Implement email/Slack webhook here
        # Example: self._send_email(message) or self._send_slack(message)
    
    def send_failure_notification(self, event: str, session: str, error: str):
        """Send failure notification"""
        message = f"""
[ERROR] F1 Pipeline Failed
Event: {event}
Session: {session}
Error: {error}
"""
        self.logger.error(message)
        
        # TODO: Implement email/Slack webhook here


# Global logger instance
_global_logger: Optional[PipelineLogger] = None


def get_logger() -> PipelineLogger:
    """Get or create global logger instance"""
    global _global_logger
    if _global_logger is None:
        _global_logger = PipelineLogger()
    return _global_logger


def reset_logger():
    """Reset global logger (useful for testing)"""
    global _global_logger
    _global_logger = None
