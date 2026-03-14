"""
Racing Line Analyzer - Optimal Racing Line Detection & Delta Analysis
Identifies fastest lines per corner and provides actionable setup/driver feedback
"""
import pandas as pd
import numpy as np
from pathlib import Path
import json
from datetime import datetime
from pipeline_logger import get_logger
from snowflake_config import SNOWFLAKE_CONFIG
from typing import Dict, List, Tuple
import matplotlib.pyplot as plt
import seaborn as sns


def get_snowflake_connection(schema='PUBLIC'):
    """Create Snowflake connection"""
    import snowflake.connector
    
    conn = snowflake.connector.connect(
        account=SNOWFLAKE_CONFIG['account'],
        user=SNOWFLAKE_CONFIG['user'],
        password=SNOWFLAKE_CONFIG['password'],
        database=SNOWFLAKE_CONFIG['database'],
        warehouse=SNOWFLAKE_CONFIG['warehouse'],
        schema=schema,
        role=SNOWFLAKE_CONFIG['role']
    )
    return conn


def write_to_snowflake(df, table_name, conn):
    """Write DataFrame to Snowflake"""
    from snowflake.connector.pandas_tools import write_pandas
    
    df_copy = df.copy()
    if not isinstance(df_copy.index, pd.RangeIndex):
        df_copy = df_copy.reset_index(drop=True)
    
    df_copy.columns = [str(col).upper() for col in df_copy.columns]
    
    # Cast boolean columns to int — Arrow/Snowflake write_pandas rejects bool dtype
    bool_cols = df_copy.select_dtypes(include='bool').columns.tolist()
    for col in bool_cols:
        df_copy[col] = df_copy[col].fillna(False).astype(int)
    # Also catch object columns whose values are Python bools or float 0.0/1.0 (or a mix)
    for col in df_copy.select_dtypes(include='object').columns:
        sample = df_copy[col].dropna()
        if sample.empty:
            continue
        if sample.apply(lambda x: isinstance(x, bool) or (isinstance(x, (int, float)) and x in (0, 1, 0.0, 1.0))).all():
            df_copy[col] = pd.to_numeric(
                df_copy[col].map(lambda x: int(x) if isinstance(x, bool) else x),
                errors='coerce'
            ).fillna(0).astype(int)

    success, nchunks, nrows, _ = write_pandas(
        conn=conn,
        df=df_copy,
        table_name=table_name.upper(),
        auto_create_table=True,
        overwrite=True
    )
    
    return success, nrows


