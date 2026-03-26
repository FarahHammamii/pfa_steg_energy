"""
ingestion/ingest_districts.py
Ingest STEG districts data from Excel to Bronze layer
"""
import pandas as pd
from pathlib import Path
import sys
import numpy as np
import re
import time
from typing import Tuple, Optional

sys.path.append(str(Path(__file__).parent.parent))
from utils.db import execute_batch
from utils.logger import get_logger
from config.settings import RAW_DATA_DIR

logger = get_logger(__name__)

# Try to import geocoding libraries
try:
    from geopy.geocoders import Nominatim
    from geopy.extra.rate_limiter import RateLimiter
    from geopy.exc import GeocoderTimedOut, GeocoderUnavailable
    GEOCODING_AVAILABLE = True
except ImportError:
    GEOCODING_AVAILABLE = False
    logger.warning("Geopy not installed. Install with: pip install geopy")


class Geocoder:
    """Simple geocoder with rate limiting and caching"""
    
    def __init__(self, user_agent="steg_energy_lakehouse", cache_file=None):
        self.cache = {}
        self.user_agent = user_agent
        self.cache_file = cache_file
        self.geolocator = None
        self.geocode = None
        
        if GEOCODING_AVAILABLE:
            try:
                self.geolocator = Nominatim(user_agent=user_agent)
                # Add rate limiter (1 request per second to respect Nominatim's policy)
                self.geocode = RateLimiter(self.geolocator.geocode, min_delay_seconds=1)
                logger.info("Geocoder initialized with rate limiting")
            except Exception as e:
                logger.error(f"Failed to initialize geocoder: {e}")
                self.geocode = None
        
        # Load cache if exists
        if cache_file and Path(cache_file).exists():
            try:
                import pickle
                with open(cache_file, 'rb') as f:
                    self.cache = pickle.load(f)
                logger.info(f"Loaded {len(self.cache)} cached geocoding results")
            except Exception as e:
                logger.warning(f"Could not load cache: {e}")
    
    def geocode_address(self, address: str, district: str, gouvernorat: str) -> Tuple[Optional[float], Optional[float]]:
        """Geocode an address, using cache if available"""
        # Create cache key
        cache_key = f"{address}_{district}_{gouvernorat}".lower().strip()
        
        # Check cache first
        if cache_key in self.cache:
            lat, lon = self.cache[cache_key]
            logger.debug(f"Cache hit for: {address[:50]}... -> ({lat}, {lon})")
            return lat, lon
        
        # Try multiple address formats
        address_variants = [
            f"{address}, {district}, {gouvernorat}, Tunisia",
            f"{district}, {gouvernorat}, Tunisia",
            f"{gouvernorat}, Tunisia"
        ]
        
        for addr in address_variants:
            if not addr or addr.strip() == "":
                continue
                
            try:
                if self.geocode:
                    location = self.geocode(addr)
                    if location:
                        lat, lon = location.latitude, location.longitude
                        # Cache the result
                        self.cache[cache_key] = (lat, lon)
                        logger.info(f"Geocoded: {addr[:50]}... -> ({lat:.6f}, {lon:.6f})")
                        return lat, lon
                else:
                    # No geocoder available
                    return None, None
            except (GeocoderTimedOut, GeocoderUnavailable) as e:
                logger.warning(f"Geocoding timeout for {addr[:50]}: {e}")
                time.sleep(2)  # Wait before retry
                continue
            except Exception as e:
                logger.warning(f"Geocoding failed for {addr[:50]}: {e}")
                continue
        
        # If all attempts fail, cache None result to avoid retrying
        self.cache[cache_key] = (None, None)
        return None, None
    
    def save_cache(self):
        """Save cache to disk"""
        if self.cache_file:
            try:
                import pickle
                with open(self.cache_file, 'wb') as f:
                    pickle.dump(self.cache, f)
                logger.info(f"Saved {len(self.cache)} geocoding results to cache")
            except Exception as e:
                logger.warning(f"Could not save cache: {e}")


