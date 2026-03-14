"""
Weather Data Integration - Fetches weather conditions for F1 sessions
Uses OpenWeatherMap API for historical weather data
"""
import os
import json
import requests
import pandas as pd
from datetime import datetime, timezone
from pathlib import Path
from pipeline_logger import get_logger


# Load .env file
env_file = Path(__file__).parent / '.env'
if env_file.exists():
    with open(env_file, 'r') as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                os.environ[key] = value


class WeatherDataFetcher:
    """Fetches historical weather data for F1 sessions"""
    
    # Circuit coordinates (approximate paddock locations)
    CIRCUIT_COORDS = {
        'Melbourne Grand Prix Circuit': (-37.8497, 144.9680),
        'Bahrain International Circuit': (26.0325, 50.5106),
        'Shanghai International Circuit': (31.3389, 121.2197),
        'Suzuka Circuit': (34.8431, 136.5407),
        'Miami International Autodrome': (25.9581, -80.2389),
        'Circuit de Monaco': (43.7347, 7.4206),
        'Circuit Gilles Villeneuve': (45.5048, -73.5268),
        'Red Bull Ring': (47.2197, 14.7647),
        'Silverstone Circuit': (52.0786, -1.0169),
        'Hungaroring': (47.5789, 19.2486),
        'Circuit de Spa-Francorchamps': (50.4372, 5.9714),
        'Circuit Zandvoort': (52.3889, 4.5408),
        'Autodromo Nazionale di Monza': (45.6156, 9.2811),
        'Circuit de Barcelona-Catalunya': (41.5700, 2.2611),
        'Baku City Circuit': (40.3725, 49.8533),
        'Marina Bay Street Circuit': (1.2914, 103.8639),
        'Circuit of the Americas': (30.1328, -97.6411),
        'Autódromo Hermanos Rodríguez': (19.4042, -99.0907),
        'Autódromo José Carlos Pace': (-23.7014, -46.6978),
        'Las Vegas Street Circuit': (36.1147, -115.1728),
        'Losail International Circuit': (25.4900, 51.4542),
        'Yas Marina Circuit': (24.4672, 54.6031),
        'Abu Dhabi Grand Prix': (24.4672, 54.6031),
    }
    
    def __init__(self, api_key=None):
        self.api_key = api_key or os.environ.get('OPENWEATHER_API_KEY')
        self.logger = get_logger()
        
        if not self.api_key or self.api_key == 'your_api_key_here':
            self.logger.warning("OpenWeatherMap API key not configured. Weather data will be skipped.")
            self.api_key = None
    
    def get_circuit_coordinates(self, circuit_name):
        """Get coordinates for a circuit"""
        # Try exact match first
        if circuit_name in self.CIRCUIT_COORDS:
            return self.CIRCUIT_COORDS[circuit_name]
        
        # Try partial match
        for name, coords in self.CIRCUIT_COORDS.items():
            if circuit_name.lower() in name.lower() or name.lower() in circuit_name.lower():
                return coords
        
        return None
    
    def fetch_historical_weather(self, lat, lon, timestamp):
        """
        Fetch historical weather data from OpenWeatherMap
        timestamp: Unix timestamp (seconds since epoch)
        """
        if not self.api_key:
            return None
        
        try:
            # OpenWeatherMap Time Machine API (requires paid subscription)
            # For free tier, we'll use current weather as fallback
            url = f"https://api.openweathermap.org/data/3.0/onecall/timemachine"
            params = {
                'lat': lat,
                'lon': lon,
                'dt': timestamp,
                'appid': self.api_key,
                'units': 'metric'
            }
            
            response = requests.get(url, params=params, timeout=10)
            
            if response.status_code == 200:
                return response.json()
            elif response.status_code == 401:
                self.logger.warning("Weather API: Invalid API key or subscription level")
                return None
            else:
                self.logger.warning(f"Weather API error: {response.status_code}")
                return None
                
        except Exception as e:
            self.logger.warning(f"Failed to fetch weather data: {e}")
            return None
    
    def fetch_current_weather(self, lat, lon):
        """Fetch current weather (free tier fallback)"""
        if not self.api_key:
            return None
        
        try:
            url = "https://api.openweathermap.org/data/2.5/weather"
            params = {
                'lat': lat,
                'lon': lon,
                'appid': self.api_key,
                'units': 'metric'
            }
            
            response = requests.get(url, params=params, timeout=10)
            
            if response.status_code == 200:
                return response.json()
            else:
                return None
                
        except Exception as e:
            self.logger.warning(f"Failed to fetch current weather: {e}")
            return None
    
    def parse_weather_data(self, weather_data, data_type='current'):
        """Extract relevant weather information"""
        if not weather_data:
            return None
        
        try:
            if data_type == 'timemachine' and 'data' in weather_data and weather_data['data']:
                data = weather_data['data'][0]
                return {
                    'temperature': data.get('temp'),
                    'feels_like': data.get('feels_like'),
                    'pressure': data.get('pressure'),
                    'humidity': data.get('humidity'),
                    'wind_speed': data.get('wind_speed'),
                    'wind_deg': data.get('wind_deg'),
                    'clouds': data.get('clouds'),
                    'rain_1h': data.get('rain', {}).get('1h', 0),
                    'weather_main': data.get('weather', [{}])[0].get('main'),
                    'weather_description': data.get('weather', [{}])[0].get('description'),
                }
            elif data_type == 'current':
                return {
                    'temperature': weather_data['main']['temp'],
                    'feels_like': weather_data['main']['feels_like'],
                    'pressure': weather_data['main']['pressure'],
                    'humidity': weather_data['main']['humidity'],
                    'wind_speed': weather_data['wind']['speed'],
                    'wind_deg': weather_data['wind']['deg'],
                    'clouds': weather_data['clouds']['all'],
                    'rain_1h': weather_data.get('rain', {}).get('1h', 0),
                    'weather_main': weather_data['weather'][0]['main'],
                    'weather_description': weather_data['weather'][0]['description'],
                }
        except Exception as e:
            self.logger.error(f"Error parsing weather data: {e}")
            return None
    
    def get_session_weather(self, circuit_name, session_date=None):
        """
        Get weather data for a session
        circuit_name: Name of the circuit
        session_date: datetime object of session (if None, uses current weather)
        """
        coords = self.get_circuit_coordinates(circuit_name)
        if not coords:
            return None
        
        lat, lon = coords
        
        if session_date and self.api_key:
            # Try historical data (requires paid API)
            timestamp = int(session_date.timestamp())
            weather_data = self.fetch_historical_weather(lat, lon, timestamp)
            if weather_data:
                return self.parse_weather_data(weather_data, 'timemachine')
        
        # Fallback to current weather (free tier)
        weather_data = self.fetch_current_weather(lat, lon)
        return self.parse_weather_data(weather_data, 'current')


