"""
sql/init_schema.py
Initialize database schema on Neon
"""
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))
from utils.db import get_cursor, get_conn
from utils.logger import get_logger

logger = get_logger(__name__)

def init_schema():
    """Execute schema.sql to create all tables"""
    
    schema_path = Path(__file__).parent / "schema.sql"
    
    if not schema_path.exists():
        logger.error(f"Schema file not found: {schema_path}")
        return
    
    logger.info(f"Reading schema from {schema_path}")
    
    with open(schema_path, 'r') as f:
        schema_sql = f.read()
    
    # Execute schema
    with get_conn() as conn:
        with conn.cursor() as cur:
            try:
                cur.execute(schema_sql)
                conn.commit()
                logger.info("Schema created successfully!")
            except Exception as e:
                conn.rollback()
                logger.error(f"Error creating schema: {e}")
                raise

if __name__ == "__main__":
    init_schema()