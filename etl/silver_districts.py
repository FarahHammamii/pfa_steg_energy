"""
etl/silver_districts.py
Clean districts data from Bronze to Silver with climate zones
"""
from pathlib import Path
import sys

sys.path.append(str(Path(__file__).parent.parent))
from utils.db import get_cursor, execute_batch
from utils.logger import get_logger

logger = get_logger(__name__)

def get_climate_zone(latitude):
    """Determine climate zone based on latitude"""
    if latitude is None:
        return 'Unknown'
    if latitude > 36:
        return 'North_Mediterranean'
    elif latitude > 34:
        return 'Central_SemiArid'
    else:
        return 'South_Arid'

def get_grid_density(latitude):
    """Determine grid density based on latitude"""
    if latitude is None:
        return 'Unknown'
    if latitude > 36:
        return 'High'
    elif latitude > 34:
        return 'Medium'
    else:
        return 'Low'

def transform_districts_to_silver():
    """Transform districts data from Bronze to Silver"""
    
    logger.info("Transforming districts data to Silver layer")
    
    with get_cursor() as cur:
        cur.execute("TRUNCATE TABLE silver.districts")
    
    with get_cursor(dict_cursor=True) as cur:
        cur.execute("""
            SELECT DISTINCT ON (district, gouvernorat)
                region, gouvernorat, type, district, adresse,
                standard, fax, latitude, longitude
            FROM bronze.districts
            WHERE district IS NOT NULL
            ORDER BY district, gouvernorat, id DESC
        """)
        rows = cur.fetchall()
    
    if not rows:
        logger.warning("No data found in bronze.districts")
        return
    
    records = []
    for row in rows:
        lat = row['latitude']
        
        records.append((
            row['region'], row['gouvernorat'], row['type'], row['district'],
            row['adresse'], row['standard'], row['fax'], lat, row['longitude'],
            get_climate_zone(lat), get_grid_density(lat)
        ))
    
    sql = """
        INSERT INTO silver.districts 
        (region, gouvernorat, type, district, adresse, standard, fax,
         latitude, longitude, climate_zone, grid_density)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """
    
    inserted = execute_batch(sql, records)
    logger.info(f"Inserted {inserted} records into silver.districts")

if __name__ == "__main__":
    transform_districts_to_silver()