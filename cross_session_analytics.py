"""
Cross-Session Analytics - Historical aggregations across multiple F1 sessions
"""
import pandas as pd
import numpy as np
from pathlib import Path
from snowflake_config import SNOWFLAKE_CONFIG
from pipeline_logger import get_logger
import math


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
    
    success, nchunks, nrows, _ = write_pandas(
        conn=conn,
        df=df_copy,
        table_name=table_name.upper(),
        auto_create_table=True,
        overwrite=True
    )
    
    return success, nrows


def query_snowflake(conn, query):
    """Execute query and return DataFrame using cursor to avoid pandas warning"""
    cursor = conn.cursor()
    cursor.execute(query)
    columns = [desc[0] for desc in cursor.description]
    data = cursor.fetchall()
    cursor.close()
    return pd.DataFrame(data, columns=columns)


def generate_cross_session_analytics():
    def find_all_session_dirs(base_dir='telemetry_out'):
        """Recursively find all session directories in telemetry_out/{year}/{event}/{session_type}/"""
        session_dirs = []
        for year_dir in Path(base_dir).iterdir():
            if not year_dir.is_dir():
                continue
            for event_dir in year_dir.iterdir():
                if not event_dir.is_dir():
                    continue
                for session_dir in event_dir.iterdir():
                    if session_dir.is_dir():
                        session_dirs.append(session_dir)
        return session_dirs

    def load_all_lap_csvs(base_dir='telemetry_out'):
        """Load all *_laps.csv files from all session directories"""
        lap_dfs = []
        for session_dir in find_all_session_dirs(base_dir):
            for lap_file in session_dir.glob('*_laps.csv'):
                try:
                    df = pd.read_csv(lap_file)
                    lap_dfs.append(df)
                except Exception:
                    continue
        return lap_dfs
    """
    Generate analytics tables that aggregate data across multiple sessions
    """
    logger = get_logger()
    logger.info("\n" + "="*70)
    logger.info("CROSS-SESSION ANALYTICS - Aggregating historical data")
    logger.info("="*70 + "\n")

    # Helper: parse lap time strings/timedeltas into seconds
    def parse_laptime_seconds(x):
        try:
            if pd.isna(x):
                return np.nan
        except Exception:
            pass

        # If already numeric
        if isinstance(x, (int, float)):
            return float(x)

        # If pandas Timedelta-like
        try:
            if hasattr(x, 'total_seconds'):
                return float(x.total_seconds())
        except Exception:
            pass

        s = str(x)
        if 'day' in s:
            try:
                return pd.to_timedelta(s).total_seconds()
            except Exception:
                pass

        parts = s.split(':')
        try:
            if len(parts) == 2:
                minutes = int(parts[0])
                seconds = float(parts[1])
                return minutes * 60.0 + seconds
            if len(parts) == 3:
                hours = int(parts[0])
                minutes = int(parts[1])
                seconds = float(parts[2])
                return hours * 3600.0 + minutes * 60.0 + seconds
        except Exception:
            pass

        try:
            return float(s)
        except Exception:
            return np.nan

    # Helper: format seconds into M:SS.mmm
    def format_seconds_to_mmss_ms(sec):
        try:
            if sec is None or (isinstance(sec, float) and math.isnan(sec)):
                return None
        except Exception:
            pass

        try:
            total = round(float(sec), 3)
        except Exception:
            return None

        minutes = int(total // 60)
        seconds = int(total % 60)
        ms = int(round((total - (minutes * 60) - seconds) * 1000))
        if ms == 1000:
            ms = 0
            seconds += 1
            if seconds == 60:
                seconds = 0
                minutes += 1

        return f"{minutes}:{seconds:02d}.{ms:03d}"
    
    conn = get_snowflake_connection(schema='PUBLIC')
    
    # Get all session schemas
    logger.info("Discovering available sessions...")
    schemas_query = f"""
    SELECT SCHEMA_NAME 
    FROM {SNOWFLAKE_CONFIG['database']}.INFORMATION_SCHEMA.SCHEMATA 
    WHERE SCHEMA_NAME NOT IN ('INFORMATION_SCHEMA', 'PUBLIC')
    ORDER BY SCHEMA_NAME
    """
    
    schemas_df = query_snowflake(conn, schemas_query)
    schemas = schemas_df['SCHEMA_NAME'].tolist()
    
    if not schemas:
        logger.warning("No session schemas found. Run fetch_pipeline.py first!")
        return
    
    logger.info(f"Found {len(schemas)} session(s): {', '.join(schemas[:5])}{'...' if len(schemas) > 5 else ''}\n")
    
    # ============================================
    # CROSS 1: SEASON CHAMPIONSHIP STANDINGS
    # ============================================
    logger.info("[AGGREGATING] Building season championship standings...")
    
    all_best_laps = []
    for schema in schemas:
        try:
            query = f"""
            SELECT 
                DRIVER,
                LAPTIME,
                COMPOUND,
                SPEEDI1,
                SPEEDI2,
                SPEEDST,
                '{schema}' as SESSION_SCHEMA
            FROM {SNOWFLAKE_CONFIG['database']}.{schema}.VIZ_BEST_LAPS
            """
            laps_df = query_snowflake(conn, query)
            all_best_laps.append(laps_df)
        except Exception as e:
            logger.warning(f"Skipping {schema}: {e}")
    
    if all_best_laps:
        combined = pd.concat(all_best_laps, ignore_index=True)

        # Ensure LAPTIME is numeric (seconds). Some sessions store as timedeltas/strings.
        def parse_laptime_seconds(x):
            try:
                if pd.isna(x):
                    return np.nan
            except Exception:
                pass

            # If already numeric
            if isinstance(x, (int, float)):
                return float(x)

            # If pandas Timedelta
            try:
                if hasattr(x, 'total_seconds'):
                    return float(x.total_seconds())
            except Exception:
                pass

            s = str(x)
            # Handle pandas timedelta string like '0 days 00:01:16.258000'
            if 'day' in s:
                try:
                    return pd.to_timedelta(s).total_seconds()
                except Exception:
                    pass

            # Handle mm:ss[.fff] or hh:mm:ss formats
            parts = s.split(':')
            try:
                if len(parts) == 2:
                    minutes = int(parts[0])
                    seconds = float(parts[1])
                    return minutes * 60.0 + seconds
                if len(parts) == 3:
                    hours = int(parts[0])
                    minutes = int(parts[1])
                    seconds = float(parts[2])
                    return hours * 3600.0 + minutes * 60.0 + seconds
            except Exception:
                pass

            # Fallback numeric parse
            try:
                return float(s)
            except Exception:
                return np.nan

        combined['LAPTIME_SECONDS'] = combined['LAPTIME'].apply(parse_laptime_seconds)

        # Extract event and session from schema name
        combined['EVENT'] = combined['SESSION_SCHEMA'].str.rsplit('_', n=2).str[0].str.replace('_', ' ')
        combined['SESSION'] = combined['SESSION_SCHEMA'].str.rsplit('_', n=2).str[1]
        combined['YEAR'] = combined['SESSION_SCHEMA'].str.rsplit('_', n=1).str[-1]

        # Championship points system (simplified: top 10 in each session get points)
        combined_sorted = combined.sort_values(['SESSION_SCHEMA', 'LAPTIME_SECONDS'])
        combined_sorted['POSITION'] = combined_sorted.groupby('SESSION_SCHEMA')['LAPTIME_SECONDS'].rank(method='min')
        
        # Points: 25, 18, 15, 12, 10, 8, 6, 4, 2, 1
        points_map = {1: 25, 2: 18, 3: 15, 4: 12, 5: 10, 6: 8, 7: 6, 8: 4, 9: 2, 10: 1}
        combined_sorted['POINTS'] = combined_sorted['POSITION'].map(points_map).fillna(0)
        
        # Aggregate standings
        standings = combined_sorted.groupby('DRIVER').agg({
            'POINTS': 'sum',
            'POSITION': ['count', 'mean'],
            'LAPTIME_SECONDS': 'mean',
            'EVENT': 'nunique'
        }).reset_index()

        standings.columns = ['DRIVER', 'TOTAL_POINTS', 'SESSIONS_COUNT', 'AVG_POSITION', 'AVG_LAPTIME_SECONDS', 'EVENTS_COUNT']
        # Format average laptime as M:SS.mmm
        standings['AVG_LAPTIME'] = standings['AVG_LAPTIME_SECONDS'].apply(format_seconds_to_mmss_ms)
        standings = standings.sort_values('TOTAL_POINTS', ascending=False).reset_index(drop=True)
        standings['CHAMPIONSHIP_POSITION'] = standings.index + 1
        
        success, nrows = write_to_snowflake(standings, 'ANALYTICS_SEASON_STANDINGS', conn)
        logger.info(f"  [OK] ANALYTICS_SEASON_STANDINGS ({nrows} drivers)")
    
    # ============================================
    # CROSS 2: DRIVER PERFORMANCE TRENDS
    # ============================================
    logger.info("\n[AGGREGATING] Analyzing driver performance trends...")
    
    all_laps = []
    for schema in schemas:
        try:
            query = f"""
            SELECT 
                DRIVER,
                LAPNUMBER,
                LAPTIMESECONDS,
                COMPOUND,
                '{schema}' as SESSION_SCHEMA
            FROM {SNOWFLAKE_CONFIG['database']}.{schema}.VIZ_LAP_PROGRESSION
            """
            laps_df = query_snowflake(conn, query)
            all_laps.append(laps_df)
        except Exception as e:
            logger.warning(f"Skipping {schema}: {e}")
    
    if all_laps:
        combined = pd.concat(all_laps, ignore_index=True)
        combined['EVENT'] = combined['SESSION_SCHEMA'].str.rsplit('_', n=2).str[0].str.replace('_', ' ')

        # Calculate consistency metrics per driver per event
        trends = combined.groupby(['DRIVER', 'EVENT']).agg({
            'LAPTIMESECONDS': ['mean', 'std', 'min', 'count']
        }).reset_index()

        trends.columns = ['DRIVER', 'EVENT', 'AVG_LAPTIME_SECONDS', 'STD_LAPTIME', 'BEST_LAPTIME_SECONDS', 'LAPS_COMPLETED']
        # Format readable time strings
        trends['AVG_LAPTIME'] = trends['AVG_LAPTIME_SECONDS'].apply(format_seconds_to_mmss_ms)
        trends['BEST_LAPTIME'] = trends['BEST_LAPTIME_SECONDS'].apply(format_seconds_to_mmss_ms)
        trends['CONSISTENCY_SCORE'] = 1 - (trends['STD_LAPTIME'] / trends['AVG_LAPTIME_SECONDS'])
        trends = trends.sort_values(['DRIVER', 'EVENT'])

        success, nrows = write_to_snowflake(trends, 'ANALYTICS_DRIVER_TRENDS', conn)
        logger.info(f"  [OK] ANALYTICS_DRIVER_TRENDS ({nrows} driver-events)")
    
    # ============================================
    # CROSS 3: CIRCUIT-SPECIFIC RECORDS
    # ============================================
    logger.info("\n[AGGREGATING] Building circuit records...")
    
    if all_best_laps:
        combined = pd.concat(all_best_laps, ignore_index=True)
        combined['EVENT'] = combined['SESSION_SCHEMA'].str.rsplit('_', n=2).str[0].str.replace('_', ' ')
        combined['YEAR'] = combined['SESSION_SCHEMA'].str.rsplit('_', n=1).str[-1]

        # Ensure numeric lap time and pick best per event
        combined['LAPTIME_SECONDS'] = combined['LAPTIME'].apply(parse_laptime_seconds)
        circuit_records = combined.loc[combined.groupby('EVENT')['LAPTIME_SECONDS'].idxmin()].reset_index(drop=True)
        circuit_records = circuit_records[['EVENT', 'DRIVER', 'LAPTIME', 'LAPTIME_SECONDS', 'COMPOUND', 'SPEEDI1', 'SPEEDI2', 'SPEEDST', 'YEAR']]
        circuit_records = circuit_records.sort_values('LAPTIME_SECONDS')
        # Add readable lap time
        circuit_records['LAPTIME_STR'] = circuit_records['LAPTIME_SECONDS'].apply(format_seconds_to_mmss_ms)

        success, nrows = write_to_snowflake(circuit_records, 'ANALYTICS_CIRCUIT_RECORDS', conn)
        logger.info(f"  [OK] ANALYTICS_CIRCUIT_RECORDS ({nrows} circuits)")
    
    # ============================================
    # CROSS 4: HEAD-TO-HEAD RECORDS
    # ============================================
    logger.info("\n[AGGREGATING] Calculating head-to-head records...")
    
    if all_best_laps:
        combined = pd.concat(all_best_laps, ignore_index=True)
        combined['EVENT'] = combined['SESSION_SCHEMA'].str.rsplit('_', n=2).str[0].str.replace('_', ' ')
        
        # Get all sessions and create head-to-head matrix
        h2h_data = []
        for schema in combined['SESSION_SCHEMA'].unique():
            session_data = combined[combined['SESSION_SCHEMA'] == schema].sort_values('LAPTIME').head(10)
            session_data['POSITION'] = range(1, len(session_data) + 1)
            
            for i, row1 in session_data.iterrows():
                for j, row2 in session_data.iterrows():
                    if row1['DRIVER'] != row2['DRIVER'] and row1['POSITION'] < row2['POSITION']:
                        h2h_data.append({
                            'DRIVER1': row1['DRIVER'],
                            'DRIVER2': row2['DRIVER'],
                            'DRIVER1_WINS': 1,
                            'DRIVER2_WINS': 0
                        })
        
        if h2h_data:
            h2h_df = pd.DataFrame(h2h_data)
            h2h_summary = h2h_df.groupby(['DRIVER1', 'DRIVER2']).agg({
                'DRIVER1_WINS': 'sum',
                'DRIVER2_WINS': 'sum'
            }).reset_index()
            
            h2h_summary['TOTAL_BATTLES'] = h2h_summary['DRIVER1_WINS']
            h2h_summary['WIN_PERCENTAGE'] = (h2h_summary['DRIVER1_WINS'] / h2h_summary['TOTAL_BATTLES'] * 100).round(2)
            
            success, nrows = write_to_snowflake(h2h_summary, 'ANALYTICS_HEAD_TO_HEAD', conn)
            logger.info(f"  [OK] ANALYTICS_HEAD_TO_HEAD ({nrows} matchups)")
    
    # ============================================
    # CROSS 5: TIRE STRATEGY ANALYSIS
    # ============================================
    logger.info("\n[AGGREGATING] Analyzing tire strategies...")
    
    all_tire_data = []
    for schema in schemas:
        try:
            query = f"""
            SELECT 
                DRIVER,
                COMPOUND,
                TYRELIFE,
                AVGLAPTIME,
                '{schema}' as SESSION_SCHEMA
            FROM {SNOWFLAKE_CONFIG['database']}.{schema}.VIZ_TIRE_DEGRADATION
            """
            tire_df = query_snowflake(conn, query)
            all_tire_data.append(tire_df)
        except Exception as e:
            logger.warning(f"Skipping {schema}: {e}")
    
    if all_tire_data:
        combined = pd.concat(all_tire_data, ignore_index=True)
        combined['EVENT'] = combined['SESSION_SCHEMA'].str.rsplit('_', n=2).str[0].str.replace('_', ' ')
        
        # Average performance by compound
        tire_analysis = combined.groupby(['COMPOUND', 'TYRELIFE']).agg({
            'AVGLAPTIME': ['mean', 'std', 'count']
        }).reset_index()
        
        tire_analysis.columns = ['COMPOUND', 'TYRELIFE', 'AVG_TIME', 'STD_TIME', 'SAMPLES']
        tire_analysis = tire_analysis[tire_analysis['SAMPLES'] >= 5]  # Minimum sample size
        tire_analysis = tire_analysis.sort_values(['COMPOUND', 'TYRELIFE'])
        # Add formatted average lap time
        tire_analysis['AVG_TIME_STR'] = tire_analysis['AVG_TIME'].apply(format_seconds_to_mmss_ms)
        
        success, nrows = write_to_snowflake(tire_analysis, 'ANALYTICS_TIRE_STRATEGY', conn)
        logger.info(f"  [OK] ANALYTICS_TIRE_STRATEGY ({nrows} compound-life combinations)")
    
    conn.close()
    
    logger.info("\n" + "="*70)
    logger.info("[SUCCESS] CROSS-SESSION ANALYTICS COMPLETE")
    logger.info("="*70)
    logger.info("\n[DATA] New tables created in F1DATA.PUBLIC:")
    logger.info("  • ANALYTICS_SEASON_STANDINGS - Championship points")
    logger.info("  • ANALYTICS_DRIVER_TRENDS - Performance over time")
    logger.info("  • ANALYTICS_CIRCUIT_RECORDS - Track records")
    logger.info("  • ANALYTICS_HEAD_TO_HEAD - Driver comparisons")
    logger.info("  • ANALYTICS_TIRE_STRATEGY - Compound analysis")
    logger.info("\n[INFO] Use these for season-long dashboards and comparisons!\n")


if __name__ == '__main__':
    generate_cross_session_analytics()
