"""
Live Session Monitor - Real-time telemetry updates during active F1 sessions
Polls FastF1 every 30 seconds and incrementally updates data
"""
import fastf1
import pandas as pd
import time
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from pipeline_logger import get_logger
import os

# Enable cache
cache_dir = 'f1_cache'
os.makedirs(cache_dir, exist_ok=True)
fastf1.Cache.enable_cache(cache_dir)


class LiveSessionMonitor:
    def __init__(self, year, event, session_type, update_interval=30):
        self.year = year
        self.event = event
        self.session_type = session_type
        self.update_interval = update_interval
        self.logger = get_logger()
        self.session = None
        self.last_lap_count = 0
        self.live_data_dir = Path('telemetry_out/live')
        self.live_data_dir.mkdir(parents=True, exist_ok=True)
        
    def load_session(self):
        """Load the F1 session"""
        try:
            self.session = fastf1.get_session(self.year, self.event, self.session_type)
            self.session.load(telemetry=False, weather=False, messages=False)
            self.logger.info(f"Session loaded: {self.session.event['EventName']} {self.session_type}")
            return True
        except Exception as e:
            self.logger.error(f"Failed to load session: {e}")
            return False
    
    def get_live_status(self):
        """Get current session status"""
        try:
            if not self.session:
                return None
            
            # Reload to get latest data
            self.session.load(telemetry=False, weather=False, messages=False)
            
            laps = self.session.laps
            if laps.empty:
                return {
                    'status': 'not_started',
                    'lap_count': 0,
                    'timestamp': datetime.now(timezone.utc).isoformat()
                }
            
            # Get latest lap
            latest_lap = laps['LapNumber'].max()
            new_laps = latest_lap - self.last_lap_count
            
            # Get driver positions
            latest_laps = laps[laps['LapNumber'] == latest_lap]
            positions = latest_laps.sort_values('Position')[['Driver', 'Position', 'LapTime']].to_dict('records')
            
            # Calculate session progress
            total_time = (self.session.session_end_time - self.session.session_start_time).total_seconds()
            elapsed_time = (datetime.now(timezone.utc) - self.session.session_start_time).total_seconds()
            progress = min(100, (elapsed_time / total_time) * 100) if total_time > 0 else 0
            
            status = {
                'status': 'active' if progress < 100 else 'finished',
                'lap_count': int(latest_lap),
                'new_laps': int(new_laps),
                'total_drivers': len(latest_laps),
                'positions': positions[:10],  # Top 10
                'progress': round(progress, 1),
                'timestamp': datetime.now(timezone.utc).isoformat()
            }
            
            self.last_lap_count = latest_lap
            return status
            
        except Exception as e:
            self.logger.error(f"Failed to get live status: {e}")
            return None
    
    def update_live_data(self):
        """Update live data files"""
        try:
            status = self.get_live_status()
            if not status:
                return False
            
            # Save live status
            status_file = self.live_data_dir / 'live_status.json'
            with open(status_file, 'w') as f:
                json.dump(status, f, indent=2)
            
            # Update lap progression
            if status['new_laps'] > 0:
                self.logger.info(f"New laps: {status['new_laps']} | Total: {status['lap_count']}")
                self.save_lap_progression()
            
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to update live data: {e}")
            return False
    
    def save_lap_progression(self):
        """Save current lap progression to file"""
        try:
            laps = self.session.laps
            if laps.empty:
                return
            
            # Create simplified lap data
            lap_data = []
            for _, lap in laps.iterrows():
                lap_data.append({
                    'Driver': lap['Driver'],
                    'LapNumber': int(lap['LapNumber']),
                    'Position': int(lap['Position']) if pd.notna(lap['Position']) else None,
                    'LapTime': str(lap['LapTime']) if pd.notna(lap['LapTime']) else None,
                    'LapTimeSeconds': lap['LapTime'].total_seconds() if pd.notna(lap['LapTime']) else None,
                    'Compound': lap['Compound'] if 'Compound' in lap and pd.notna(lap['Compound']) else None,
                })
            
            # Save to JSON
            output_file = self.live_data_dir / 'live_laps.json'
            with open(output_file, 'w') as f:
                json.dump(lap_data, f)
            
            self.logger.info(f"Saved {len(lap_data)} laps to live_laps.json")
            
        except Exception as e:
            self.logger.error(f"Failed to save lap progression: {e}")
    
    def monitor_loop(self, max_duration_hours=4):
        """Main monitoring loop"""
        self.logger.info(f"Starting live monitor for {self.event} {self.session_type}")
        self.logger.info(f"Update interval: {self.update_interval}s")
        
        if not self.load_session():
            return False
        
        start_time = datetime.now(timezone.utc)
        end_time = start_time + timedelta(hours=max_duration_hours)
        
        iteration = 0
        while datetime.now(timezone.utc) < end_time:
            iteration += 1
            self.logger.info(f"\n=== Update #{iteration} ===")
            
            # Update data
            if self.update_live_data():
                status_file = self.live_data_dir / 'live_status.json'
                if status_file.exists():
                    with open(status_file, 'r') as f:
                        status = json.load(f)
                    
                    self.logger.info(f"Status: {status['status']}")
                    self.logger.info(f"Progress: {status['progress']}%")
                    self.logger.info(f"Laps: {status['lap_count']}")
                    
                    # Stop if session finished
                    if status['status'] == 'finished' and status['progress'] >= 100:
                        self.logger.info("Session finished, stopping monitor")
                        break
            
            # Sleep until next update
            self.logger.info(f"Sleeping {self.update_interval}s until next update...")
            time.sleep(self.update_interval)
        
        self.logger.info("Live monitoring complete")
        return True


def start_live_monitor(year, event, session_type, update_interval=30):
    """Start live session monitoring"""
    monitor = LiveSessionMonitor(year, event, session_type, update_interval)
    return monitor.monitor_loop()


if __name__ == '__main__':
    import sys
    
    # Example: python live_session_monitor.py 2025 "Abu Dhabi Grand Prix" R
    if len(sys.argv) >= 4:
        year = int(sys.argv[1])
        event = sys.argv[2]
        session_type = sys.argv[3]
        interval = int(sys.argv[4]) if len(sys.argv) > 4 else 30
        
        start_live_monitor(year, event, session_type, interval)
    else:
        print("Usage: python live_session_monitor.py <year> <event> <session_type> [interval]")
        print("Example: python live_session_monitor.py 2025 'Abu Dhabi Grand Prix' R 30")