def clean_coordinate_value(value):
    """
    Attempt to fix malformed coordinate values.
    Common issues:
    - Missing decimal point: 84703 -> 8.4703
    - Extra digits: 33.4568733 -> 33.456873 (truncate to reasonable precision)
    """
    if value is None or pd.isna(value):
        return None
    
    try:
        # Convert to string to analyze
        str_val = str(value).strip()
        
        # If it's already a float/integer
        num_val = float(str_val)
        
        # Check if it's a valid coordinate range
        if -180 <= num_val <= 180:
            # Already valid, but might need precision adjustment
            return round(num_val, 6)  # Keep 6 decimal places
        
        # If it's a large number that looks like a missing decimal point
        # Example: 84703 -> should be 8.4703
        if abs(num_val) > 1000:
            # Try to interpret as a number with missing decimal
            # Count digits to determine where to place decimal
            str_without_decimal = str_val.replace('.', '')
            
            # If it's a 5-digit number like 84703, likely 8.4703 (5 digits -> 1 before decimal)
            if len(str_without_decimal) == 5:
                corrected = float(str_without_decimal) / 10000
                logger.debug(f"Fixed coordinate: {num_val} -> {corrected}")
                return round(corrected, 6)
            
            # If it's a 4-digit number like 8470, likely 8.470
            elif len(str_without_decimal) == 4:
                corrected = float(str_without_decimal) / 1000
                logger.debug(f"Fixed coordinate: {num_val} -> {corrected}")
                return round(corrected, 6)
        
        # If none of the above worked, return None
        logger.warning(f"Could not fix coordinate value: {num_val}")
        return None
        
    except (ValueError, TypeError):
        return None


def validate_and_fix_coordinates(lat, lon, geocoder=None, row_data=None):
    """Validate and attempt to fix latitude and longitude values"""
    # Valid ranges
    valid_lat_range = (-90, 90)
    valid_lon_range = (-180, 180)
    
    # Check if coordinates already exist and are valid
    if lat is not None and lon is not None:
        lat_clean = clean_coordinate_value(lat)
        lon_clean = clean_coordinate_value(lon)
        
        if lat_clean is not None and lon_clean is not None:
            if (valid_lat_range[0] <= lat_clean <= valid_lat_range[1] and
                valid_lon_range[0] <= lon_clean <= valid_lon_range[1]):
                return lat_clean, lon_clean
    
    # If coordinates are missing or invalid, try to geocode
    if geocoder and row_data:
        address = row_data.get('Adresse', '')
        district = row_data.get('District', '')
        gouvernorat = row_data.get('Gouvernorat', '')
        
        if address or district or gouvernorat:
            logger.debug(f"Attempting to geocode: {district}, {gouvernorat}")
            lat_geo, lon_geo = geocoder.geocode_address(address, district, gouvernorat)
            if lat_geo and lon_geo:
                return lat_geo, lon_geo
    
    # If all attempts fail, return None
    return None, None


