# F1 Telemetry Analytics Pipeline

Automated F1 data extraction and analytics pipeline with Snowflake cloud data warehouse.

## Setup

1. **Install dependencies:**
```powershell
pip install -r requirements.txt
```

2. **Configure Snowflake:**

Your Snowflake credentials are already configured in `snowflake_config.py`:
- Account: `SRAFERN-PH02712.snowflakecomputing.com`
- Database: `F1DATA`
- Schema: `PUBLIC`
- Warehouse: `COMPUTE_WH`

## Usage

**Run the complete pipeline:**
```powershell
python fetch_pipeline.py
```

This will:
1. Find the most recent F1 session
2. Extract telemetry for all drivers
3. Aggregate data for visualization
4. Load directly to Snowflake cloud data warehouse

## Output

### Snowflake Tables
All data loaded to: `F1DATA.PUBLIC.*`

**14 tables ready for BI tools:**
- `VIZ_LAP_PROGRESSION` - Lap-by-lap data
- `VIZ_BEST_LAPS` - Best lap leaderboard
- `VIZ_SECTOR_PERFORMANCE` - Sector comparison
- `VIZ_TIRE_DEGRADATION` - Tire analysis
- `VIZ_CORNER_COMPARISON` - Corner statistics
- `VIZ_CORNER_HEATMAP` - Heatmap data
- `VIZ_SPEED_TRACE` - Speed traces
- `VIZ_DRIVER_SUMMARY` - Summary stats
- `VIZ_TOP2_COMPARISON` - P1 vs P2 laps
- `VIZ_TOP2_CORNERS` - P1 vs P2 corners
- `VIZ_TEAMMATE_COMPARISON` - Intra-team battles
- `VIZ_TEAMMATE_PROGRESSION` - Teammate lap data
- `VIZ_TEAMMATE_CORNERS` - Teammate corners
- `SESSION_METADATA` - Event information

### Local Archive
- `telemetry_out/` - Raw CSV files (backup)

## Connecting BI Tools

### Power BI
1. Get Data → More → Snowflake
2. Server: `SRAFERN-PH02712.snowflakecomputing.com`
3. Database: `F1DATA`
4. Schema: `PUBLIC`
5. Warehouse: `COMPUTE_WH`
6. Enter your Snowflake credentials

### Tableau
1. Connect → Snowflake
2. Server: `SRAFERN-PH02712.snowflakecomputing.com`
3. Database: `F1DATA`
4. Schema: `PUBLIC`
5. Warehouse: `COMPUTE_WH`
6. Enter your Snowflake credentials

### Excel / Other Tools
1. Use Snowflake ODBC driver
2. Connection string: `SRAFERN-PH02712.snowflakecomputing.com`
3. Database: `F1DATA`

## Benefits of Snowflake

✅ **Cloud-native** - Access from anywhere  
✅ **Scalable** - Handles historical data accumulation  
✅ **Concurrent** - Multiple users/tools can query simultaneously  
✅ **No local database management** - Everything in the cloud  
✅ **Automatic optimization** - Snowflake handles performance  

## Troubleshooting

**Snowflake connection fails:**
- Verify credentials in `snowflake_config.py`
- Check warehouse is running in Snowflake UI
- Ensure your IP is whitelisted (if network policies enabled)
- Test connection in Snowflake web UI first

**Tables not appearing:**
- Verify you're looking in `F1DATA.PUBLIC` schema
- Check warehouse is active
- Query: `SHOW TABLES IN F1DATA.PUBLIC;`

## File Structure
```
.
├── fetch_pipeline.py          # Main data extraction
├── aggregate_for_viz.py       # Aggregation & Snowflake loading
├── snowflake_config.py        # Snowflake credentials
├── requirements.txt           # Dependencies
├── f1_cache/                  # FastF1 API cache
└── telemetry_out/             # Raw CSV exports (backup)
```

