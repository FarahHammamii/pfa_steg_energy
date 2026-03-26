"""
etl/gold_solar_impact.py
Generate solar impact KPIs for Gold layer
"""
from pathlib import Path
import sys

sys.path.append(str(Path(__file__).parent.parent))
from utils.db import get_cursor, execute_batch
from utils.logger import get_logger

logger = get_logger(__name__)

def generate_solar_impact():
    """Generate aggregated solar impact metrics"""
    
    logger.info("Generating solar impact KPIs for Gold layer")
    
    with get_cursor() as cur:
        cur.execute("TRUNCATE TABLE gold.solar_impact")
    
    with get_cursor(dict_cursor=True) as cur:
        cur.execute("""
            SELECT 
                region,
                year,
                SUM(surface_m2) as total_surface,
                SUM(installations_count) as total_installations,
                SUM(total_investment_dt) as total_investment,
                SUM(co2_saved_tons) as co2_saved
            FROM silver.prosol
            WHERE region IS NOT NULL AND year IS NOT NULL
            GROUP BY region, year
            ORDER BY region, year
        """)
        rows = cur.fetchall()
    
    if not rows:
        logger.warning("No PROSOL data found in silver layer")
        return
    
    records = []
    for row in rows:
        # Convert Decimal to float
        total_surface = float(row['total_surface'] or 0)
        total_installations = float(row['total_installations'] or 0)
        total_investment = float(row['total_investment'] or 0)
        co2_saved = float(row['co2_saved'] or 0)
        
        # Calculate estimated subsidy (30% of investment based on typical PROSOL program)
        estimated_subsidy = total_investment * 0.3
        avg_subsidy_pct = (estimated_subsidy / total_investment * 100) if total_investment > 0 else 0
        
        records.append((
            row['region'], 
            row['year'],
            int(total_surface),
            int(total_installations),
            total_investment,
            co2_saved,
            avg_subsidy_pct
        ))
    
    sql = """
        INSERT INTO gold.solar_impact 
        (region, year, total_surface_m2, total_installations,
         total_investment_dt, co2_saved_tons, avg_subsidy_pct)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
    """
    
    inserted = execute_batch(sql, records)
    logger.info(f"Inserted {inserted} records into gold.solar_impact")

if __name__ == "__main__":
    generate_solar_impact()