def ingest_districts(enable_geocoding=False, geocoding_cache_file=None):
    """Read districts Excel and insert into bronze.districts"""
    file_path = RAW_DATA_DIR / "districts" / "tnlistedistrictsteg.xls"
    
    if not file_path.exists():
        logger.error(f"Districts file not found: {file_path}")
        return
    
    logger.info(f"Reading districts from {file_path}")
    
    # Initialize geocoder if enabled
    geocoder = None
    if enable_geocoding and GEOCODING_AVAILABLE:
        cache_path = geocoding_cache_file or RAW_DATA_DIR / "geocoding_cache.pkl"
        geocoder = Geocoder(user_agent="steg_energy_lakehouse", cache_file=str(cache_path))
        logger.info("Geocoding enabled")
    elif enable_geocoding and not GEOCODING_AVAILABLE:
        logger.warning("Geocoding requested but geopy not installed. Install with: pip install geopy")
    
    # Load the data
    df = pd.read_excel(file_path)

    # 1. CLEAN DATA: Replace all NaNs with None (SQL NULL) across the whole DF
    df = df.where(pd.notna(df), None)

    # 2. FORMAT DATA: Ensure Lat/Lon are numeric (or None)
    df['Lat'] = pd.to_numeric(df['Lat'], errors='coerce')
    df['Lon'] = pd.to_numeric(df['Lon'], errors='coerce')
    
    # 3. Log records with missing coordinates
    missing_coords = df[df['Lat'].isna() | df['Lon'].isna()]
    if len(missing_coords) > 0:
        logger.info(f"Found {len(missing_coords)} records without coordinates")
        if enable_geocoding:
            logger.info("Will attempt to geocode these addresses")
    
    # 4. Log problematic records before fixing (malformed coordinates)
    problem_mask = (df['Lat'].notna() | df['Lon'].notna()) & \
                   ((df['Lat'].abs() > 90) | (df['Lon'].abs() > 180))
    
    if problem_mask.any():
        logger.info(f"Found {problem_mask.sum()} records with potentially malformed coordinates")
        for idx in df[problem_mask].head(3).index:
            logger.info(f"  - {df.loc[idx, 'Gouvernorat']}: {df.loc[idx, 'District']} - "
                       f"Lat={df.loc[idx, 'Lat']}, Lon={df.loc[idx, 'Lon']}")
    
    # 5. VALIDATE AND FIX COORDINATES (with optional geocoding)
    validated_coords = []
    for idx, row in df.iterrows():
        row_data = {
            'Adresse': row.get('Adresse'),
            'District': row.get('District'),
            'Gouvernorat': row.get('Gouvernorat')
        }
        
        lat, lon = validate_and_fix_coordinates(
            row['Lat'], row['Lon'], 
            geocoder if enable_geocoding else None,
            row_data
        )
        validated_coords.append((lat, lon))
        
        # Rate limiting for geocoding
        if enable_geocoding and geocoder and lat is not None:
            time.sleep(0.5)  # Be respectful of API limits
    
    # Update the dataframe with validated/fixed coordinates
    df['Lat'] = [coord[0] for coord in validated_coords]
    df['Lon'] = [coord[1] for coord in validated_coords]
    
    # Replace any remaining NaN with None
    df['Lat'] = df['Lat'].replace({np.nan: None})
    df['Lon'] = df['Lon'].replace({np.nan: None})
    
    # Save geocoding cache if used
    if enable_geocoding and geocoder:
        geocoder.save_cache()
    
    # Log statistics
    total_records = len(df)
    records_with_coords = df[df['Lat'].notna() & df['Lon'].notna()].shape[0]
    records_without_coords = total_records - records_with_coords
    
    logger.info(f"Coordinate statistics: {records_with_coords}/{total_records} records have coordinates")
    if records_without_coords > 0:
        logger.info(f"  {records_without_coords} records will have NULL coordinates in the database")

    # 6. SELECT & TUPLE-IZE
    columns_to_insert = [
        'Region', 'Gouvernorat', 'Type', 'District', 'Adresse', 
        'Standard', 'Reclamation', 'Fax', 'Lat', 'Lon'
    ]
    
    records = list(df[columns_to_insert].itertuples(index=False, name=None))

    if records:
        sql = """
            INSERT INTO bronze.districts 
            (region, gouvernorat, type, district, adresse, standard, reclamation, fax, latitude, longitude)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """
        
        inserted = execute_batch(sql, records)
        logger.info(f"Inserted {inserted} records into bronze.districts")
    else:
        logger.warning("No records to insert")


if __name__ == "__main__":
    # Enable geocoding by setting to True (requires pip install geopy)
    # Also consider using a local cache file to avoid re-geocoding
    ingest_districts(
        enable_geocoding=True,  # Set to True to enable geocoding
        geocoding_cache_file=RAW_DATA_DIR / "districts" / "geocoding_cache.pkl"
    )