"""
etl/gold_regional.py
Generate regional consumption aggregates
"""
from pathlib import Path
import sys
from decimal import Decimal

sys.path.append(str(Path(__file__).parent.parent))
from utils.db import get_cursor, execute_batch
from utils.logger import get_logger

logger = get_logger(__name__)

def generate_regional_aggregates():
    """Generate regional consumption aggregates"""
    
    logger.info("Generating regional consumption aggregates")
    
    with get_cursor() as cur:
        cur.execute("TRUNCATE TABLE gold.regional_consumption")
    
    # Get district counts per region/gouvernorat
    with get_cursor(dict_cursor=True) as cur:
        cur.execute("""
            SELECT 
                d.region,
                d.gouvernorat,
                COUNT(*) as district_count
            FROM silver.districts d
            WHERE d.region IS NOT NULL
            GROUP BY d.region, d.gouvernorat
        """)
        region_counts = cur.fetchall()
        
        # Get yearly production totals from gold.monthly_production
        cur.execute("""
            SELECT year, SUM(total_production_gwh) as annual_production
            FROM gold.monthly_production
            GROUP BY year
            ORDER BY year
        """)
        production = cur.fetchall()
    
    if not region_counts:
        logger.warning("No district data found")
        return
    
    if not production:
        logger.warning("No production data found")
        return
    
    total_districts = sum(float(r['district_count']) for r in region_counts)
    
    records = []
    for prod in production:
        year = prod['year']
        total_production = float(prod['annual_production'] or 0)
        
        for region in region_counts:
            # Convert Decimal to float
            district_count = float(region['district_count'])
            share = district_count / total_districts if total_districts > 0 else 0
            estimated_consumption = total_production * share
            
            records.append((
                region['region'],
                region['gouvernorat'],
                year,
                estimated_consumption,
                share * 100  # Convert to percentage
            ))
    
    if records:
        sql = """
            INSERT INTO gold.regional_consumption 
            (region, gouvernorat, year, estimated_consumption_gwh, share_of_national_pct)
            VALUES (%s, %s, %s, %s, %s)
        """
        
        inserted = execute_batch(sql, records)
        logger.info(f"Inserted {inserted} records into gold.regional_consumption")
    else:
        logger.warning("No records to insert")

if __name__ == "__main__":
    generate_regional_aggregates()