class RacingLineAnalyzer:
    """Analyzes corner telemetry to identify optimal racing lines and provide delta analysis"""
    
    def __init__(self, telemetry_dir: str = 'telemetry_out'):
        self.telemetry_dir = Path(telemetry_dir)
        self.logger = get_logger()
        self.corner_data = None
        self.lap_data = None
        self.optimal_lines = {}
        self.delta_analysis = {}
        
    def load_session_data(self, session_prefix: str) -> bool:
        """
        Load all corner and lap data for a session using fixed telemetry file structure.
        Args:
            session_prefix: e.g., 'Australian_Grand_Prix_FP2'
        """
        try:
            # Parse session_prefix into year, event, session_type if possible
            parts = session_prefix.split('_')
            year = None
            event = None
            session_type = None
            # Try to extract year from self.telemetry_dir if possible
            # Fallback: use self.telemetry_dir as base
            base_dir = Path(self.telemetry_dir)
            # If session_prefix is like Australian_Grand_Prix_FP2, try to split
            if hasattr(self, 'year') and self.year:
                year = str(self.year)
            if len(parts) >= 3:
                event = '_'.join(parts[:-1])
                session_type = parts[-1]
            else:
                event = session_prefix
                session_type = ''
            event_clean = str(event).replace(' ', '_').replace('-', '_')
            session_type_clean = str(session_type).replace(' ', '_').replace('-', '_')
            # Build telemetry path
            telemetry_path = base_dir / year / event_clean / session_type_clean if year else base_dir
            # Exclude circuit_corners.csv (track layout data) — only want per-driver corner files
            corner_files = [f for f in telemetry_path.glob("*corners.csv") if f.name != 'circuit_corners.csv']
            lap_files = list(telemetry_path.glob(f"*laps.csv"))
            if not corner_files:
                # fallback: recursive search, still excluding circuit layout file
                corner_files = [f for f in base_dir.rglob("*corners.csv") if f.name != 'circuit_corners.csv']
            if not lap_files:
                lap_files = list(base_dir.rglob(f"*laps.csv"))

            if not corner_files:
                self.logger.error(f"No corner files found for {session_prefix}")
                return False

            # Load all corner data
            corner_dfs = []
            for file in corner_files:
                driver = file.stem.split('_')[-2]  # Extract driver code
                df = pd.read_csv(file)
                df['Driver'] = driver
                df['Session'] = session_prefix
                corner_dfs.append(df)

            self.corner_data = pd.concat(corner_dfs, ignore_index=True)

            # Load all lap data
            lap_dfs = []
            for file in lap_files:
                driver = file.stem.split('_')[-2]
                df = pd.read_csv(file)
                df['Driver'] = driver
                lap_dfs.append(df)

            self.lap_data = pd.concat(lap_dfs, ignore_index=True)

            self.logger.info(f"[OK] Loaded {len(corner_dfs)} drivers, {len(self.corner_data)} corners")
            return True

        except Exception as e:
            self.logger.error(f"Failed to load session data: {e}")
            return False
    
    def identify_optimal_lines(self, min_laps: int = 3) -> pd.DataFrame:
        """
        Identify the fastest racing line for each corner across all drivers
        Returns DataFrame with optimal parameters per corner
        """
        if self.corner_data is None:
            self.logger.error("No corner data loaded")
            return None
        
        optimal_lines = []
        
        # Filter out first lap (out lap) and invalid data
        valid_corners = self.corner_data[
            (self.corner_data['LapNumber'] > 1) & 
            (self.corner_data['ApexSpeed'].notna()) &
            (self.corner_data['ExitSpeed'].notna())
        ].copy()
        
        # Calculate corner efficiency metrics
        valid_corners['CornerSpeed'] = (
            valid_corners['EntrySpeed'] + 
            valid_corners['ApexSpeed'] + 
            valid_corners['ExitSpeed']
        ) / 3
        
        valid_corners['SpeedLoss'] = (
            valid_corners['EntrySpeed'] - valid_corners['ApexSpeed']
        )
        
        valid_corners['SpeedGain'] = (
            valid_corners['ExitSpeed'] - valid_corners['ApexSpeed']
        )
        
        # Group by corner and find optimal characteristics
        for corner_num in sorted(valid_corners['Corner'].unique()):
            corner_subset = valid_corners[valid_corners['Corner'] == corner_num]
            
            if len(corner_subset) < min_laps:
                continue
            
            # Find the fastest average corner speed (best overall)
            fastest_idx = corner_subset['CornerSpeed'].idxmax()
            fastest = corner_subset.loc[fastest_idx]
            
            # Statistical benchmarks
            optimal = {
                'Corner': corner_num,
                'CornerAngle': corner_subset['CornerAngle'].median(),
                
                # Optimal speeds (from fastest execution)
                'OptimalEntrySpeed': fastest['EntrySpeed'],
                'OptimalApexSpeed': fastest['ApexSpeed'],
                'OptimalExitSpeed': fastest['ExitSpeed'],
                
                # Benchmark driver and lap
                'BenchmarkDriver': fastest['Driver'],
                'BenchmarkLap': fastest['LapNumber'],
                
                # Statistical ranges (95th percentile)
                'TopEntrySpeedP95': corner_subset['EntrySpeed'].quantile(0.95),
                'TopApexSpeedP95': corner_subset['ApexSpeed'].quantile(0.95),
                'TopExitSpeedP95': corner_subset['ExitSpeed'].quantile(0.95),
                
                # Throttle/brake characteristics
                'OptimalEntryThrottle': fastest['EntryThrottle'],
                'OptimalApexThrottle': fastest['ApexThrottle'],
                'OptimalExitThrottle': fastest['ExitThrottle'],
                
                # Gear strategy
                'OptimalEntryGear': fastest['EntryGear'],
                'OptimalApexGear': fastest['ApexGear'],
                'OptimalExitGear': fastest['ExitGear'],
                
                # Performance metrics
                'MinSpeedLoss': corner_subset['SpeedLoss'].min(),
                'MaxSpeedGain': corner_subset['SpeedGain'].max(),
                
                # Sample size
                'SampleSize': len(corner_subset),
            }
            
            optimal_lines.append(optimal)
        
        self.optimal_lines = pd.DataFrame(optimal_lines)
        self.logger.info(f"[OK] Identified optimal lines for {len(self.optimal_lines)} corners")
        
        return self.optimal_lines
    
    def calculate_driver_deltas(self, target_driver: str = None) -> pd.DataFrame:
        """
        Calculate delta between each driver's corner execution vs optimal line
        If target_driver specified, focus on that driver
        """
        if self.optimal_lines is None or self.optimal_lines.empty:
            self.logger.error("Must run identify_optimal_lines() first")
            return None
        
        valid_corners = self.corner_data[
            (self.corner_data['LapNumber'] > 1) & 
            (self.corner_data['ApexSpeed'].notna())
        ].copy()
        
        if target_driver:
            valid_corners = valid_corners[valid_corners['Driver'] == target_driver]
        
        # Merge with optimal lines
        merged = valid_corners.merge(
            self.optimal_lines[['Corner', 'OptimalEntrySpeed', 'OptimalApexSpeed', 
                               'OptimalExitSpeed', 'BenchmarkDriver']],
            on='Corner',
            how='left'
        )
        
        # Calculate deltas (negative = slower than optimal)
        merged['EntrySpeedDelta'] = merged['EntrySpeed'] - merged['OptimalEntrySpeed']
        merged['ApexSpeedDelta'] = merged['ApexSpeed'] - merged['OptimalApexSpeed']
        merged['ExitSpeedDelta'] = merged['ExitSpeed'] - merged['OptimalExitSpeed']
        
        merged['TotalSpeedDelta'] = (
            merged['EntrySpeedDelta'] + 
            merged['ApexSpeedDelta'] + 
            merged['ExitSpeedDelta']
        )
        
        # Categorize performance
        merged['PerformanceCategory'] = merged['TotalSpeedDelta'].apply(
            lambda x: 'Optimal' if x >= -5 else ('Good' if x >= -15 else 'Needs Work')
        )
        
        self.delta_analysis = merged
        self.logger.info(f"[OK] Calculated deltas for {len(merged)} corner executions")
        
        return merged
    
    def generate_driver_report(self, driver: str) -> Dict:
        """
        Generate comprehensive corner-by-corner report for a specific driver
        """
        if self.delta_analysis is None or self.delta_analysis.empty:
            self.logger.error("Must run calculate_driver_deltas() first")
            return None
        
        driver_data = self.delta_analysis[self.delta_analysis['Driver'] == driver].copy()
        
        if driver_data.empty:
            self.logger.warning(f"No data found for driver {driver}")
            return None
        
        # Aggregate by corner
        corner_summary = driver_data.groupby('Corner').agg({
            'EntrySpeedDelta': 'mean',
            'ApexSpeedDelta': 'mean',
            'ExitSpeedDelta': 'mean',
            'TotalSpeedDelta': 'mean',
            'LapNumber': 'count'
        }).rename(columns={'LapNumber': 'Attempts'})
        
        corner_summary = corner_summary.round(2)
        
        # Identify problem corners (worst 3)
        worst_corners = corner_summary.nsmallest(3, 'TotalSpeedDelta')
        
        # Identify strength corners (best 3)
        best_corners = corner_summary.nlargest(3, 'TotalSpeedDelta')
        
        # Overall statistics
        report = {
            'Driver': driver,
            'TotalCorners': len(corner_summary),
            'AverageEntryDelta': corner_summary['EntrySpeedDelta'].mean(),
            'AverageApexDelta': corner_summary['ApexSpeedDelta'].mean(),
            'AverageExitDelta': corner_summary['ExitSpeedDelta'].mean(),
            'OverallSpeedDeficit': corner_summary['TotalSpeedDelta'].mean(),
            'WorstCorners': worst_corners.to_dict('index'),
            'BestCorners': best_corners.to_dict('index'),
            'CornerBreakdown': corner_summary.to_dict('index')
        }
        
        return report
    
    def track_sector_comparison(self) -> pd.DataFrame:
        """
        Aggregate corner deltas by track sector for high-level analysis
        """
        if self.delta_analysis is None:
            return None
        
        # Assign sectors (simple division by corner number)
        max_corner = self.delta_analysis['Corner'].max()
        sector_size = max_corner / 3
        
        self.delta_analysis['Sector'] = self.delta_analysis['Corner'].apply(
            lambda x: 1 if x <= sector_size else (2 if x <= 2*sector_size else 3)
        )
        
        sector_summary = self.delta_analysis.groupby(['Driver', 'Sector']).agg({
            'TotalSpeedDelta': ['mean', 'std', 'min', 'max'],
            'Corner': 'count'
        }).round(2)
        
        sector_summary.columns = ['_'.join(col).strip() for col in sector_summary.columns]
        
        return sector_summary.reset_index()
    
    def export_to_snowflake(self, conn) -> bool:
        """Export analysis results to Snowflake"""
        try:
            if self.optimal_lines is not None and not self.optimal_lines.empty:
                success, rows = write_to_snowflake(
                    self.optimal_lines, 
                    'RACING_LINE_OPTIMAL_CORNERS', 
                    conn
                )
                self.logger.info(f"[OK] Exported {rows} optimal corner benchmarks to Snowflake")
            
            if self.delta_analysis is not None and not self.delta_analysis.empty:
                success, rows = write_to_snowflake(
                    self.delta_analysis, 
                    'RACING_LINE_DRIVER_DELTAS', 
                    conn
                )
                self.logger.info(f"[OK] Exported {rows} driver delta records to Snowflake")
            
            return True
            
        except Exception as e:
            self.logger.error(f"Snowflake export failed: {e}")
            return False
    
    def visualize_corner_deltas(self, driver: str, output_dir: str = 'telemetry_out/viz'):
        """
        Create visualization of corner-by-corner deltas for a driver
        """
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)
        
        driver_data = self.delta_analysis[self.delta_analysis['Driver'] == driver].copy()
        
        if driver_data.empty:
            self.logger.warning(f"No data to visualize for {driver}")
            return
        
        # Corner summary
        corner_avg = driver_data.groupby('Corner').agg({
            'EntrySpeedDelta': 'mean',
            'ApexSpeedDelta': 'mean',
            'ExitSpeedDelta': 'mean'
        })
        
        # Create figure
        fig, axes = plt.subplots(2, 1, figsize=(14, 10))
        
        # Plot 1: Entry/Apex/Exit deltas by corner
        ax1 = axes[0]
        corner_avg.plot(kind='bar', ax=ax1, color=['#FF6B6B', '#4ECDC4', '#45B7D1'])
        ax1.axhline(y=0, color='black', linestyle='--', linewidth=1)
        ax1.set_title(f'{driver} - Corner Speed Deltas vs Optimal Line', fontsize=14, weight='bold')
        ax1.set_xlabel('Corner Number', fontsize=12)
        ax1.set_ylabel('Speed Delta (km/h)', fontsize=12)
        ax1.legend(['Entry', 'Apex', 'Exit'], loc='lower right')
        ax1.grid(axis='y', alpha=0.3)
        
        # Plot 2: Total speed delta heatmap
        ax2 = axes[1]
        total_delta = driver_data.groupby('Corner')['TotalSpeedDelta'].mean()
        colors = ['#d73027' if x < -15 else '#fee08b' if x < -5 else '#1a9850' 
                  for x in total_delta.values]
        
        ax2.bar(total_delta.index, total_delta.values, color=colors)
        ax2.axhline(y=0, color='black', linestyle='--', linewidth=1)
        ax2.axhline(y=-15, color='red', linestyle=':', linewidth=1, alpha=0.5)
        ax2.set_title(f'{driver} - Total Speed Performance (Red < -15 km/h = Needs Work)', 
                     fontsize=14, weight='bold')
        ax2.set_xlabel('Corner Number', fontsize=12)
        ax2.set_ylabel('Total Delta (km/h)', fontsize=12)
        ax2.grid(axis='y', alpha=0.3)
        
        plt.tight_layout()
        
        output_file = output_path / f'{driver}_corner_analysis.png'
        plt.savefig(output_file, dpi=150, bbox_inches='tight')
        plt.close()
        
        self.logger.info(f"[OK] Saved visualization: {output_file}")


