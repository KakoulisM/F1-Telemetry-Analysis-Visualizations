# Weather Data Integration Guide

The F1 telemetry pipeline now includes weather data integration using the OpenWeatherMap API.

## 🌤️ Features

- Fetches weather conditions for each F1 session
- Stores temperature, humidity, wind, rain, and cloud coverage
- Creates `viz_weather_conditions` table in Snowflake for each session
- Correlates weather with lap times and tire performance

## 📋 Setup

### 1. Get OpenWeatherMap API Key

Visit [OpenWeatherMap API](https://openweathermap.org/api) and sign up:

- **Free Tier**: Current weather data only (good for testing)
- **One Call by Call** ($40/month): Historical weather data (recommended for accurate session conditions)

### 2. Add API Key to .env

Open `.env` file and add your API key:

```bash
OPENWEATHER_API_KEY=your_actual_api_key_here
```

### 3. Run Pipeline

Weather data will be automatically fetched when you run the pipeline:

```bash
python fetch_pipeline.py
```

## 📊 Data Available

The `viz_weather_conditions` table includes:

| Column | Description |
|--------|-------------|
| EventName | Grand Prix name |
| SessionName | Practice, Qualifying, or Race |
| Year | Season year |
| Temperature | Air temperature (°C) |
| FeelsLike | Feels like temperature (°C) |
| Pressure | Atmospheric pressure (hPa) |
| Humidity | Humidity percentage (%) |
| WindSpeed | Wind speed (m/s) |
| WindDirection | Wind direction (degrees) |
| Clouds | Cloud coverage (%) |
| Rain1h | Rain volume last hour (mm) |
| WeatherMain | Main weather condition (Clear, Clouds, Rain) |
| WeatherDescription | Detailed description |

## 🔧 Circuit Coordinates

Weather is fetched for these circuits:

- ✅ All 2024/2025 F1 circuits pre-configured
- Melbourne, Bahrain, Shanghai, Suzuka
- Miami, Monaco, Montreal, Red Bull Ring
- Silverstone, Hungaroring, Spa, Zandvoort
- Monza, Barcelona, Baku, Singapore
- COTA, Mexico City, Interlagos
- Las Vegas, Losail, Abu Dhabi

## 🚀 Usage in Snowflake

Query weather impact on lap times:

```sql
SELECT 
    l.Driver,
    l.LapTimeSeconds,
    w.Temperature,
    w.Humidity,
    w.WindSpeed,
    w.WeatherMain
FROM viz_lap_progression l
CROSS JOIN viz_weather_conditions w
WHERE l.LapTimeSeconds < 100
ORDER BY l.LapTimeSeconds;
```

Analyze tire degradation by weather:

```sql
SELECT 
    t.Compound,
    AVG(t.LapTimeSeconds) as AvgLapTime,
    w.Temperature,
    w.WeatherMain
FROM viz_tire_degradation t
CROSS JOIN viz_weather_conditions w
GROUP BY t.Compound, w.Temperature, w.WeatherMain
ORDER BY AvgLapTime;
```

## ⚠️ Notes

- **Free tier**: Fetches current weather (good for recent sessions)
- **Paid tier**: Fetches historical weather for exact session time
- If API key is missing, pipeline continues without weather data
- Weather data is optional - pipeline works fine without it

## 🔍 Troubleshooting

### "Weather data not available"
- Check `.env` file has `OPENWEATHER_API_KEY`
- Verify API key is valid at OpenWeatherMap
- Ensure you have API credits remaining

### "Coordinates not found for circuit"
Update `weather_integration.py` with new circuit coordinates:

```python
CIRCUIT_COORDS = {
    'New Circuit Name': (latitude, longitude),
    # Add new circuits here
}
```

## 📈 Power BI Integration

Connect Power BI to Snowflake and use `viz_weather_conditions`:

1. New Source → Snowflake
2. Select your session schema
3. Load `viz_weather_conditions` table
4. Merge with other viz tables on EventName/SessionName/Year
5. Create visuals correlating weather with performance

## 🐳 Docker Container

Weather integration works automatically in Docker:

```bash
docker-compose up -d
```

Make sure `.env` file includes `OPENWEATHER_API_KEY` before building.
