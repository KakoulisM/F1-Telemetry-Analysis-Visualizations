"""
Master Pipeline Orchestrator
Automated end-to-end F1 analytics pipeline execution
Chains: Telemetry → Warehouse → ML Analytics → Driver Reports

Triggered by scheduler.py after F1 sessions complete (2hr data delay)
"""
import subprocess
import os
import sys
import json
import time
from datetime import datetime
from pathlib import Path
from pipeline_logger import get_logger
from typing import Dict, List, Optional
import pandas as _pd
from pathlib import Path as _Path


class MasterPipeline:
    """Orchestrates complete F1 analytics pipeline execution"""
    
    def __init__(self, session_name: Optional[str] = None, year: Optional[int] = None, 
                 event: Optional[str] = None, session_type: Optional[str] = None,
                 classifier_ignore_isaccurate: bool = True, upload_snowflake: bool = True,
                 is_testing: bool = False):
        self.logger = get_logger()
        self.session_name = session_name
        self.year = year
        self.event = event
        self.session_type = session_type
        self.start_time = datetime.now()
        self.stage_results = {}
        self.python_exe = sys.executable
        self.errors = []
        self.classifier_ignore_isaccurate = classifier_ignore_isaccurate
        self.upload_snowflake = upload_snowflake
        self.is_testing = is_testing

        # If session_name is provided, parse it into event and session_type
        if self.session_name:
            # Example: Monaco_Grand_Prix_Practice_3
            parts = self.session_name.split('_')
            # Find the session type (last part or last two for Sprint Shootout etc)
            # Try to match known session types
            known_types = [
                'Practice_1', 'Practice_2', 'Practice_3', 'Qualifying', 'Race',
                'Sprint', 'Sprint_Shootout', 'Sprint_Qualifying',
                'SprintQualifying', 'SprintRace',
                'FP1', 'FP2', 'FP3', 'Q', 'R', 'S', 'SS', 'SQ'
            ]
            for ktype in sorted(known_types, key=len, reverse=True):
                if self.session_name.endswith(ktype):
                    self.session_type = ktype
                    self.event = self.session_name[:-(len(ktype)+1)]
                    break
            else:
                # Fallback: last part is session type
                self.session_type = parts[-1]
                self.event = '_'.join(parts[:-1])
    
    def get_actual_session_identifier(self) -> Optional[str]:
        """Get the actual session identifier from extracted data (session_meta.json).
        Returns format like: Monaco_Grand_Prix_Practice_3
        """
        _SESSION_TYPE_ALIASES = {
            'FP1': 'Practice_1', 'FP2': 'Practice_2', 'FP3': 'Practice_3',
            'Qualifying': 'Qualifying', 'Race': 'Race', 'Sprint': 'Sprint',
            'Sprint_Shootout': 'Sprint_Shootout', 'Sprint_Qualifying': 'Sprint_Qualifying',
            'SQ': 'Sprint_Qualifying', 'SprintQualifying': 'Sprint_Qualifying',
            'S': 'Sprint', 'SprintRace': 'Sprint',
        }
        try:
            if self.year and self.event and self.session_type:
                event_clean = str(self.event).replace(' ', '_').replace('-', '_')
                raw_type = str(self.session_type).replace(' ', '_').replace('-', '_')
                session_type_clean = _SESSION_TYPE_ALIASES.get(raw_type, raw_type)
                meta_path = Path(f"telemetry_out/{self.year}/{event_clean}/{session_type_clean}/session_meta.json")
                if meta_path.exists():
                    with open(meta_path, 'r', encoding='utf-8') as f:
                        meta = json.load(f)
                        event_name = meta.get('event_name', '').replace(' ', '_')
                        session_name = meta.get('session_name', '').replace(' ', '_')
                        if event_name and session_name:
                            return f"{event_name}_{session_name}"
        except Exception as e:
            self.logger.warning(f"Could not read session identifier from session_meta.json: {e}")
        return None

    def _find_qual_flags_path(self) -> Optional[Path]:
        """Locate the qual_flags.csv for the current session.

        Looks in: telemetry_out/{year}/{event}/{session_type}/qual_flags.csv
        Falls back to: telemetry_out/qual_flags.csv
        Returns None if not found.
        """
        _SESSION_TYPE_ALIASES = {
            'FP1': 'Practice_1', 'FP2': 'Practice_2', 'FP3': 'Practice_3',
            'Qualifying': 'Qualifying', 'Race': 'Race', 'Sprint': 'Sprint',
            'Sprint_Shootout': 'Sprint_Shootout', 'Sprint_Qualifying': 'Sprint_Qualifying',
            'SQ': 'Sprint_Qualifying', 'SprintQualifying': 'Sprint_Qualifying',
            'S': 'Sprint', 'SprintRace': 'Sprint',
        }
        if self.year and self.event and self.session_type:
            event_clean = str(self.event).replace(' ', '_').replace('-', '_')
            raw_type = str(self.session_type).replace(' ', '_').replace('-', '_')
            session_type_clean = _SESSION_TYPE_ALIASES.get(raw_type, raw_type)
            candidate = Path(f"telemetry_out/{self.year}/{event_clean}/{session_type_clean}/qual_flags.csv")
            if candidate.exists():
                return candidate
        fallback = Path("telemetry_out/qual_flags.csv")
        if fallback.exists():
            return fallback
        return None

    def event_has_session_tag(self, year: Optional[int], event_name: Optional[str], requested_tag: Optional[str]) -> Optional[bool]:
        """Check FastF1 event schedule and config to determine whether the requested session tag exists for the event.

        Returns True if exists, False if definitely not, None if unknown (couldn't determine).
        """
        if not requested_tag or not event_name:
            return None

        try:
            import fastf1
        except Exception:
            # Cannot import fastf1 here — cannot validate
            return None

        # Determine year if not provided by trying to read telemetry meta or schedule
        yr = year
        if yr is None:
            # Try telemetry meta
            meta_file = _Path('telemetry_out') / 'session_meta.json'
            try:
                if meta_file.exists():
                    with open(meta_file, 'r') as f:
                        meta = json.load(f)
                        yr = int(meta.get('year')) if meta.get('year') else None
            except Exception:
                yr = None

        if yr is None:
            return None

        try:
            sched = fastf1.get_event_schedule(yr)
            df_row = sched[sched['EventName'] == event_name]
            if df_row.empty:
                return None

            row = df_row.iloc[0]

            # Find available SessionN timestamp columns (SessionNDateUtc / SessionNDate)
            available = []
            for i in range(1, 6):
                col = f"Session{i}"
                col_date_utc = f"{col}DateUtc"
                col_date = f"{col}Date"
                ts = None
                if col_date_utc in row.index:
                    ts = row[col_date_utc]
                elif col_date in row.index:
                    ts = row[col_date]

                if ts is not None and _pd.notna(ts):
                    if isinstance(ts, _pd.Timestamp):
                        available.append((col, ts.to_pydatetime()))
                    else:
                        try:
                            parsed = _pd.to_datetime(ts)
                            available.append((col, parsed.to_pydatetime()))
                        except Exception:
                            continue

            if not available:
                return False

            available.sort(key=lambda x: x[1])

            # Load event formats and session tags config
            try:
                with open(_Path('config/event_formats.json'), 'r', encoding='utf-8') as ef:
                    event_formats = json.load(ef)
            except Exception:
                event_formats = None

            try:
                with open(_Path('config/session_tags.json'), 'r', encoding='utf-8') as sf:
                    session_tags = json.load(sf)
            except Exception:
                session_tags = {}

            # Choose a format matching number of available sessions
            format_tags = None
            if event_formats:
                for name, tags in event_formats.items():
                    if len(tags) == len(available) and len(tags) > 0:
                        format_tags = tags
                        break

            if format_tags is None:
                # Fallback to conventional mapping truncated/padded
                format_tags = ['FP1', 'FP2', 'FP3', 'Q', 'R'][:len(available)]

            # Map tags to available SessionN
            mapped = {tag: col for (col, _), tag in zip(available, format_tags)}

            # Normalize requested tag
            rt = requested_tag.strip().upper()
            # Accept common synonyms
            synonyms = {
                'QUALIFYING': 'Q', 'PRACTICE1': 'FP1', 'PRACTICE2': 'FP2', 'PRACTICE3': 'FP3',
                'SPRINT': 'S', 'SPRINTSHOOTOUT': 'SS', 'SPRINTQUALIFYING': 'SQ', 'RACE': 'R'
            }
            if rt in synonyms:
                rt = synonyms[rt]

            return rt in mapped
        except Exception:
            return None
        
    def run_stage(self, stage_name: str, command: List[str], 
                  timeout: int = 600) -> bool:
        """
        Execute a pipeline stage and track results
        
        Args:
            stage_name: Human-readable stage name
            command: Command to execute
            timeout: Max execution time in seconds
        """
        self.logger.info(f"\n{'='*70}")
        self.logger.info(f"STAGE: {stage_name}")
        self.logger.info(f"{'='*70}")
        
        stage_start = time.time()
        
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=True
            )
            
            duration = time.time() - stage_start
            
            # Log output
            if result.stdout:
                for line in result.stdout.split('\n'):
                    if line.strip():
                        self.logger.info(f"  {line}")
            
            self.stage_results[stage_name] = {
                'status': 'SUCCESS',
                'duration': f"{duration:.1f}s",
                'timestamp': datetime.now().isoformat()
            }
            
            self.logger.info(f"[SUCCESS] {stage_name} completed in {duration:.1f}s")
            return True
            
        except subprocess.TimeoutExpired:
            duration = time.time() - stage_start
            error_msg = f"Stage timeout after {timeout}s"
            self.logger.error(f"[ERROR] {stage_name}: {error_msg}")
            
            self.stage_results[stage_name] = {
                'status': 'TIMEOUT',
                'duration': f"{duration:.1f}s",
                'error': error_msg
            }
            self.errors.append({
                'stage': stage_name,
                'error': error_msg
            })
            return False
            
        except subprocess.CalledProcessError as e:
            duration = time.time() - stage_start
            error_msg = f"Exit code {e.returncode}"
            
            self.logger.error(f"[ERROR] {stage_name}: {error_msg}")
            
            if e.stderr:
                self.logger.error(f"Error output:\n{e.stderr}")
            
            self.stage_results[stage_name] = {
                'status': 'FAILED',
                'duration': f"{duration:.1f}s",
                'error': error_msg,
                'stderr': e.stderr[:500] if e.stderr else None
            }
            
            self.errors.append({
                'stage': stage_name,
                'error': error_msg,
                'stderr': e.stderr[:500] if e.stderr else None
            })
            return False
            
        except Exception as e:
            duration = time.time() - stage_start
            error_msg = str(e)
            
            self.logger.error(f"[ERROR] {stage_name}: {error_msg}")
            
            self.stage_results[stage_name] = {
                'status': 'EXCEPTION',
                'duration': f"{duration:.1f}s",
                'error': error_msg
            }
            
            self.errors.append({
                'stage': stage_name,
                'error': error_msg
            })
            return False
    
    def stage_1_telemetry_extraction(self) -> bool:
        """Stage 1: Fetch telemetry data from FastF1"""
        cmd = [self.python_exe, "fetch_pipeline.py", "--fetch-only"]
        
        # Add session parameters if specified
        if self.year and self.event and self.session_type:
            # Map testing-style session names (e.g. 'Testing Day 1') to a supported
            # fetch flag (fetch_pipeline.py only accepts FP1/FP2/FP3/Qualifying/Sprint/Race)
            sess_type_to_pass = self.session_type
            try:
                st = str(self.session_type).strip()
                low = st.lower()
                # Map session types to identifiers accepted by fetch_pipeline.py / FastF1
                _fetch_map = {
                    'SprintQualifying': 'SQ',
                    'Sprint_Qualifying': 'SQ',
                    'SprintRace': 'S',
                    'Sprint_Race': 'S',
                }
                if st in _fetch_map:
                    sess_type_to_pass = _fetch_map[st]
                    self.logger.info(f"Mapping session type '{self.session_type}' -> '{sess_type_to_pass}' for fetcher compatibility")
                elif 'testing' in low and 'day' in low:
                    # extract day number and map to FP1/FP2/FP3
                    import re
                    m = re.search(r'day\s*(\d+)', low)
                    if m:
                        daynum = int(m.group(1))
                        sess_map = {1: 'FP1', 2: 'FP2', 3: 'FP3'}
                        sess_type_to_pass = sess_map.get(daynum, 'FP3')
                    else:
                        sess_type_to_pass = 'FP3'
                    self.logger.info(f"Mapping testing session '{self.session_type}' -> '{sess_type_to_pass}' for fetcher compatibility")
            except Exception:
                sess_type_to_pass = self.session_type

            cmd.extend(["--year", str(self.year), "--event", self.event, "--session-type", sess_type_to_pass])
        
        return self.run_stage(
            "Telemetry Extraction",
            cmd,
            timeout=600  # 10 minutes for large data fetches
        )

    def stage_1_5_flag_qual_laps(self) -> bool:
        """Stage 1.5: Run qualifier vs race-sim classifier on extracted telemetry"""
        # Only run classifier for FP3 sessions (where teams do quali sims and race sims)
        # Also allow it to run for testing sessions (OpenF1) when requested.

        is_fp2_or_fp3 = False
        if self.session_type:
            stype = self.session_type.upper()
            is_fp2_or_fp3 = stype in ['FP2', 'PRACTICE 2', 'PRACTICE2', 'FP3', 'PRACTICE 3', 'PRACTICE3']
        else:
            # Try to detect from session_meta.json
            try:
                meta_files = list(Path('telemetry_out').rglob('session_meta.json'))
                meta_file = meta_files[0] if meta_files else None
                if meta_file and meta_file.exists():
                    with open(meta_file, 'r', encoding='utf-8') as f:
                        meta = json.load(f)
                        session_name = meta.get('session_name', '').upper()
                        is_fp2_or_fp3 = (
                            'PRACTICE 2' in session_name or session_name == 'FP2' or
                            'PRACTICE 3' in session_name or session_name == 'FP3'
                        )
            except Exception:
                pass

        # Allow classifier to run for testing mode as well
        if getattr(self, 'is_testing', False):
            is_fp2_or_fp3 = True
            self.logger.info('Testing mode detected: enabling qual-vs-race classifier for testing data')

        if not is_fp2_or_fp3:
            self.logger.info(f"Skipping qual vs race-sim classifier - only runs for FP2/FP3/testing sessions (current: {self.session_type or 'unknown'})")
            self.stage_results['Flag Qualifying Laps'] = {
                'status': 'SKIPPED',
                'reason': 'Not an FP2, FP3 or testing session - classifier only runs for Practice 2, Practice 3 or testing',
                'timestamp': datetime.now().isoformat()
            }
            return True
        
        session_id = self.get_actual_session_identifier()

        telemetry_dir = Path('telemetry_out')
        if not telemetry_dir.exists() or not any(telemetry_dir.iterdir()):
            self.logger.info("telemetry_out missing or empty, skipping classifier stage")
            self.stage_results['Flag Qualifying Laps'] = {
                'status': 'SKIPPED',
                'reason': 'No telemetry data',
                'timestamp': datetime.now().isoformat()
            }
            return True

        cmd = [
            self.python_exe,
            "tools/qual_vs_race_classifier.py",
            "--input-dir", "telemetry_out",
            "--output", "telemetry_out/qual_flags.csv",
        ]

        if session_id:
            cmd.extend(["--session-filter", session_id])
            if self.year:
                cmd.extend(["--year", str(self.year)])
            self.logger.info(f"Running classifier for session: {session_id} (year: {self.year})")
        else:
            self.logger.info("Running classifier without session filter (auto-detect)")

        # Log the exact command and environment to aid reproducibility/debugging
        try:
            self.logger.info(f"Classifier command: {' '.join(cmd)}")
            self.logger.info(f"Working dir: {os.getcwd()}")
            self.logger.info(f"Python exe: {self.python_exe}")
            self.logger.info(f"PYTHONPATH: {os.environ.get('PYTHONPATH')}")
        except Exception:
            # Don't fail the pipeline for logging issues
            pass

        # Preserve upload option and classifier tuning based on pipeline settings
        if getattr(self, 'classifier_ignore_isaccurate', False):
            cmd.append("--ignore-isaccurate")
        if getattr(self, 'upload_snowflake', False):
            cmd.append("--upload-snowflake")

        return self.run_stage(
            "Flag Qualifying Laps",
            cmd,
            timeout=120
        )
    
    def stage_2_warehouse_loading(self) -> bool:
        """Stage 2: Load data to Snowflake warehouse"""
        # Use testing-specific aggregation for testing sessions
        if self.is_testing:
            script = "aggregate_testing.py"
        else:
            script = "aggregate_for_viz.py"
        
        return self.run_stage(
            "Snowflake Warehouse Loading",
            [self.python_exe, script],
            timeout=180  # 3 minutes
        )
    
    def stage_3_racing_line_analysis(self) -> bool:
        """Stage 3: Racing line analysis for all drivers"""
        # Get actual session identifier from extracted data
        session_id = self.get_actual_session_identifier()
        
        if session_id:
            cmd = [self.python_exe, "racing_line_analyzer.py", session_id]
        elif self.session_name:
            cmd = [self.python_exe, "racing_line_analyzer.py", self.session_name]
        else:
            # Will auto-detect latest session
            cmd = [self.python_exe, "racing_line_analyzer.py", "Abu_Dhabi_Grand_Prix_Race"]
        
        return self.run_stage(
            "Racing Line Analysis",
            cmd,
            timeout=240  # 4 minutes
        )
    
    def stage_4_tire_degradation_training(self) -> bool:
        """Stage 4a: Retrain tire degradation model with new data (race_sim laps only).

        Only runs for FP2, FP3, and Race sessions — skipped for FP1 and Qualifying
        because those sessions contain no meaningful race simulation stints.
        When a qual_flags.csv is available for the current session it is passed to
        the model trainer so that only flagged race_sim laps are used.
        """
        # Skip FP1 and Qualifying — no race simulation stints to model
        if self.session_type:
            st = str(self.session_type).upper()
            skip_types = {'FP1', 'PRACTICE 1', 'PRACTICE1', 'Q', 'QUALIFYING', 'SQ', 'SPRINTQUALIFYING', 'SPRINT_QUALIFYING'}
            if st in skip_types:
                self.logger.info(
                    f"Skipping Tire Degradation Model Training — not applicable for {self.session_type} sessions"
                )
                self.stage_results['Tire Degradation Model Training'] = {
                    'status': 'SKIPPED',
                    'reason': f'Session type {self.session_type} has no race simulation laps',
                    'timestamp': datetime.now().isoformat(),
                }
                return True

        # Determine if this is a Race-type session (all laps are race laps)
        is_race_type = False
        if self.session_type:
            st = str(self.session_type).upper()
            is_race_type = 'RACE' in st or st in {'S', 'SPRINT', 'SPRINTRACE', 'SPRINT_RACE'}

        # Pass session + year arguments for strict mapping
        session_id = self.get_actual_session_identifier()
        session_arg = session_id or self.session_name
        cmd = [self.python_exe, "tire_degradation_model.py", "train"]
        if session_arg:
            cmd.append(session_arg)
        if self.year:
            cmd += ["--year", str(self.year)]

        if is_race_type:
            # All race laps are race simulation laps — no qual_flags filter needed
            cmd.append("--race-session")
            self.logger.info("Race session — all laps will be used as race simulation training data")
        else:
            # Resolve qual_flags.csv path for this session (race_sim filter)
            qual_flags_path = self._find_qual_flags_path()
            if qual_flags_path:
                cmd += ["--qual-flags", str(qual_flags_path)]
                self.logger.info(f"Tire model will use race_sim filter from: {qual_flags_path}")
            else:
                self.logger.info("No qual_flags.csv found for this session — model will train on all laps")

        return self.run_stage(
            "Tire Degradation Model Training",
            cmd,
            timeout=120  # 2 minutes
        )
    
    def stage_5_tire_degradation_analysis(self) -> bool:
        """Stage 4b: Generate tire predictions for session"""
        # Only run prediction analysis for FP3 sessions. For races, attempt to
        # recover FP3 predictions saved earlier and compare with race telemetry.
        session_id = self.get_actual_session_identifier()

        # Determine if current session is a practice (FP1/FP2/FP3) or Race
        is_practice = False
        is_fp2_or_fp3 = False
        is_race = False
        if self.session_type:
            st = str(self.session_type).upper()
            is_practice = st in ['FP1', 'FP2', 'FP3', 'PRACTICE 1', 'PRACTICE 2', 'PRACTICE 3', 'PRACTICE1', 'PRACTICE2', 'PRACTICE3']
            is_fp2_or_fp3 = st in ['FP2', 'PRACTICE 2', 'PRACTICE2', 'FP3', 'PRACTICE 3', 'PRACTICE3']
            is_qualifying = st in ['Q', 'QUALIFYING', 'SQ', 'SPRINTQUALIFYING', 'SPRINT_QUALIFYING']
            is_race = 'RACE' in st or st in ['S', 'SPRINT', 'SPRINTRACE', 'SPRINT_RACE']
        else:
            # Try to read telemetry meta for session_name
            try:
                meta_file = Path('telemetry_out') / 'session_meta.json'
                if meta_file.exists():
                    with open(meta_file, 'r', encoding='utf-8') as f:
                        meta = json.load(f)
                        sess_name = (meta.get('session_name') or '').upper()
                        is_practice = any(x in sess_name for x in ['PRACTICE 1', 'PRACTICE 2', 'PRACTICE 3', 'FP1', 'FP2', 'FP3'])
                        is_fp2_or_fp3 = any(x in sess_name for x in ['PRACTICE 2', 'FP2', 'PRACTICE 3', 'FP3'])
                        is_qualifying = any(x in sess_name for x in ['QUALIFYING', 'SPRINT QUALIFYING', 'SPRINT_QUALIFYING'])
                        is_race = 'RACE' in sess_name or any(x in sess_name for x in ['SPRINT RACE', 'SPRINT_RACE', ' SPRINT'])
            except Exception:
                pass

        # For FP2 or FP3 practice sessions, run predictions and persist them
        if is_fp2_or_fp3:
            target = session_id or self.session_name or 'PRACTICE_Session'
            cmd = [self.python_exe, "tire_degradation_model.py", "predict", target, "5"]
            return self.run_stage(
                "Tire Degradation Analysis",
                cmd,
                timeout=180  # 3 minutes
            )

        # For Race sessions, run the compare command which loads the FP2/FP3 model and plots
        # predicted degradation curves vs actual race lap times.
        if is_race:
            try:
                target = session_id or self.session_name or 'Race_Session'

                # Find best FP2/FP3 model pkl
                model_dir = Path('models')
                fp_candidates = sorted(
                    [p for p in model_dir.glob('tire_deg_model_*.pkl')
                     if any(x in p.stem.upper() for x in ('FP2', 'FP3', 'PRACTICE_2', 'PRACTICE_3'))],
                    key=lambda p: p.stat().st_mtime, reverse=True,
                )
                model_pkl = str(fp_candidates[0]) if fp_candidates else None

                # Derive visualizations output dir
                if self.year and self.event:
                    event_clean = str(self.event).replace(' ', '_').replace('-', '_')
                    st_clean = str(self.session_type).replace(' ', '_') if self.session_type else 'Race'
                    viz_dir = str(Path('visualizations') / str(self.year) / event_clean / st_clean)
                else:
                    viz_dir = str(Path('visualizations') / target)

                cmd = [self.python_exe, "tire_degradation_model.py", "compare", target,
                       "--output-dir", viz_dir]
                if model_pkl:
                    cmd += ["--model", model_pkl]
                    self.logger.info(f"Tire comparison using FP2/FP3 model: {model_pkl}")
                if self.year:
                    cmd += ["--year", str(self.year)]

                ok = self.run_stage("Tire Degradation Analysis", cmd, timeout=180)
                if not ok:
                    self.logger.warning('Tire degradation comparison failed')
            except Exception as e:
                self.logger.warning(f'Failed to run tire degradation comparison: {e}')
            return True

        # Non-FP3/non-Race: attempt to find FP2/FP3 prediction artifact and compare pit windows
        try:
            # Find most recent FP2 or FP3 predictions file saved in telemetry_out
            pred_files = list(Path('telemetry_out').glob('tire_predictions_*.csv'))
            if not pred_files:
                self.logger.info('No FP2/FP3 predictions found in telemetry_out; skipping comparison')
                self.stage_results['Tire Degradation Analysis'] = {
                    'status': 'SKIPPED',
                    'reason': 'No FP2/FP3 predictions available',
                    'timestamp': datetime.now().isoformat()
                }
                return True

            # Prefer FP3 then FP2 predictions, else take newest
            fp3_candidates = [p for p in pred_files if 'FP3' in p.stem.upper() or 'PRACTICE_3' in p.stem.upper()]
            fp2_candidates = [p for p in pred_files if 'FP2' in p.stem.upper() or 'PRACTICE_2' in p.stem.upper()]
            chosen = None
            if fp3_candidates:
                # pick latest modified
                chosen = max(fp3_candidates, key=lambda p: p.stat().st_mtime)
            elif fp2_candidates:
                chosen = max(fp2_candidates, key=lambda p: p.stat().st_mtime)
            else:
                chosen = max(pred_files, key=lambda p: p.stat().st_mtime)

            self.logger.info(f'Found FP2/FP3 predictions artifact: {chosen}')

            # Load predictions
            preds = _pd.read_csv(chosen)

            # Load race laps for current session
            # Attempt to read lap files matching session prefix
            # Use fixed telemetry file structure for lap files
            laps_glob = []
            if self.year and self.event and self.session_type:
                event_clean = str(self.event).replace(' ', '_').replace('-', '_')
                session_type_clean = str(self.session_type).replace(' ', '_').replace('-', '_')
                laps_dir = Path(f"telemetry_out/{self.year}/{event_clean}/{session_type_clean}")
                laps_glob = list(laps_dir.glob(f"*laps.csv"))
            if not laps_glob:
                # fallback: any laps files in telemetry_out
                laps_glob = list(Path('telemetry_out').rglob('*laps.csv'))

            if not laps_glob:
                self.logger.info('No race lap files found to compare; skipping comparison')
                self.stage_results['Tire Degradation Analysis'] = {
                    'status': 'SKIPPED',
                    'reason': 'No race lap files found',
                    'timestamp': datetime.now().isoformat()
                }
                return True

            all_laps = []
            for f in laps_glob:
                try:
                    df = _pd.read_csv(f)
                    df['Driver'] = f.stem.split('_')[-2]
                    all_laps.append(df)
                except Exception:
                    continue

            if not all_laps:
                self.logger.info('No usable race lap files loaded; skipping comparison')
                self.stage_results['Tire Degradation Analysis'] = {
                    'status': 'SKIPPED',
                    'reason': 'No usable race lap files',
                    'timestamp': datetime.now().isoformat()
                }
                return True

            race_laps = _pd.concat(all_laps, ignore_index=True)

            # Identify actual pit laps per driver (using PitInTime/PitOutTime or PitIn/PitOut flags)
            actual_pits = {}
            for driver in race_laps['Driver'].unique():
                d = race_laps[race_laps['Driver'] == driver]
                pit_laps = d[d[['PitInTime', 'PitOutTime']].notna().any(axis=1)]['LapNumber'] if 'PitInTime' in d.columns and 'PitOutTime' in d.columns else d[d[['PitIn', 'PitOut']].notna().any(axis=1)]['LapNumber'] if 'PitIn' in d.columns else _pd.Series(dtype=float)
                if isinstance(pit_laps, _pd.Series) and not pit_laps.empty:
                    actual_pits[driver] = int(pit_laps.iloc[0])
                else:
                    # No pit recorded
                    actual_pits[driver] = None

            # Compare predicted critical laps (where recommendation == PIT_WINDOW_OPENING) vs actual pits
            comparisons = []
            for _, row in preds.iterrows():
                drv = row.get('Driver')
                predicted_lap = int(row.get('PredictedLap')) if not _pd.isna(row.get('PredictedLap')) else None
                actual = actual_pits.get(drv)
                match = False
                if actual is not None and predicted_lap is not None:
                    # consider match if actual within +/-1 lap of prediction
                    match = abs(actual - predicted_lap) <= 1

                comparisons.append({
                    'Driver': drv,
                    'PredictedLap': predicted_lap,
                    'ActualPitLap': actual,
                    'MatchWithin1Lap': match
                })

            # Save comparison report
            _session_label = (self.session_name or 'race').replace(' ', '_')
            out_file = Path('telemetry_out') / f"prediction_comparison_{_session_label}.json"
            try:
                with open(out_file, 'w', encoding='utf-8') as of:
                    json.dump(comparisons, of, indent=2)
                self.logger.info(f'Saved prediction vs actual comparison: {out_file}')
            except Exception as e:
                self.logger.warning(f'Failed to save comparison report: {e}')

            self.stage_results['Tire Degradation Analysis'] = {
                'status': 'SUCCESS',
                'duration': '0s',
                'comparison_report': str(out_file)
            }
            return True

        except Exception as e:
            self.logger.error(f"[ERROR] Tire Degradation Comparison: {e}")
            self.stage_results['Tire Degradation Analysis'] = {
                'status': 'FAILED',
                'error': str(e),
                'timestamp': datetime.now().isoformat()
            }
            self.errors.append({'stage': 'Tire Degradation Analysis', 'error': str(e)})
            return False
    
    def stage_6_driver_reports(self) -> bool:
        """Stage 5: Generate individual driver report folders"""
        self.logger.info(f"\n{'='*70}")
        self.logger.info("STAGE: Driver Reports Generation")
        self.logger.info(f"{'='*70}")
        
        stage_start = time.time()
        
        try:
            # Import required modules
            from racing_line_analyzer import RacingLineAnalyzer
            from tire_degradation_model import TireDegradationModel
            
            # Determine session - use actual identifier from extracted data
            session = self.get_actual_session_identifier() or self.session_name or "Abu_Dhabi_Grand_Prix_Race"
            
            # Create reports directory structure
            reports_dir = Path("driver_reports") / session
            reports_dir.mkdir(parents=True, exist_ok=True)
            
            # Load racing line data
            analyzer = RacingLineAnalyzer()
            if not analyzer.load_session_data(session):
                self.logger.warning("No per-driver corner data available for this session (telemetry unavailable). Skipping driver reports.")
                self.stage_results['Driver Reports'] = {
                    'status': 'SKIPPED',
                    'reason': 'No corner/telemetry data available for this session',
                    'timestamp': datetime.now().isoformat()
                }
                return True
            
            analyzer.identify_optimal_lines()
            deltas = analyzer.calculate_driver_deltas()
            
            # Get list of drivers
            drivers = sorted(deltas['Driver'].unique())
            
            self.logger.info(f"Generating reports for {len(drivers)} drivers...")
            
            reports_generated = 0
            
            for driver in drivers:
                driver_dir = reports_dir / driver
                driver_dir.mkdir(exist_ok=True)
                
                # Generate racing line report
                report = analyzer.generate_driver_report(driver)
                
                if report:
                    # Save racing line analysis
                    with open(driver_dir / 'racing_line_analysis.json', 'w') as f:
                        json.dump(report, f, indent=2, default=str)
                    
                    # Copy visualization if exists
                    viz_src = Path("telemetry_out/viz") / f"{driver}_corner_analysis.png"
                    if viz_src.exists():
                        import shutil
                        shutil.copy(viz_src, driver_dir / "corner_analysis.png")
                    
                    # Create driver summary
                    summary = {
                        'driver': driver,
                        'session': session,
                        'generated_at': datetime.now().isoformat(),
                        'racing_line': {
                            'overall_deficit': report['OverallSpeedDeficit'],
                            'worst_corners': list(report['WorstCorners'].keys()),
                            'best_corners': list(report['BestCorners'].keys())
                        }
                    }
                    
                    with open(driver_dir / 'summary.json', 'w') as f:
                        json.dump(summary, f, indent=2)
                    
                    reports_generated += 1
                    self.logger.info(f"  [OK] {driver} report generated")
            
            duration = time.time() - stage_start
            
            self.stage_results['Driver Reports'] = {
                'status': 'SUCCESS',
                'duration': f"{duration:.1f}s",
                'drivers': len(drivers),
                'reports_generated': reports_generated,
                'output_dir': str(reports_dir)
            }
            
            self.logger.info(f"[SUCCESS] Generated {reports_generated} driver reports in {duration:.1f}s")
            self.logger.info(f"Reports saved to: {reports_dir}")
            
            return True
            
        except Exception as e:
            duration = time.time() - stage_start
            error_msg = str(e)
            
            self.logger.error(f"[ERROR] Driver Reports: {error_msg}")
            
            self.stage_results['Driver Reports'] = {
                'status': 'FAILED',
                'duration': f"{duration:.1f}s",
                'error': error_msg
            }
            
            self.errors.append({
                'stage': 'Driver Reports',
                'error': error_msg
            })
            
            return False

    # ── Coasting Analysis ──────────────────────────────────────────────────
    # Sessions where lift-and-coast analysis is meaningful.
    # FP1 is excluded: it is typically a short installation lap session with
    # no representative flying laps and adds noise to the dataset.
    _COASTING_SKIP_TYPES = {
        'FP1', 'PRACTICE 1', 'PRACTICE1', 'PRACTICE_1',
    }

    def stage_coasting_analysis(self) -> bool:
        """Stage: Detect lift-and-coast zones for every driver in this session.

        Runs ``run_coasting_analysis.py`` scoped to the current session so that
        only new data is processed.  Results are appended to the master file at
        ``coasting_zones/all_zones.csv`` and written per-driver under
        ``coasting_zones/{year}/{event}/{session_type}/``.

        Skipped for Practice 1 (FP1) — those sessions contain installation laps
        with no meaningful flying-lap data.
        """
        # Resolve session type string
        st = str(self.session_type or '').strip().upper().replace(' ', '_').replace('-', '_')
        if st in self._COASTING_SKIP_TYPES:
            self.logger.info(
                f"Skipping Coasting Analysis — not run for {self.session_type} sessions"
            )
            self.stage_results['Coasting Analysis'] = {
                'status': 'SKIPPED',
                'reason': 'Practice 1 sessions do not contain representative flying laps',
                'timestamp': datetime.now().isoformat(),
            }
            return True

        # Build the --session filter: event/session_type path fragment
        _SESSION_TYPE_ALIASES = {
            'FP1': 'Practice_1', 'FP2': 'Practice_2', 'FP3': 'Practice_3',
            'SQ': 'Sprint_Qualifying', 'SPRINTQUALIFYING': 'Sprint_Qualifying',
            'SPRINT_QUALIFYING': 'Sprint_Qualifying',
            'S': 'Sprint', 'SPRINTRACK': 'Sprint', 'SPRINT_RACE': 'Sprint',
        }
        session_filter = None
        if self.event and self.session_type:
            event_clean = str(self.event).replace(' ', '_').replace('-', '_')
            raw_type = str(self.session_type).replace(' ', '_').replace('-', '_')
            type_clean = _SESSION_TYPE_ALIASES.get(raw_type, raw_type)
            type_clean = _SESSION_TYPE_ALIASES.get(type_clean.upper(), type_clean)
            session_filter = f"{event_clean}/{type_clean}"

        cmd = [self.python_exe, 'run_coasting_analysis.py']
        if session_filter:
            cmd += ['--session', session_filter]
        # Force re-process so the current session always gets updated data
        cmd.append('--force')

        return self.run_stage(
            'Coasting Analysis',
            cmd,
            timeout=120,   # 2 minutes — pure CSV processing, no network calls
        )

    def stage_coasting_clipping_viz(self) -> bool:
        """Stage: Generate per-driver coasting / super-clipping zone PNGs.

        Calls ``plot_coasting_clipping.run()`` directly (in-process) for speed.
        Output lands in
        ``visualizations/{year}/{event}/{session_type}/coasting_clipping/``.

        Skipped for Practice 1 (same rule as the analysis stage).
        """
        st = str(self.session_type or '').strip().upper().replace(' ', '_').replace('-', '_')
        if st in self._COASTING_SKIP_TYPES:
            self.stage_results['Coasting Clipping Viz'] = {
                'status': 'SKIPPED',
                'reason': 'Practice 1 sessions excluded',
                'timestamp': datetime.now().isoformat(),
            }
            return True

        try:
            from plot_coasting_clipping import run as run_ccviz

            _TYPE_ALIASES = {
                'FP2': 'Practice_2', 'FP3': 'Practice_3',
                'SQ': 'Sprint_Qualifying', 'SPRINTQUALIFYING': 'Sprint_Qualifying',
                'SPRINT_QUALIFYING': 'Sprint_Qualifying',
                'S': 'Sprint', 'SPRINTRACK': 'Sprint', 'SPRINT_RACE': 'Sprint',
            }
            event_clean = str(self.event or '').replace(' ', '_').replace('-', '_')
            raw_type    = str(self.session_type or '').replace(' ', '_').replace('-', '_')
            type_clean  = _TYPE_ALIASES.get(raw_type.upper(), raw_type)

            self.logger.info(
                f"[Coasting/Clipping Viz] {self.year}/{event_clean}/{type_clean}"
            )
            run_ccviz(
                year=str(self.year),
                event=event_clean,
                session_type=type_clean,
                force=True,
            )
            self.stage_results['Coasting Clipping Viz'] = {
                'status': 'SUCCESS',
                'timestamp': datetime.now().isoformat(),
            }
            return True

        except Exception as exc:
            self.logger.warning(f'Coasting Clipping Viz stage failed: {exc}')
            self.stage_results['Coasting Clipping Viz'] = {
                'status': 'FAILED',
                'error': str(exc),
                'timestamp': datetime.now().isoformat(),
            }
            return False

    def stage_intra_brake_throttle(self) -> bool:
        """Stage: Per-team Speed / Throttle / Brake best-lap overlay.

        Calls ``plot_intra_brake_throttle.run()`` in-process for every team,
        saving one PNG per team into
        ``visualizations/{year}/{event}/{session_type}/intra/{Team}/brake_throttle.png``.

        Skipped for Practice 1 (no representative flying laps).
        """
        st = str(self.session_type or '').strip().upper().replace(' ', '_').replace('-', '_')
        if st in self._COASTING_SKIP_TYPES:
            self.stage_results['Intra Brake Throttle'] = {
                'status': 'SKIPPED',
                'reason': 'Practice 1 sessions excluded',
                'timestamp': datetime.now().isoformat(),
            }
            return True

        try:
            from plot_intra_brake_throttle import run as run_ibt

            _TYPE_ALIASES = {
                'FP2': 'Practice_2', 'FP3': 'Practice_3',
                'SQ': 'Sprint_Qualifying', 'SPRINTQUALIFYING': 'Sprint_Qualifying',
                'SPRINT_QUALIFYING': 'Sprint_Qualifying',
                'S': 'Sprint', 'SPRINTRACE': 'Sprint', 'SPRINT_RACE': 'Sprint',
            }
            event_clean = str(self.event or '').replace(' ', '_').replace('-', '_')
            raw_type    = str(self.session_type or '').replace(' ', '_').replace('-', '_')
            type_clean  = _TYPE_ALIASES.get(raw_type.upper(), raw_type)

            self.logger.info(
                f"[Intra Brake Throttle] {self.year}/{event_clean}/{type_clean}"
            )
            run_ibt(year=str(self.year), event=event_clean,
                    session_type=type_clean, force=True)

            self.stage_results['Intra Brake Throttle'] = {
                'status': 'SUCCESS',
                'timestamp': datetime.now().isoformat(),
            }
            return True

        except Exception as exc:
            self.logger.warning(f'Intra Brake Throttle stage failed: {exc}')
            self.stage_results['Intra Brake Throttle'] = {
                'status': 'FAILED',
                'error': str(exc),
                'timestamp': datetime.now().isoformat(),
            }
            return False

    def stage_circuit_dominance(self) -> bool:
        """Stage: Circuit dominance map for all intra-team pairs + session top 2.

        Saves ``circuit_dominance.png`` into every intra-team folder and
        ``02_circuit_dominance.png`` into the session visualizations root.
        """
        st = str(self.session_type or '').strip().upper().replace(' ', '_').replace('-', '_')
        if st in self._COASTING_SKIP_TYPES:
            self.stage_results['Circuit Dominance'] = {
                'status': 'SKIPPED',
                'reason': 'Practice 1 sessions excluded',
                'timestamp': datetime.now().isoformat(),
            }
            return True

        try:
            from plot_circuit_dominance import run as run_cd

            _TYPE_ALIASES = {
                'FP2': 'Practice_2', 'FP3': 'Practice_3',
                'SQ': 'Sprint_Qualifying', 'SPRINTQUALIFYING': 'Sprint_Qualifying',
                'SPRINT_QUALIFYING': 'Sprint_Qualifying',
                'S': 'Sprint', 'SPRINTRACE': 'Sprint', 'SPRINT_RACE': 'Sprint',
            }
            event_clean = str(self.event or '').replace(' ', '_').replace('-', '_')
            raw_type    = str(self.session_type or '').replace(' ', '_').replace('-', '_')
            type_clean  = _TYPE_ALIASES.get(raw_type.upper(), raw_type)

            self.logger.info(f"[Circuit Dominance] {self.year}/{event_clean}/{type_clean}")
            run_cd(year=str(self.year), event=event_clean,
                   session_type=type_clean, force=True)

            self.stage_results['Circuit Dominance'] = {
                'status': 'SUCCESS',
                'timestamp': datetime.now().isoformat(),
            }
            return True

        except Exception as exc:
            self.logger.warning(f'Circuit Dominance stage failed: {exc}')
            self.stage_results['Circuit Dominance'] = {
                'status': 'FAILED',
                'error': str(exc),
                'timestamp': datetime.now().isoformat(),
            }
            return False

        """Stage: Generate visualizations for the session using visualize_testing.py

        For testing sessions this will attempt to parse session_meta.json in the correct folder.
        If metadata is missing the stage is skipped gracefully.
        """
        _SESSION_TYPE_ALIASES = {
            'FP1': 'Practice_1', 'FP2': 'Practice_2', 'FP3': 'Practice_3',
            'Qualifying': 'Qualifying', 'Race': 'Race', 'Sprint': 'Sprint',
            'Sprint_Shootout': 'Sprint_Shootout', 'Sprint_Qualifying': 'Sprint_Qualifying',
        }
        meta_file = None
        if self.year and self.event and self.session_type:
            event_clean = str(self.event).replace(' ', '_').replace('-', '_')
            raw_type = str(self.session_type).replace(' ', '_').replace('-', '_')
            session_type_clean = _SESSION_TYPE_ALIASES.get(raw_type, raw_type)
            meta_path = Path(f"telemetry_out/{self.year}/{event_clean}/{session_type_clean}/session_meta.json")
            if meta_path.exists():
                meta_file = meta_path
        if not meta_file:
            # Fallback: recursive search — prefer the file matching year/event/session_type
            meta_files = list(Path('telemetry_out').rglob('session_meta.json'))
            if meta_files:
                # Try to match by year and event in the path
                if self.year and self.event:
                    event_clean_fb = str(self.event).replace(' ', '_').replace('-', '_')
                    preferred = [f for f in meta_files
                                 if str(self.year) in f.parts and event_clean_fb in f.parts]
                    meta_file = preferred[0] if preferred else meta_files[0]
                else:
                    meta_file = meta_files[0]
        if not meta_file or not meta_file.exists():
            self.logger.info('No session_meta.json found; skipping visualizations stage')
            self.stage_results['Visualizations'] = {
                'status': 'SKIPPED',
                'reason': 'Missing session_meta.json',
                'timestamp': datetime.now().isoformat()
            }
            return True

        try:
            with open(meta_file, 'r', encoding='utf-8') as f:
                meta = json.load(f)

            sess_name = (meta.get('session_name') or '').lower()
            # default values
            test_num = int(meta.get('test_number', 1)) if meta.get('test_number') else 1
            day_num = int(meta.get('day', 1)) if meta.get('day') else 1

            import re
            # Try to extract 'day N' and 'test N' from session_name if present
            mday = re.search(r'day\s*(\d+)', sess_name)
            if mday:
                try:
                    day_num = int(mday.group(1))
                except Exception:
                    pass

            mtest = re.search(r'test\s*(\d+)', sess_name) or re.search(r'testing\s*(\d+)', sess_name)
            if mtest:
                try:
                    test_num = int(mtest.group(1))
                except Exception:
                    pass

        except Exception as e:
            self.logger.warning(f'Could not read/parse session_meta.json for visualizations: {e}')
            self.stage_results['Visualizations'] = {
                'status': 'SKIPPED',
                'reason': 'Error parsing session_meta.json',
                'timestamp': datetime.now().isoformat()
            }
            return True

        # Always run visualizations for all session types
        session_id = self.get_actual_session_identifier() or self.session_name
        race_vis = Path('visualize_race.py')
        if race_vis.exists():
            cmd = [self.python_exe, 'visualize_race.py']
            if session_id:
                cmd.extend(['--session', session_id])
            if self.year:
                cmd.extend(['--year', str(self.year)])
            self.logger.info(f"Running race visualizer for session: {session_id or 'auto-detect'}")
            return self.run_stage('Generate Visualizations (race)', cmd, timeout=240)
        # Fallback: call testing visualizer with a warning (best-effort)
        fallback_cmd = [self.python_exe, 'visualize_testing.py', '--test', str(1), '--day', str(1), '--source', 'local']
        self.logger.warning('No race visualizer found (visualize_race.py). Falling back to testing visualizer (best-effort).')
        self.stage_results['Visualizations'] = {
            'status': 'FALLBACK',
            'note': 'No race visualizer found; invoked testing visualizer as fallback',
            'timestamp': datetime.now().isoformat()
        }
        return self.run_stage('Generate Visualizations (fallback)', fallback_cmd, timeout=180)
    
    def save_execution_summary(self):
        """Save pipeline execution summary to logs"""
        total_duration = (datetime.now() - self.start_time).total_seconds()
        
        summary = {
            'execution_id': self.start_time.isoformat(),
            'session': self.session_name or 'auto-detected',
            'start_time': self.start_time.isoformat(),
            'end_time': datetime.now().isoformat(),
            'total_duration': f"{total_duration:.1f}s",
            'stages': self.stage_results,
            'errors': self.errors,
            'status': 'SUCCESS' if not self.errors else 'PARTIAL' if len(self.errors) < len(self.stage_results) else 'FAILED'
        }
        
        # Save to logs directory
        logs_dir = Path('logs/pipeline_executions')
        logs_dir.mkdir(parents=True, exist_ok=True)
        
        filename = f"pipeline_{self.start_time.strftime('%Y%m%d_%H%M%S')}.json"
        filepath = logs_dir / filename
        
        with open(filepath, 'w') as f:
            json.dump(summary, f, indent=2)
        
        self.logger.info(f"\nExecution summary saved: {filepath}")
        
        return summary
    
    def run_full_pipeline(self) -> bool:
        """Execute complete pipeline from start to finish"""
        self.logger.info("\n" + "="*70)
        self.logger.info("F1 MASTER PIPELINE - AUTOMATED ANALYTICS EXECUTION")
        self.logger.info("="*70)
        self.logger.info(f"Started: {self.start_time.strftime('%Y-%m-%d %H:%M:%S')}")
        self.logger.info(f"Session: {self.session_name or 'auto-detect latest'}")
        self.logger.info("="*70 + "\n")
        # If a specific session_type was requested, validate it exists for the event
        if self.session_type and self.event:
            exists = self.event_has_session_tag(self.year, self.event, self.session_type)
            if exists is False:
                self.logger.info(f"Requested session type '{self.session_type}' not present for event '{self.event}'. Skipping pipeline.")
                # Mark summary as skipped
                self.stage_results['Pipeline'] = {
                    'status': 'SKIPPED',
                    'reason': f"Session type '{self.session_type}' not present for event '{self.event}'",
                    'timestamp': datetime.now().isoformat()
                }
                # Save summary and exit as successful skip
                self.save_execution_summary()
                return True
            elif exists is None:
                self.logger.info(f"Could not determine availability of session type '{self.session_type}' for event '{self.event}'. Proceeding optimistically.")

        # Execute stages sequentially
        # Testing sessions skip stages requiring detailed telemetry (corner data)
        if self.is_testing:
            # OpenF1 data only has lap times, sectors, speed traps - no corner data
            stages = [
                ("1.5/6", self.stage_1_5_flag_qual_laps),
                ("2/6", self.stage_2_warehouse_loading),
                ("4/6", self.stage_4_tire_degradation_training),
            ]
            self.logger.info("Testing mode: Running classifier + skipping stages that require detailed telemetry (corners/racing line)")
        else:
            # Full pipeline for race weekend sessions with FastF1 detailed telemetry
            stages = [
                ("1/6", self.stage_1_telemetry_extraction),
                ("1.5/6", self.stage_1_5_flag_qual_laps),
                ("2/6", self.stage_2_warehouse_loading),
                ("3/6", self.stage_3_racing_line_analysis),
                ("4/6", self.stage_4_tire_degradation_training),
                ("5/6", self.stage_5_tire_degradation_analysis),
                ("6/6", self.stage_6_driver_reports),
            ]
        
        for stage_num, stage_func in stages:
            self.logger.info(f"\n>>> EXECUTING STAGE {stage_num}")
            
            success = stage_func()
            
            if not success:
                self.logger.warning(f"Stage {stage_num} failed, continuing to next stage...")
                # Don't stop pipeline - continue with remaining stages
        
        # After all stages have run, attempt to generate visualizations for the session
        try:
            vis_success = self.stage_visualizations()
            if not vis_success:
                self.logger.warning('Visualizations stage reported failure or was skipped')
        except Exception as e:
            self.logger.warning(f'Visualizations stage raised an exception: {e}')

        # Coasting analysis — runs for all sessions except FP1
        try:
            self.stage_coasting_analysis()
        except Exception as e:
            self.logger.warning(f'Coasting Analysis stage raised an exception: {e}')

        # Coasting / super-clipping zone visualisations
        try:
            self.stage_coasting_clipping_viz()
        except Exception as e:
            self.logger.warning(f'Coasting Clipping Viz stage raised an exception: {e}')

        # Intra-team Speed/Throttle/Brake best-lap overlay
        try:
            self.stage_intra_brake_throttle()
        except Exception as e:
            self.logger.warning(f'Intra Brake Throttle stage raised an exception: {e}')

        # Circuit dominance maps (intra-team + session top 2)
        try:
            self.stage_circuit_dominance()
        except Exception as e:
            self.logger.warning(f'Circuit Dominance stage raised an exception: {e}')


        # Generate final summary
        self.logger.info("\n" + "="*70)
        self.logger.info("PIPELINE EXECUTION SUMMARY")
        self.logger.info("="*70)
        
        summary = self.save_execution_summary()
        
        total_duration = (datetime.now() - self.start_time).total_seconds()
        stages_completed = sum(1 for s in self.stage_results.values() if s['status'] == 'SUCCESS')
        stages_skipped = sum(1 for s in self.stage_results.values() if s['status'] == 'SKIPPED')
        stages_ran = len(self.stage_results) - stages_skipped

        self.logger.info(f"Total Duration: {total_duration:.1f}s ({total_duration/60:.1f} minutes)")
        self.logger.info(f"Stages Completed: {stages_completed}/{stages_ran}")
        if stages_skipped:
            skipped_names = [name for name, s in self.stage_results.items() if s['status'] == 'SKIPPED']
            self.logger.info(f"Stages Skipped:   {stages_skipped} ({', '.join(skipped_names)})")

        if self.errors:
            self.logger.warning(f"Errors Encountered: {len(self.errors)}")
            for error in self.errors:
                self.logger.warning(f"  - {error['stage']}: {error['error']}")
        else:
            self.logger.info("[SUCCESS] All stages completed successfully!")
        
        self.logger.info("="*70 + "\n")
        
        return len(self.errors) == 0


