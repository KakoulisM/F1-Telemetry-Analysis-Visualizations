"""
OpenF1 Data Fetcher for Testing Sessions and Telemetry Fallback
Fetches lap data from OpenF1 API and formats for pipeline compatibility.

Also provides a fallback for race-weekend sessions when FastF1 car telemetry
is unavailable: fetch car_data and location from OpenF1 instead.
"""
import requests
import pandas as pd
import json
from pathlib import Path
from typing import Optional, Dict, List
import logging

logger = logging.getLogger(__name__)

BASE_URL = "https://api.openf1.org/v1"


# ---------------------------------------------------------------------------
# OpenF1 telemetry fallback for race-weekend sessions
# ---------------------------------------------------------------------------

def _openf1_get(endpoint: str, params: dict, timeout: int = 30) -> list:
    """GET from OpenF1 API, return list of records or []."""
    try:
        url = f"{BASE_URL}/{endpoint}"
        resp = requests.get(url, params=params, timeout=timeout)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.warning(f"OpenF1 request failed ({endpoint} {params}): {e}")
        return []


def find_openf1_session_key(year: int, location: str, session_name: str) -> Optional[int]:
    """
    Look up the OpenF1 session_key for a FastF1 session.

    Args:
        year: e.g. 2026
        location: city/circuit name from FastF1 session.event['Location'], e.g. 'Melbourne'
        session_name: FastF1 session.name, e.g. 'Practice 3'

    Returns:
        session_key int, or None if not found.
    """
    records = _openf1_get("sessions", {"year": year, "session_name": session_name})
    if not records:
        # Try with year only and filter manually
        records = _openf1_get("sessions", {"year": year})

    if not records:
        return None

    # Match by location (city name, case-insensitive substring)
    loc_lower = location.lower()
    for rec in records:
        rec_loc = str(rec.get("location", "") or rec.get("circuit_short_name", "") or "").lower()
        rec_sname = str(rec.get("session_name", "")).lower()
        if loc_lower in rec_loc or rec_loc in loc_lower:
            if session_name.lower() in rec_sname or rec_sname in session_name.lower():
                key = rec.get("session_key")
                if key is not None:
                    logger.info(f"[OpenF1] Found session_key={key} for {year} {location} {session_name}")
                    return int(key)

    # Fallback: first record matching session name
    for rec in records:
        rec_sname = str(rec.get("session_name", "")).lower()
        if session_name.lower() in rec_sname:
            key = rec.get("session_key")
            if key is not None:
                logger.info(f"[OpenF1] Fallback session_key={key} for {year} {session_name}")
                return int(key)

    return None


def fetch_openf1_car_data(session_key: int, driver_number: int,
                          session_start_utc: pd.Timestamp) -> Optional[pd.DataFrame]:
    """
    Fetch car telemetry from OpenF1 and return a FastF1-compatible DataFrame.

    FastF1 car_data columns used downstream:
        SessionTime (timedelta), Speed, RPM, nGear, Throttle, Brake, DRS

    Args:
        session_key: OpenF1 session key
        driver_number: driver car number (int)
        session_start_utc: UTC timestamp of session start, used to compute SessionTime

    Returns:
        DataFrame with FastF1-compatible columns, or None on failure.
    """
    records = _openf1_get("car_data",
                          {"session_key": session_key, "driver_number": driver_number},
                          timeout=60)
    if not records:
        return None

    df = pd.DataFrame(records)
    if df.empty or "date" not in df.columns:
        return None

    df["date"] = pd.to_datetime(df["date"], utc=True, errors="coerce")
    df = df.dropna(subset=["date"]).sort_values("date")

    # Compute SessionTime as timedelta from session start
    df["SessionTime"] = df["date"] - session_start_utc
    df["Date"] = df["date"]

    # Rename to FastF1 conventions
    rename = {
        "speed": "Speed",
        "rpm": "RPM",
        "n_gear": "nGear",
        "throttle": "Throttle",
        "brake": "Brake",
        "drs": "DRS",
    }
    df = df.rename(columns={k: v for k, v in rename.items() if k in df.columns})

    keep = ["SessionTime", "Date", "Speed", "RPM", "nGear", "Throttle", "Brake", "DRS"]
    keep = [c for c in keep if c in df.columns]
    return df[keep].reset_index(drop=True)


