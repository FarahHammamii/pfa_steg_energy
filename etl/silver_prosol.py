"""
etl/silver_prosol.py
Clean PROSOL data from Bronze to Silver
"""
from pathlib import Path
import sys
from decimal import Decimal

sys.path.append(str(Path(__file__).parent.parent))
from utils.db import get_cursor, execute_batch
from utils.logger import get_logger

logger = get_logger(__name__)

def transform_prosol_to_silver():
    """Transform PROSOL data from Bronze to Silver with derived metrics"""
    
    logger.info("Transforming PROSOL data to Silver layer")
    
    with get_cursor() as cur:
        cur.execute("TRUNCATE TABLE silver.prosol")
    
    with get_cursor(dict_cursor=True) as cur:
        cur.execute("""
            SELECT region, year, surface_m2, installations_count,
                   subsidy_dt, self_financing_dt, total_investment_dt,
                   avg_price_dt
            FROM bronze.prosol
            ORDER BY region, year
        """)
        rows = cur.fetchall()
    
    if not rows:
        logger.warning("No data found in bronze.prosol")
        return
    
    records = []
    region_cumulative = {}
    
    for row in rows:
        region = row['region']
        year = row['year']
        
        if not year or year < 2000:
            continue
        
        # Convert Decimal to float for calculations
        surface = float(row['surface_m2'] or 0)
        installations = float(row['installations_count'] or 0)
        total_investment = float(row['total_investment_dt'] or 0)
        subsidy = float(row['subsidy_dt'] or 0)
        self_financing = float(row['self_financing_dt'] or 0)
        avg_price = float(row['avg_price_dt'] or 0)
        
        # Calculate derived metrics
        avg_price_per_m2 = total_investment / surface if surface > 0 else 0
        subsidy_percentage = (subsidy / total_investment * 100) if total_investment > 0 else 0
        
        # CO2 savings: 0.5 kg CO2 per m2 per year (typical solar panel saving)
        # Convert to tons: divide by 1000
        co2_saved_tons = (surface * 0.0005)  # 0.5 kg = 0.0005 tons
        
        # Cumulative metrics per region
        cum_key = region
        if cum_key not in region_cumulative:
            region_cumulative[cum_key] = {'surface': 0, 'installations': 0}
        
        region_cumulative[cum_key]['surface'] += surface
        region_cumulative[cum_key]['installations'] += installations
        
        records.append((
            region, 
            year, 
            int(surface), 
            int(installations),
            subsidy, 
            self_financing, 
            total_investment,
            avg_price_per_m2, 
            subsidy_percentage, 
            co2_saved_tons,
            int(region_cumulative[cum_key]['installations']),
            int(region_cumulative[cum_key]['surface'])
        ))
    
    sql = """
        INSERT INTO silver.prosol 
        (region, year, surface_m2, installations_count, subsidy_dt,
         self_financing_dt, total_investment_dt, avg_price_per_m2,
         subsidy_percentage, co2_saved_tons, cumulative_installations,
         cumulative_surface)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """
    
    inserted = execute_batch(sql, records)
    logger.info(f"Inserted {inserted} records into silver.prosol")

if __name__ == "__main__":
    transform_prosol_to_silver()