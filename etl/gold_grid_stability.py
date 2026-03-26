"""
etl/gold_grid_stability.py
Generate grid stability KPIs for Gold layer
"""
from pathlib import Path
import sys

sys.path.append(str(Path(__file__).parent.parent))
from utils.db import get_cursor, execute_batch
from utils.logger import get_logger

logger = get_logger(__name__)

def generate_grid_stability():
    """Generate grid stability KPIs from incident data"""
    
    logger.info("Generating grid stability KPIs for Gold layer")
    
    with get_cursor() as cur:
        cur.execute("TRUNCATE TABLE gold.grid_stability")
    
    with get_cursor(dict_cursor=True) as cur:
        cur.execute("""
            SELECT 
                incident_date,
                duration_minutes,
                power_loss_mw,
                recovery_time_minutes
            FROM bronze.incidents
            WHERE incident_date IS NOT NULL
        """)
        rows = cur.fetchall()
    
    if not rows:
        logger.warning("No incident data found")
        return
    
    # Group by year and quarter
    quarterly = {}
    for row in rows:
        if not row['incident_date']:
            continue
        
        # Convert to proper date if it's string
        incident_date = row['incident_date']
        if hasattr(incident_date, 'year'):
            year = incident_date.year
            month = incident_date.month
        else:
            # If it's a string or other type, try to parse
            from datetime import datetime
            try:
                if isinstance(incident_date, str):
                    incident_date = datetime.strptime(incident_date, '%Y-%m-%d')
                    year = incident_date.year
                    month = incident_date.month
                else:
                    continue
            except:
                continue
        
        quarter = (month - 1) // 3 + 1
        key = (year, quarter)
        
        if key not in quarterly:
            quarterly[key] = {
                'count': 0, 
                'duration': 0, 
                'power_loss': 0, 
                'recovery': 0
            }
        
        # Convert Decimal to float
        duration = float(row['duration_minutes'] or 0)
        power_loss = float(row['power_loss_mw'] or 0)
        recovery = float(row['recovery_time_minutes'] or 0)
        
        quarterly[key]['count'] += 1
        quarterly[key]['duration'] += duration
        quarterly[key]['power_loss'] += power_loss
        quarterly[key]['recovery'] += recovery
    
    records = []
    for (year, quarter), data in quarterly.items():
        avg_severity = (data['duration'] / data['count']) / 60 if data['count'] > 0 else 0
        avg_recovery = data['recovery'] / data['count'] if data['count'] > 0 else 0
        
        records.append((
            year, quarter,
            data['count'],
            data['duration'],
            avg_severity,
            data['power_loss'],
            avg_recovery
        ))
    
    sql = """
        INSERT INTO gold.grid_stability 
        (year, quarter, incident_count, total_duration_minutes,
         avg_severity_score, power_loss_mw, recovery_time_avg_minutes)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
    """
    
    inserted = execute_batch(sql, records)
    logger.info(f"Inserted {inserted} records into gold.grid_stability")

if __name__ == "__main__":
    generate_grid_stability()