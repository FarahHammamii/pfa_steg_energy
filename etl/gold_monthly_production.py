"""
etl/gold_monthly_production.py
Generate monthly aggregated production KPIs
"""
from pathlib import Path
import sys

sys.path.append(str(Path(__file__).parent.parent))
from utils.db import get_cursor, execute_batch
from utils.logger import get_logger

logger = get_logger(__name__)

def generate_monthly_production():
    """Generate monthly aggregated production KPIs"""
    
    logger.info("Generating monthly production KPIs for Gold layer")
    
    with get_cursor() as cur:
        cur.execute("TRUNCATE TABLE gold.monthly_production")
    
    with get_cursor(dict_cursor=True) as cur:
        cur.execute("""
            SELECT 
                year, month, month_name,
                total_production_gwh,
                steg_share_pct,
                renewable_share_pct,
                growth_rate_pct
            FROM silver.production
            ORDER BY year, month
        """)
        rows = cur.fetchall()
    
    records = []
    for row in rows:
        records.append((
            row['year'], row['month'], row['month_name'],
            row['total_production_gwh'] or 0,
            row['steg_share_pct'] or 0,
            row['renewable_share_pct'] or 0,
            row['growth_rate_pct']
        ))
    
    sql = """
        INSERT INTO gold.monthly_production 
        (year, month, month_name, total_production_gwh,
         avg_steg_share_pct, avg_renewable_share_pct, growth_rate_pct)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
    """
    
    inserted = execute_batch(sql, records)
    logger.info(f"Inserted {inserted} records into gold.monthly_production")

if __name__ == "__main__":
    generate_monthly_production()