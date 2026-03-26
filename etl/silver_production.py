"""
etl/silver_production.py
Clean production data from Bronze to Silver
"""
from pathlib import Path
import sys
from datetime import date

sys.path.append(str(Path(__file__).parent.parent))
from utils.db import get_cursor, execute_batch
from utils.logger import get_logger

logger = get_logger(__name__)

def get_season(month):
    """Return season based on month"""
    if month in [12, 1, 2]:
        return 'Winter'
    elif month in [3, 4, 5]:
        return 'Spring'
    elif month in [6, 7, 8]:
        return 'Summer'
    else:
        return 'Autumn'

def transform_production_to_silver():
    """Transform production data from Bronze to Silver"""
    
    logger.info("Transforming production data to Silver layer")
    
    # Clear existing Silver data
    with get_cursor() as cur:
        cur.execute("TRUNCATE TABLE silver.production")
    
    # Read from Bronze and transform
    with get_cursor(dict_cursor=True) as cur:
        cur.execute("""
            SELECT month, year, steg_production_gwh, ipp_production_gwh,
                   ipp_solar_gwh, autoproducer_gwh, total_production_gwh
            FROM bronze.production
            ORDER BY year, month
        """)
        rows = cur.fetchall()
    
    if not rows:
        logger.warning("No data found in bronze.production")
        return
    
    records = []
    for i, row in enumerate(rows):
        month = row['month']
        year = row['year']
        
        # Create date_key
        try:
            date_key = date(year, month, 1)
        except ValueError:
            continue
        
        # Calculate shares
        total = row['total_production_gwh'] or 1
        steg_share = (row['steg_production_gwh'] or 0) / total * 100
        renewable_share = (row['ipp_solar_gwh'] or 0) / total * 100
        
        # Calculate growth rate (year-over-year)
        growth_rate = None
        if i >= 12:
            prev_total = rows[i-12]['total_production_gwh']
            if prev_total and prev_total > 0:
                growth_rate = ((total - prev_total) / prev_total) * 100
        
        records.append((
            date_key, year, month, (month - 1) // 3 + 1,
            date_key.strftime('%B'), get_season(month),
            row['steg_production_gwh'], row['ipp_production_gwh'],
            row['ipp_solar_gwh'], row['autoproducer_gwh'],
            total, steg_share, renewable_share, growth_rate
        ))
    
    sql = """
        INSERT INTO silver.production 
        (date_key, year, month, quarter, month_name, season,
         steg_production_gwh, ipp_production_gwh, ipp_solar_gwh,
         autoproducer_gwh, total_production_gwh, steg_share_pct,
         renewable_share_pct, growth_rate_pct)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """
    
    inserted = execute_batch(sql, records)
    logger.info(f"Inserted {inserted} records into silver.production")

if __name__ == "__main__":
    transform_production_to_silver()