def analyze_session(session_prefix: str, target_driver: str = None, 
                    export_snowflake: bool = True):
    """
    Main execution function - analyze a full session
    Args:
        session_prefix: e.g., 'Abu_Dhabi_Grand_Prix_Race'
        target_driver: Optional specific driver to focus on
        export_snowflake: Whether to export results to Snowflake
    """
    logger = get_logger()
    analyzer = RacingLineAnalyzer()
    
    logger.info(f"[START] Racing Line Analysis: {session_prefix}")
    
    # Load data
    if not analyzer.load_session_data(session_prefix):
        logger.error("Failed to load session data")
        return None
    
    # Identify optimal lines
    optimal = analyzer.identify_optimal_lines()
    if optimal is None:
        return None
    
    logger.info(f"\n{'='*60}")
    logger.info("OPTIMAL CORNER BENCHMARKS")
    logger.info(f"{'='*60}")
    # Print optimal benchmark for every corner (robust formatting)
    def _fmt_speed(val):
        try:
            if pd.isna(val):
                return 'N/A'
            return f"{float(val):.0f}"
        except Exception:
            return 'N/A'

    # Ensure corners are printed in order
    try:
        optimal_sorted = optimal.sort_values('Corner')
    except Exception:
        optimal_sorted = optimal

    for _, corner in optimal_sorted.iterrows():
        try:
            cnum = corner.get('Corner', '')
            entry = _fmt_speed(corner.get('OptimalEntrySpeed'))
            apex = _fmt_speed(corner.get('OptimalApexSpeed'))
            exitv = _fmt_speed(corner.get('OptimalExitSpeed'))
            bench = corner.get('BenchmarkDriver', '')
            logger.info(f"Corner {cnum}: Entry {entry} km/h | Apex {apex} km/h | Exit {exitv} km/h | Benchmark: {bench}")
        except Exception as e:
            logger.warning(f"Failed to print optimal for corner row: {e}")
    
    # Calculate deltas
    deltas = analyzer.calculate_driver_deltas(target_driver)
    
    # Generate reports for each driver
    drivers = [target_driver] if target_driver else analyzer.corner_data['Driver'].unique()
    
    for driver in drivers:
        report = analyzer.generate_driver_report(driver)
        if report:
            logger.info(f"\n{'='*60}")
            logger.info(f"DRIVER REPORT: {driver}")
            logger.info(f"{'='*60}")
            logger.info(f"Overall Speed Deficit: {report['OverallSpeedDeficit']:.2f} km/h")
            logger.info(f"Entry Avg: {report['AverageEntryDelta']:.2f} km/h")
            logger.info(f"Apex Avg: {report['AverageApexDelta']:.2f} km/h")
            logger.info(f"Exit Avg: {report['AverageExitDelta']:.2f} km/h")
            
            logger.info(f"\n[WARNING] WORST 3 CORNERS (Priority Work):")
            for corner, data in report['WorstCorners'].items():
                logger.info(f"  Corner {corner}: {data['TotalSpeedDelta']:.2f} km/h deficit")
            
            logger.info(f"\n[OK] BEST 3 CORNERS:")
            for corner, data in report['BestCorners'].items():
                logger.info(f"  Corner {corner}: {data['TotalSpeedDelta']:.2f} km/h")
            
            # Generate visualization
            analyzer.visualize_corner_deltas(driver)
    
    # Sector comparison
    sector_comp = analyzer.track_sector_comparison()
    if sector_comp is not None:
        logger.info(f"\n{'='*60}")
        logger.info("SECTOR COMPARISON")
        logger.info(f"{'='*60}")
        logger.info(f"\n{sector_comp.to_string()}")
    
    # Export to Snowflake
    if export_snowflake:
        try:
            # Recursively search for session_meta.json
            meta_files = list(Path('telemetry_out').rglob('session_meta.json'))
            meta_file = meta_files[0] if meta_files else None
            schema_name = None
            if meta_file and meta_file.exists():
                with open(meta_file, 'r') as f:
                    meta = json.load(f)
                    event_name = meta.get('event_name', '').replace(' ', '_')
                    session_name = meta.get('session_name', '').replace(' ', '_')
                    year = meta.get('year', '')
                    if event_name and session_name and year:
                        schema_name = f"{event_name}_{session_name}_{year}"

            conn = get_snowflake_connection(schema=schema_name)
            analyzer.export_to_snowflake(conn)
            conn.close()
        except Exception as e:
            logger.warning(f"Snowflake export skipped: {e}")
    
    logger.info(f"\n{'='*60}")
    logger.info("[SUCCESS] RACING LINE ANALYSIS COMPLETE")
    logger.info(f"{'='*60}")
    
    return analyzer


if __name__ == '__main__':
    import sys
    
    # Example usage
    if len(sys.argv) > 1:
        session = sys.argv[1]
        driver = sys.argv[2] if len(sys.argv) > 2 else None
        analyze_session(session, driver)
    else:
        print("Usage: python racing_line_analyzer.py <session_prefix> [driver_code]")
        print("Example: python racing_line_analyzer.py Abu_Dhabi_Grand_Prix_Race VER")
