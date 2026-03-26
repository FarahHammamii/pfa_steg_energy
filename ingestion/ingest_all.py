"""
ingestion/ingest_all.py
Orchestrate all ingestion scripts
"""
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))
from utils.logger import get_logger

logger = get_logger(__name__)

def run_ingestion():
    """Run all ingestion scripts"""
    
    logger.info("=" * 60)
    logger.info("Starting STEG Data Lakehouse Ingestion")
    logger.info("=" * 60)
    
    scripts = [
        ("Production data", "ingestion.ingest_production"),
        ("PROSOL data", "ingestion.ingest_prosol"),
        ("Districts data", "ingestion.ingest_districts"),
        ("Blackout PDF", "ingestion.ingest_blackout_pdf"),
    ]
    
    for name, module_name in scripts:
        logger.info(f"\n📥 Ingestion: {name}")
        logger.info("-" * 40)
        
        try:
            module = __import__(module_name, fromlist=[''])
            if hasattr(module, 'ingest_production'):
                module.ingest_production()
            elif hasattr(module, 'ingest_prosol'):
                module.ingest_prosol()
            elif hasattr(module, 'ingest_districts'):
                module.ingest_districts()
            elif hasattr(module, 'ingest_blackout_incident'):
                module.ingest_blackout_incident()
        except Exception as e:
            logger.error(f"Error in {name}: {e}")
    
    logger.info("\n" + "=" * 60)
    logger.info("Ingestion complete!")
    logger.info("=" * 60)

if __name__ == "__main__":
    run_ingestion()