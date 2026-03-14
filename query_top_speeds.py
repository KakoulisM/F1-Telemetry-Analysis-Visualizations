"""
Query Top Speed Trap Rankings from Snowflake
Shows the fastest drivers through the speed trap for a given event
"""
import snowflake.connector
from snowflake_config import get_snowflake_connection
import pandas as pd

def query_top_speeds(event_name='Bahrain Grand Prix', session_name='Race', year=2025):
    """Query top speed trap rankings"""
    
    conn = get_snowflake_connection()
    cursor = conn.cursor()
    
    print("\n" + "="*70)
    print(f"TOP SPEED TRAP RANKINGS")
    print(f"Event: {event_name} {year}")
    print(f"Session: {session_name}")
    print("="*70 + "\n")
    
    # Query 1: Overall Top Speeds from driver summary
    query1 = f"""
    SELECT 
        Driver,
        MaxSpeedTrap as TopSpeed_kmh,
        BestLapTime,
        TotalLaps,
        EventName,
        SessionName
    FROM viz_driver_summary
    WHERE EventName = '{event_name}'
      AND SessionName = '{session_name}'
    ORDER BY MaxSpeedTrap DESC
    """
    
    print("📊 OVERALL TOP SPEEDS (km/h)")
    print("-" * 70)
    
    df1 = pd.read_sql(query1, conn)
    
    if df1.empty:
        print(f"⚠️  No data found for {event_name} - {session_name}")
        print("Available events:")
        
        # Show available events
        available_query = """
        SELECT DISTINCT EventName, SessionName, Year
        FROM viz_driver_summary
        ORDER BY Year DESC, EventName, SessionName
        LIMIT 20
        """
        available_df = pd.read_sql(available_query, conn)
        print(available_df.to_string(index=False))
    else:
        # Display rankings
        df1['Rank'] = range(1, len(df1) + 1)
        print(df1[['Rank', 'Driver', 'TopSpeed_kmh', 'BestLapTime', 'TotalLaps']].to_string(index=False))
        
        print("\n" + "="*70)
        print(f"🏆 Fastest: {df1.iloc[0]['Driver']} - {df1.iloc[0]['TopSpeed_kmh']:.2f} km/h")
        print(f"📈 Speed Range: {df1['TopSpeed_kmh'].min():.2f} - {df1['TopSpeed_kmh'].max():.2f} km/h")
        print(f"📉 Speed Spread: {df1['TopSpeed_kmh'].max() - df1['TopSpeed_kmh'].min():.2f} km/h")
        
        # Query 2: Best laps with speed trap details
        print("\n" + "="*70)
        print("🏁 BEST LAPS WITH SPEED TRAP DATA")
        print("-" * 70)
        
        query2 = f"""
        SELECT 
            Driver,
            LapNumber,
            LapTime,
            SpeedST as SpeedTrap_kmh,
            Compound,
            TyreLife,
            SpeedI1 as Speed_Intermediate1,
            SpeedI2 as Speed_Intermediate2
        FROM viz_best_laps
        WHERE EventName = '{event_name}'
          AND SessionName = '{session_name}'
        ORDER BY SpeedST DESC
        LIMIT 10
        """
        
        df2 = pd.read_sql(query2, conn)
        print(df2.to_string(index=False))
    
    cursor.close()
    conn.close()
    
    print("\n" + "="*70)
    print("Query complete!")
    print("="*70 + "\n")
    
    return df1 if not df1.empty else None


if __name__ == '__main__':
    import argparse
    
    parser = argparse.ArgumentParser(description='Query top speed trap data')
    parser.add_argument('--event', default='Bahrain Grand Prix', help='Event name')
    parser.add_argument('--session', default='Race', help='Session name')
    parser.add_argument('--year', type=int, default=2025, help='Year')
    
    args = parser.parse_args()
    
    query_top_speeds(args.event, args.session, args.year)