def main():
    """Main entry point"""
    import argparse
    # argparse.BooleanOptionalAction provides --flag / --no-flag

    parser = argparse.ArgumentParser(
        description='F1 Master Pipeline - Automated Analytics Orchestration'
    )
    parser.add_argument(
        '--session',
        help='Specific session to process (e.g., Abu_Dhabi_Grand_Prix_Race)',
        default=None
    )
    parser.add_argument(
        '--year',
        type=int,
        help='Year of the event (e.g., 2025)',
        default=None
    )
    parser.add_argument(
        '--event',
        help='Event/Grand Prix name (e.g., Monaco, Bahrain, Abu_Dhabi)',
        default=None
    )
    parser.add_argument(
        '--session-type',
        choices=['FP1', 'FP2', 'FP3', 'Qualifying', 'Sprint', 'SQ', 'SprintQualifying', 'Race'],
        help='Session type (FP1, FP2, FP3, Qualifying, Sprint, SQ/SprintQualifying, Race)',
        default=None
    )
    parser.add_argument(
        '--ignore-isaccurate',
        action=argparse.BooleanOptionalAction,
        default=True,
        help='Pass --ignore-isaccurate to the classifier (default: enabled)'
    )
    parser.add_argument(
        '--upload-snowflake',
        action=argparse.BooleanOptionalAction,
        default=True,
        help='Pass --upload-snowflake to the classifier (default: enabled)'
    )
    parser.add_argument(
        '--testing',
        action='store_true',
        help='Process testing session data from OpenF1 (reads metadata from telemetry_out/session_meta.json)'
    )

    args = parser.parse_args()

    # Handle testing sessions
    if args.testing:
        # Read session metadata from OpenF1 fetcher output
        meta_files = list(Path('telemetry_out').rglob('session_meta.json'))
        meta_file = meta_files[0] if meta_files else None
        if not meta_file or not meta_file.exists():
            print("Error: No testing session metadata found in telemetry_out/session_meta.json")
            print("Run openf1_fetcher.py or run_testing_pipeline.py first to fetch testing data")
            sys.exit(1)

        with open(meta_file, 'r') as f:
            meta = json.load(f)

        # Construct session name from metadata
        event_name = meta['event_name'].replace(' ', '_')
        session_name_part = meta['session_name'].replace(' ', '_')
        session_name = f"{event_name}_{session_name_part}"

        year = meta.get('year')
        event = meta.get('event_name')
        session_type = meta.get('session_name')

        print(f"Processing testing session: {session_name}")
        print(f"  Year: {year}")
        print(f"  Event: {event}")
        print(f"  Session: {session_type}")
        print(f"  Date: {meta.get('session_date')}")
        print(f"  Source: OpenF1(Session Key: {meta.get('openf1_session_key')})")
        print()

    else:
        # Regular race weekend processing
        # Determine session name and parameters
        session_name = None
        year = args.year
        event = args.event
        session_type = args.session_type

        if args.session:
            # Use explicit session name if provided
            session_name = args.session
        elif args.event and args.session_type:
            # Construct session name from components
            # Format: Event_Grand_Prix_SessionType (e.g., Monaco_Grand_Prix_FP3)
            event_formatted = args.event.replace(' ', '_').replace('-', '_')
            if not event_formatted.endswith('_Grand_Prix') and 'Grand_Prix' not in event_formatted:
                event_formatted = f"{event_formatted}_Grand_Prix"
            session_name = f"{event_formatted}_{args.session_type}"

    # Create and run pipeline with all parameters
    pipeline = MasterPipeline(
        session_name=session_name,
        year=year,
        event=event,
        session_type=session_type,
        classifier_ignore_isaccurate=args.ignore_isaccurate,
        upload_snowflake=args.upload_snowflake,
        is_testing=args.testing
    )
    success = pipeline.run_full_pipeline()

    # Exit with appropriate code for scheduler monitoring
    sys.exit(0 if success else 1)


if __name__ == '__main__':
    main()