def fetch_openf1_pos_data(session_key: int, driver_number: int,
                          session_start_utc: pd.Timestamp) -> Optional[pd.DataFrame]:
    """
    Fetch location (X/Y/Z) from OpenF1 and return a FastF1-compatible DataFrame.

    FastF1 pos_data columns used downstream:
        SessionTime (timedelta), X, Y, Z, Status

    Returns:
        DataFrame with FastF1-compatible columns, or None on failure.
    """
    records = _openf1_get("location",
                          {"session_key": session_key, "driver_number": driver_number},
                          timeout=60)
    if not records:
        return None

    df = pd.DataFrame(records)
    if df.empty or "date" not in df.columns:
        return None

    df["date"] = pd.to_datetime(df["date"], utc=True, errors="coerce")
    df = df.dropna(subset=["date"]).sort_values("date")

    df["SessionTime"] = df["date"] - session_start_utc

    rename = {"x": "X", "y": "Y", "z": "Z"}
    df = df.rename(columns={k: v for k, v in rename.items() if k in df.columns})

    if "Status" not in df.columns:
        df["Status"] = "OnTrack"

    keep = ["SessionTime", "X", "Y", "Z", "Status"]
    keep = [c for c in keep if c in df.columns]
    return df[keep].reset_index(drop=True)


def build_openf1_telemetry_dicts(fastf1_session,
                                  driver_numbers: dict) -> tuple:
    """
    Fallback: fetch car_data and pos_data from OpenF1 for all drivers,
    returning dicts in the same format FastF1 would produce.

    Args:
        fastf1_session: loaded FastF1 session object
        driver_numbers: dict mapping driver abbreviation -> driver number string

    Returns:
        (car_data_dict, pos_data_dict) — dicts keyed by driver number string.
        Both may be empty if OpenF1 data is unavailable.
    """
    try:
        year = int(fastf1_session.event.year)
        location = fastf1_session.event.get("Location", "")
        session_name = fastf1_session.name  # e.g. "Practice 3"
    except Exception as e:
        logger.warning(f"[OpenF1 fallback] Could not read session metadata: {e}")
        return {}, {}

    logger.info(f"[OpenF1 fallback] Looking up session key for {year} {location} {session_name}")
    session_key = find_openf1_session_key(year, location, session_name)

    if session_key is None:
        logger.warning(f"[OpenF1 fallback] Could not find session_key — telemetry unavailable")
        return {}, {}

    logger.info(f"[OpenF1 fallback] session_key={session_key}, fetching telemetry for {len(driver_numbers)} drivers")

    # Determine session start UTC for SessionTime computation
    try:
        # FastF1 session.date is the session start datetime (UTC-aware or naive)
        session_start = pd.Timestamp(fastf1_session.date)
        if session_start.tzinfo is None:
            session_start = session_start.tz_localize("UTC")
        else:
            session_start = session_start.tz_convert("UTC")
    except Exception:
        # Fallback: use first lap's LapStartDate
        try:
            session_start = pd.to_datetime(
                fastf1_session.laps["LapStartDate"].dropna().iloc[0], utc=True
            )
        except Exception:
            logger.warning("[OpenF1 fallback] Could not determine session start time")
            return {}, {}

    car_data_dict = {}
    pos_data_dict = {}

    for driver_abbr, driver_num_str in driver_numbers.items():
        try:
            driver_num = int(driver_num_str)
        except (ValueError, TypeError):
            continue

        car_df = fetch_openf1_car_data(session_key, driver_num, session_start)
        if car_df is not None and not car_df.empty:
            car_data_dict[driver_num_str] = car_df
            logger.info(f"[OpenF1 fallback]   {driver_abbr}: {len(car_df)} car_data rows")
        else:
            logger.info(f"[OpenF1 fallback]   {driver_abbr}: no car_data")

        pos_df = fetch_openf1_pos_data(session_key, driver_num, session_start)
        if pos_df is not None and not pos_df.empty:
            pos_data_dict[driver_num_str] = pos_df
        else:
            logger.info(f"[OpenF1 fallback]   {driver_abbr}: no location data")

    logger.info(f"[OpenF1 fallback] Fetched car_data for {len(car_data_dict)}/{len(driver_numbers)} drivers")
    return car_data_dict, pos_data_dict


