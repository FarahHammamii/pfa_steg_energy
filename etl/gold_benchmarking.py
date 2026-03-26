"""
etl/gold_benchmarking.py
ETL transformations for benchmarking data (bronze to gold)
"""
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))
from utils.db import get_cursor, execute_batch
from utils.logger import get_logger
from config.settings import BENCHMARK_COUNTRIES

logger = get_logger(__name__)


def transform_benchmarks_to_gold():
    """Transform benchmark data from bronze to gold layer"""
    
    logger.info("📊 Transforming benchmark data to gold layer...")
    
    # Get STEG metrics
    steg = collect_steg_metrics()
    
    # Get benchmark data from bronze
    with get_cursor(dict_cursor=True) as cur:
        cur.execute("""
            SELECT country_code, country_name, indicator_name, year, value
            FROM bronze.benchmarks
            WHERE year >= 2020
            ORDER BY year DESC, country_name
        """)
        benchmarks = cur.fetchall()
    
    # Calculate Mediterranean averages
    med_averages = calculate_mediterranean_averages(benchmarks)
    
    # Generate comparison records for gold
    records = generate_comparison_records(benchmarks, steg, med_averages)
    
    if records:
        # Insert into gold layer
        inserted = insert_comparison_records(records, steg['year'])
        logger.info(f"Inserted {inserted} records into gold.international_comparison")
        return inserted
    
    return 0


def collect_steg_metrics():
    """Extract STEG's current metrics from Gold layer"""
    
    logger.info("📈 Extracting STEG metrics from gold tables...")
    
    with get_cursor(dict_cursor=True) as cur:
        # Get latest production metrics
        cur.execute("""
            SELECT year, total_production_gwh, avg_renewable_share_pct
            FROM gold.monthly_production
            WHERE year >= 2020
            ORDER BY year DESC
            LIMIT 1
        """)
        production = cur.fetchone()
        
        # Get solar impact
        cur.execute("""
            SELECT year, SUM(total_surface_m2) as total_solar,
                   SUM(co2_saved_tons) as co2_saved
            FROM gold.solar_impact
            WHERE year >= 2020
            GROUP BY year
            ORDER BY year DESC
            LIMIT 1
        """)
        solar = cur.fetchone()
        
        # Get grid stability
        cur.execute("""
            SELECT year, incident_count, total_duration_minutes
            FROM gold.grid_stability
            ORDER BY year DESC
            LIMIT 1
        """)
        stability = cur.fetchone()
    
    # Calculate per capita (Tunisia population ~12M)
    population = 12000000
    consumption_per_capita = (production['total_production_gwh'] * 1000000) / population if production else 0
    
    return {
        'year': production['year'] if production else 2023,
        'consumption_per_capita_kwh': consumption_per_capita,
        'renewable_share_pct': production['avg_renewable_share_pct'] if production else 0,
        'solar_adoption_pct': (solar['total_solar'] / 10000) if solar else 0,
        'grid_losses_pct': 12.5,  # Approx from STEG reports
        'co2_saved_tons': solar['co2_saved'] if solar else 0,
        'incident_count': stability['incident_count'] if stability else 0,
    }


def calculate_mediterranean_averages(benchmarks):
    """Calculate average values for Mediterranean countries"""
    
    mediterranean_countries = ['MAR', 'DZA', 'EGY', 'ESP', 'ITA', 'TUR']
    med_benchmarks = {}
    
    for b in benchmarks:
        if b['country_code'] in mediterranean_countries:
            key = b['indicator_name']
            if key not in med_benchmarks:
                med_benchmarks[key] = []
            med_benchmarks[key].append(b['value'])
    
    return {
        name: sum(values)/len(values) for name, values in med_benchmarks.items() if values
    }


def generate_comparison_records(benchmarks, steg, med_averages):
    """Generate comparison records for gold layer"""
    
    records = []
    
    for country_code, country_name in BENCHMARK_COUNTRIES.items():
        # Get latest metrics for this country
        country_data = [b for b in benchmarks if b['country_code'] == country_code]
        
        consumption = next((b['value'] for b in country_data if b['indicator_name'] == 'consumption_per_capita_kwh'), None)
        renewable = next((b['value'] for b in country_data if b['indicator_name'] == 'renewable_share_pct'), None)
        solar = next((b['value'] for b in country_data if b['indicator_name'] == 'solar_adoption_pct'), None)
        losses = next((b['value'] for b in country_data if b['indicator_name'] == 'grid_losses_pct'), None)
        
        # Calculate rank and gap
        rank = 1
        gap = None
        if consumption and med_averages.get('consumption_per_capita_kwh'):
            gap = ((consumption - steg['consumption_per_capita_kwh']) / steg['consumption_per_capita_kwh'] * 100) if steg['consumption_per_capita_kwh'] > 0 else 0
        
        records.append((
            country_code, country_name,
            consumption, renewable, solar, losses,
            None,  # saidi_hours - would need separate data source
            rank,
            gap
        ))
    
    return records


def insert_comparison_records(records, year):
    """Insert comparison records into gold layer"""
    
    sql = """
        INSERT INTO gold.international_comparison 
        (year, country_code, country_name, consumption_per_capita_kwh,
         renewable_share_pct, solar_adoption_pct, grid_losses_pct,
         saidi_hours, rank_position, gap_to_mediterranean_avg_pct)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT DO NOTHING
    """
    
    # Add year to each record
    full_records = [(year,) + record for record in records]
    
    return execute_batch(sql, full_records)


if __name__ == "__main__":
    transform_benchmarks_to_gold()