def add_weather_to_session(session, output_dir='telemetry_out'):
    """Add weather data to session metadata"""
    logger = get_logger()
    
    try:
        # Load session metadata
        meta_file = Path(output_dir) / 'session_meta.json'
        if not meta_file.exists():
            logger.warning("Session metadata not found, skipping weather data")
            return
        
        with open(meta_file, 'r') as f:
            session_meta = json.load(f)
        
        # Get weather data
        fetcher = WeatherDataFetcher()
        
        circuit_name = session_meta.get('circuit_short_name') or session_meta.get('event_name', '')
        
        # Try to parse session date
        session_date = None
        if 'session_date' in session_meta:
            try:
                session_date = datetime.fromisoformat(session_meta['session_date'])
            except:
                pass
        
        logger.info(f"Fetching weather data for {circuit_name}...")
        weather = fetcher.get_session_weather(circuit_name, session_date)
        
        if weather:
            session_meta['weather'] = weather
            logger.info(f"[OK] Weather: {weather['temperature']}°C, {weather['weather_description']}")
            logger.info(f"  Humidity: {weather['humidity']}%, Wind: {weather['wind_speed']} m/s")
            
            # Save updated metadata
            with open(meta_file, 'w') as f:
                json.dump(session_meta, f, indent=2)
            
            # Also save as separate weather file
            weather_file = Path(output_dir) / 'weather_data.json'
            with open(weather_file, 'w') as f:
                json.dump(weather, f, indent=2)
            
            return weather
        else:
            logger.info("Weather data not available (API key not configured or free tier limit)")
            return None
            
    except Exception as e:
        logger.error(f"Failed to add weather data: {e}")
        return None


if __name__ == '__main__':
    # Test weather fetcher
    fetcher = WeatherDataFetcher()
    weather = fetcher.get_session_weather('Silverstone Circuit')
    if weather:
        print(json.dumps(weather, indent=2))
    else:
        print("Weather data not available. Set OPENWEATHER_API_KEY in .env file.")