class OpenF1Fetcher:
    """Fetch and format testing session data from OpenF1 API"""
    
    BASE_URL = "https://api.openf1.org/v1"
    
    def __init__(self, output_dir: str = "telemetry_out"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(exist_ok=True)
        
    def fetch_session(self, session_key: int, test_number: int, day: int, 
                     event_name: str, date: str) -> bool:
        """
        Fetch testing session data from OpenF1
        
        Args:
            session_key: OpenF1 session key
            test_number: Test number (1 or 2)
            day: Day of test (1, 2, or 3)
            event_name: Event name for metadata
            date: Session date YYYY-MM-DD
            
        Returns:
            True if data fetched successfully, False otherwise
        """
        logger.info(f"Fetching OpenF1 data for session {session_key} ({event_name} - Day {day})")
        
        try:
            # Fetch session metadata
            session_info = self._fetch_session_info(session_key)
            if not session_info:
                logger.error(f"No session info available for {session_key}")
                return False
            
            # Fetch drivers
            drivers = self._fetch_drivers(session_key)
            if not drivers:
                logger.error(f"No driver data available for {session_key}")
                return False
            
            # Fetch laps
            laps = self._fetch_laps(session_key)
            if not laps:
                logger.error(f"No lap data available for {session_key}")
                return False
            
            logger.info(f"Fetched {len(drivers)} drivers and {len(laps)} laps")
            
            # Create driver mapping
            driver_map = {d['driver_number']: d for d in drivers}
            
            # Process and save data
            self._save_session_metadata(session_info, test_number, day, event_name, date)
            self._process_and_save_laps(laps, driver_map, date, event_name, day)
            
            logger.info(f"Successfully processed OpenF1 data for session {session_key}")
            return True
            
        except Exception as e:
            logger.error(f"Error fetching OpenF1 data: {e}", exc_info=True)
            return False
    
    def _fetch_session_info(self, session_key: int) -> Optional[Dict]:
        """Fetch session metadata"""
        try:
            url = f"{self.BASE_URL}/sessions?session_key={session_key}"
            response = requests.get(url, timeout=10)
            response.raise_for_status()
            data = response.json()
            return data[0] if data else None
        except Exception as e:
            logger.error(f"Failed to fetch session info: {e}")
            return None
    
    def _fetch_drivers(self, session_key: int) -> List[Dict]:
        """Fetch driver list"""
        try:
            url = f"{self.BASE_URL}/drivers?session_key={session_key}"
            response = requests.get(url, timeout=10)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"Failed to fetch drivers: {e}")
            return []
    
    def _fetch_laps(self, session_key: int) -> List[Dict]:
        """Fetch all laps"""
        try:
            url = f"{self.BASE_URL}/laps?session_key={session_key}"
            response = requests.get(url, timeout=30)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"Failed to fetch laps: {e}")
            return []
    
    def _save_session_metadata(self, session_info: Dict, test_number: int, 
                               day: int, event_name: str, date: str):
        """Save session metadata JSON"""
        year = int(date.split('-')[0])
        
        # Match FastF1 metadata format
        metadata = {
            "event_name": event_name,
            "session_name": f"Testing Day {day}",
            "date": date,
            "year": year,
            "country": "Bahrain",
            "location": session_info.get('location', 'Bahrain International Circuit'),
            "round": 0,  # Testing sessions have round 0
            # OpenF1-specific metadata
            "source": "OpenF1",
            "openf1_session_key": session_info.get('session_key'),
            "test_number": test_number,
            "day": day,
            "session_start": session_info.get('date_start'),
            "session_end": session_info.get('date_end'),
            "session_type": "Testing"
        }
        
        meta_file = self.output_dir / 'session_meta.json'
        with open(meta_file, 'w') as f:
            json.dump(metadata, f, indent=2)
        
        logger.info(f"Saved session metadata to {meta_file}")
    
    def _process_and_save_laps(self, laps: List[Dict], driver_map: Dict, date: str, 
                               event_name: str, day: int):
        """Process laps and save per-driver CSV files compatible with pipeline"""
        # Save raw payload for debugging
        try:
            raw_dir = self.output_dir / 'openf1_raw'
            raw_dir.mkdir(exist_ok=True)
            raw_file = raw_dir / f'session_{str(laps[0].get("session_key") if laps else "unknown")}.json'
            with open(raw_file, 'w') as rf:
                json.dump(laps, rf, indent=2)
            logger.info(f"Saved raw OpenF1 payload to {raw_file}")
        except Exception:
            logger.debug("Failed to save raw OpenF1 payload", exc_info=True)

        # Try to extract any tyre/compound-like keys from raw lap dicts and attach as helper fields
        for lap in laps:
            extracted = None
            fresh = False
            try:
                for k, v in list(lap.items()):
                    lk = str(k).lower()
                    # direct string fields containing tyre/compound
                    if 'compound' in lk or 'tyre' in lk:
                        # skip generic keys like 'tyre_temperature' by preferring short values
                        if isinstance(v, str) and len(v) > 0:
                            extracted = v
                            break
                        if isinstance(v, (int, float)):
                            extracted = str(v)
                            break
                        if isinstance(v, dict):
                            # nested structures
                            for subk in ('compound', 'name', 'type'):
                                if subk in v:
                                    extracted = v.get(subk)
                                    break
                            if extracted:
                                break
                    # fresh tyre flags
                    if lk in ('freshtyre', 'fresh_tyre', 'fresh_tires', 'fresh_tire'):
                        try:
                            fresh = bool(v)
                        except Exception:
                            fresh = False
                # fallback: some payloads use 'tyres' as list
                if extracted is None and 'tyres' in lap and isinstance(lap['tyres'], (list, tuple)) and len(lap['tyres'])>0:
                    t = lap['tyres'][0]
                    if isinstance(t, str):
                        extracted = t
                    elif isinstance(t, dict):
                        extracted = t.get('compound') or t.get('name')
            except Exception:
                pass
            lap['_ex_compound'] = extracted
            lap['_ex_fresh'] = fresh

        # Convert to DataFrame
        df = pd.DataFrame(laps)
        
        if df.empty:
            logger.warning("No lap data to process")
            return

        # Persist raw OpenF1 payloads for debugging / future fields
        try:
            session_key = int(df['session_key'].iloc[0]) if 'session_key' in df.columns else None
        except Exception:
            session_key = None

        try:
            raw_dir = self.output_dir / 'raw_openf1'
            raw_dir.mkdir(exist_ok=True)
            full_path = raw_dir / (f'session_{session_key}_laps.json' if session_key else 'session_laps.json')
            with open(full_path, 'w', encoding='utf-8') as rf:
                json.dump(laps, rf, indent=2, default=str)
            logger.info(f"Saved raw OpenF1 session payload to {full_path}")
        except Exception:
            logger.debug('Failed to save raw OpenF1 payload', exc_info=True)
        
        # Parse timestamps
        if 'date_start' in df.columns:
            df['date_start'] = pd.to_datetime(df['date_start'], format='mixed', errors='coerce')
        
        # Convert duration columns from seconds to timedelta
        duration_cols = ['lap_duration', 'duration_sector_1', 'duration_sector_2', 'duration_sector_3']
        for col in duration_cols:
            if col in df.columns:
                df[col] = pd.to_timedelta(df[col], unit='s', errors='coerce')
        
        # Group by driver
        for driver_num in df['driver_number'].unique():
            driver_laps = df[df['driver_number'] == driver_num].copy()
            
            # Get driver info
            driver_info = driver_map.get(driver_num, {})
            driver_abbr = driver_info.get('name_acronym', f'DRV{driver_num}')
            team = driver_info.get('team_name', 'Unknown')
            
            # Sort by lap number to ensure order
            driver_laps = driver_laps.sort_values('lap_number')

            # Save raw per-driver payload for inspection
            try:
                if session_key is not None:
                    per_path = raw_dir / f'session_{session_key}_driver_{driver_num}.json'
                else:
                    per_path = raw_dir / f'driver_{driver_num}.json'
                with open(per_path, 'w', encoding='utf-8') as pf:
                    json.dump(driver_laps.to_dict(orient='records'), pf, indent=2, default=str)
            except Exception:
                logger.debug('Failed to save per-driver raw payload', exc_info=True)
            
            # Calculate session time (cumulative time from start)
            # Use date_start as reference for session time calculation
            if 'date_start' in driver_laps.columns and driver_laps['date_start'].notna().any():
                first_lap_time = driver_laps['date_start'].min()
                driver_laps['Time'] = (driver_laps['date_start'] - first_lap_time).dt.floor('ms')
            else:
                driver_laps['Time'] = pd.NaT
            
            # Attempt to extract compound/tyre info if present in API (case-insensitive)
            compound_val = pd.NA
            tyre_life_val = pd.NA
            fresh_tyre_val = False
            try:
                # candidate keys in possible API payloads
                candidate_compound_cols = ['compound', 'tyre', 'tyre_compound', 'tyre_compound_short', 'compound_name', 'compound_short']
                lc_cols = {c.lower(): c for c in driver_laps.columns}
                for cand in candidate_compound_cols:
                    if cand in lc_cols:
                        colname = lc_cols[cand]
                        mode = driver_laps[colname].dropna().astype(str).str.strip().mode()
                        if len(mode) > 0:
                            compound_val = str(mode.iloc[0])
                            break
                # tyre life
                for cand in ['tyrelife', 'tyre_life', 'tyre_lifespan']:
                    if cand in lc_cols:
                        colname = lc_cols[cand]
                        try:
                            tyre_life_val = pd.to_numeric(driver_laps[colname], errors='coerce').median()
                        except Exception:
                            tyre_life_val = pd.NA
                        break
                # fresh tyre flag
                for cand in ['freshtyre', 'fresh_tyre', 'fresh', 'is_fresh_tyre']:
                    if cand in lc_cols:
                        colname = lc_cols[cand]
                        try:
                            fresh_tyre_val = driver_laps[colname].astype(bool).any()
                        except Exception:
                            fresh_tyre_val = False
                        break
            except Exception:
                compound_val = pd.NA
                tyre_life_val = pd.NA
                fresh_tyre_val = False

            # Create FastF1-compatible DataFrame structure
            lap_data = pd.DataFrame({
                # Session time when lap finished
                'Time': driver_laps['Time'],
                
                # Driver identification
                'Driver': driver_abbr,
                'DriverNumber': driver_num,
                
                # Lap timing
                'LapTime': driver_laps['lap_duration'],
                'LapNumber': driver_laps['lap_number'].astype(float),
                
                # Stint info - OpenF1 doesn't provide this, estimate from pit laps
                'Stint': pd.NA,
                
                # Pit times - OpenF1 doesn't provide exact pit times (use timedelta dtype)
                'PitOutTime': pd.Series(pd.NaT, index=driver_laps.index, dtype='timedelta64[ns]'),
                'PitInTime': pd.Series(pd.NaT, index=driver_laps.index, dtype='timedelta64[ns]'),
                
                # Sector times
                'Sector1Time': driver_laps['duration_sector_1'],
                'Sector2Time': driver_laps['duration_sector_2'],
                'Sector3Time': driver_laps['duration_sector_3'],
                
                # Sector session times - calculate from lap time (use timedelta dtype)
                'Sector1SessionTime': pd.Series(pd.NaT, index=driver_laps.index, dtype='timedelta64[ns]'),
                'Sector2SessionTime': pd.Series(pd.NaT, index=driver_laps.index, dtype='timedelta64[ns]'),
                'Sector3SessionTime': pd.Series(pd.NaT, index=driver_laps.index, dtype='timedelta64[ns]'),
                
                # Speed traps
                'SpeedI1': driver_laps.get('i1_speed', pd.NA),
                'SpeedI2': driver_laps.get('i2_speed', pd.NA),
                'SpeedST': driver_laps.get('st_speed', pd.NA),
                
                # Best lap flag
                'IsPersonalBest': False,  # Will calculate after
                
                # Tire info - fill from API if available else NA
                'Compound': compound_val if compound_val is not pd.NA else pd.NA,
                'TyreLife': tyre_life_val if tyre_life_val is not pd.NA else pd.NA,
                'FreshTyre': bool(fresh_tyre_val),
                
                # Team
                'Team': team,
                
                # Lap start timing (use timedelta dtype for session time)
                'LapStartTime': pd.Series(pd.NaT, index=driver_laps.index, dtype='timedelta64[ns]'),
                'LapStartDate': pd.to_datetime(date),
                
                # Track status - OpenF1 doesn't provide this
                'TrackStatus': pd.NA,
                
                # Position - OpenF1 doesn't provide this
                'Position': pd.NA,
                
                # Deleted laps
                'Deleted': False,
                'DeletedReason': pd.NA,
                
                # Metadata flags
                'FastF1Generated': False,
                'IsAccurate': True,  # OpenF1 data is considered accurate
            })
            
            # Calculate stint numbers based on pit out laps
            if 'is_pit_out_lap' in driver_laps.columns:
                pit_out_mask = driver_laps['is_pit_out_lap'].fillna(False)
                lap_data['Stint'] = pit_out_mask.cumsum().fillna(1).astype(float)
                # Mark pit out laps
                lap_data.loc[pit_out_mask, 'PitOutTime'] = lap_data.loc[pit_out_mask, 'Time']
            else:
                lap_data['Stint'] = 1.0
            
            # Calculate session times for sectors
            if lap_data['Time'].notna().any() and lap_data['Sector3Time'].notna().any():
                # Sector3SessionTime = Time (lap finish time)
                lap_data['Sector3SessionTime'] = lap_data['Time']
                
                # Sector2SessionTime = Time - Sector3Time
                lap_data['Sector2SessionTime'] = lap_data['Time'] - lap_data['Sector3Time']
                
                # Sector1SessionTime = Sector2SessionTime - Sector2Time
                mask = lap_data['Sector2Time'].notna()
                lap_data.loc[mask, 'Sector1SessionTime'] = (
                    lap_data.loc[mask, 'Sector2SessionTime'] - lap_data.loc[mask, 'Sector2Time']
                )
            
            # Calculate LapStartTime (Time - LapTime)
            mask = lap_data['LapTime'].notna() & lap_data['Time'].notna()
            lap_data.loc[mask, 'LapStartTime'] = lap_data.loc[mask, 'Time'] - lap_data.loc[mask, 'LapTime']
            
            # Mark personal best lap (fastest valid lap)
            valid_laps = lap_data['LapTime'].notna()
            if valid_laps.any():
                fastest_idx = lap_data.loc[valid_laps, 'LapTime'].idxmin()
                lap_data.loc[fastest_idx, 'IsPersonalBest'] = True
            
            # Convert timedelta columns to string format matching FastF1
            timedelta_cols = [
                'Time', 'LapTime', 'PitOutTime', 'PitInTime',
                'Sector1Time', 'Sector2Time', 'Sector3Time',
                'Sector1SessionTime', 'Sector2SessionTime', 'Sector3SessionTime',
                'LapStartTime'
            ]
            
            for col in timedelta_cols:
                if col in lap_data.columns:
                    # Convert to string, handling NaT values
                    lap_data[col] = lap_data[col].apply(
                        lambda x: str(x) if pd.notna(x) else 'NaT'
                    )
            
            # Convert date to string
            lap_data['LapStartDate'] = lap_data['LapStartDate'].astype(str)
            
            # Save to CSV matching FastF1 filename format
            # Format: {EventName}_{SessionName}_{DriverAbbr}_laps.csv
            event_formatted = event_name.replace(' ', '_').replace('-', '_')
            session_formatted = f"Testing_Day_{day}"
            filename = f"{event_formatted}_{session_formatted}_{driver_abbr}_laps.csv"
            filepath = self.output_dir / filename
            lap_data.to_csv(filepath, index=False)
            logger.info(f"Saved {len(lap_data)} laps for {driver_abbr} to {filepath}")
        
        logger.info(f"Processed data for {len(df['driver_number'].unique())} drivers")
    
    def check_data_available(self, session_key: int) -> bool:
        """
        Check if data is available for a session
        
        Note: OpenF1 API doesn't support 'limit' parameter properly for laps endpoint,
        so we check if session exists and has basic info
        
        Returns:
            True if session info is available, False otherwise
        """
        try:
            # Check if session exists (lighter query than fetching all laps)
            url = f"{self.BASE_URL}/sessions?session_key={session_key}"
            response = requests.get(url, timeout=10)
            response.raise_for_status()
            data = response.json()
            # Return True if session info exists (data will likely be available)
            return len(data) > 0
        except Exception as e:
            logger.warning(f"Error checking data availability: {e}")
            return False


