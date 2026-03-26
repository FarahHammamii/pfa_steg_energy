"""
etl/run_etl.py
Orchestrate all ETL transformations
"""
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))
from utils.logger import get_logger

logger = get_logger(__name__)

def run_etl():
    """Run all ETL transformations"""
    
    logger.info("=" * 60)
    logger.info("Starting STEG Data Lakehouse ETL")
    logger.info("=" * 60)
    
    scripts = [
        ("Production Silver", "etl.silver_production", "transform_production_to_silver"),
        ("PROSOL Silver", "etl.silver_prosol", "transform_prosol_to_silver"),
        ("Districts Silver", "etl.silver_districts", "transform_districts_to_silver"),
        ("Monthly Production Gold", "etl.gold_monthly_production", "generate_monthly_production"),
        ("Solar Impact Gold", "etl.gold_solar_impact", "generate_solar_impact"),
        ("Grid Stability Gold", "etl.gold_grid_stability", "generate_grid_stability"),
        ("Regional Aggregates", "etl.gold_regional", "generate_regional_aggregates"),
        ("Benchmarking Gold", "etl.gold_benchmarking", "transform_benchmarks_to_gold"),  # Added
    ]
    
    for name, module_name, function_name in scripts:
        logger.info(f"\n🔄 ETL: {name}")
        logger.info("-" * 40)
        
        try:
            module = __import__(module_name, fromlist=[''])
            
            if hasattr(module, function_name):
                getattr(module, function_name)()
                logger.info(f"✅ {name} completed successfully")
            else:
                logger.error(f"Function {function_name} not found in {module_name}")
                
        except Exception as e:
            logger.error(f"❌ Error in {name}: {e}")
            import traceback
            logger.error(traceback.format_exc())
    
    logger.info("\n" + "=" * 60)
    logger.info("ETL complete!")
    logger.info("=" * 60)

if __name__ == "__main__":
    run_etl()