def fetch_testing_session(session_key: int, test_number: int, day: int, 
                          event_name: str, date: str, 
                          output_dir: str = "telemetry_out") -> bool:
    """
    Convenience function to fetch a testing session
    
    Args:
        session_key: OpenF1 session key
        test_number: Test number (1 or 2)
        day: Day of test (1, 2, or 3)
        event_name: Event name
        date: Session date YYYY-MM-DD
        output_dir: Output directory for telemetry files
        
    Returns:
        True if successful, False otherwise
    """
    fetcher = OpenF1Fetcher(output_dir)
    return fetcher.fetch_session(session_key, test_number, day, event_name, date)


if __name__ == '__main__':
    # Test with first Bahrain testing session
    import sys
    
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s'
    )
    
    if len(sys.argv) > 1:
        session_key = int(sys.argv[1])
        test_num = int(sys.argv[2]) if len(sys.argv) > 2 else 1
        day = int(sys.argv[3]) if len(sys.argv) > 3 else 1
    else:
        # Default: First session of first test
        session_key = 11465
        test_num = 1
        day = 1
    
    success = fetch_testing_session(
        session_key=session_key,
        test_number=test_num,
        day=day,
        event_name="Pre-Season Testing - Bahrain",
        date="2026-02-11"
    )
    
    if success:
        print(f"✓ Successfully fetched testing data")
    else:
        print(f"✗ Failed to fetch testing data")
        sys.exit(1)
