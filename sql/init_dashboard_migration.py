#!/usr/bin/env python3
"""
Dashboard Migration Runner for STEG Energy Lakehouse
Run this script to initialize the dashboard tables
"""

import os
import sys
import logging
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from utils.db import get_conn, get_cursor
from utils.logger import get_logger

logger = get_logger(__name__)

def run_dashboard_migration():
    """Run the dashboard migration SQL"""
    migration_file = Path(__file__).parent / "migrations.sql"
    
    if not migration_file.exists():
        logger.error(f"Migration file not found: {migration_file}")
        return False
    
    logger.info(f"Reading migration from {migration_file}")
    
    with open(migration_file, 'r', encoding='utf-8') as f:
        migration_sql = f.read()
    
    logger.info("Running dashboard migration...")
    
    try:
        # Use get_conn as a context manager
        with get_conn() as conn:
            with conn.cursor() as cur:
                # Execute the entire SQL file
                cur.execute(migration_sql)
                conn.commit()
                
        logger.info("Migration completed successfully!")
        return True
        
    except Exception as e:
        logger.error(f"Migration failed: {e}")
        import traceback
        traceback.print_exc()
        return False

def verify_migration():
    """Verify that the migration was successful"""
    try:
        with get_cursor(dict_cursor=True) as cur:
            # Check if key tables exist
            tables = ['workers', 'cut_plans', 'cut_executions']
            all_exist = True
            
            print("\n" + "=" * 60)
            print("Verification Results:")
            print("=" * 60)
            
            for table in tables:
                cur.execute("""
                    SELECT EXISTS (
                        SELECT 1 FROM information_schema.tables 
                        WHERE table_schema = 'public' AND table_name = %s
                    )
                """, (table,))
                exists = cur.fetchone()['exists']
                status = "✓" if exists else "✗"
                print(f"  Table public.{table}: {status}")
                if not exists:
                    all_exist = False
            
            # Check workers count
            cur.execute("SELECT COUNT(*) FROM public.workers")
            worker_count = cur.fetchone()['count']
            print(f"\n  Workers in system: {worker_count}")
            
            # Check views
            views = ['v_worker_districts', 'v_active_cut_plans', 'v_cut_plan_summary']
            for view in views:
                cur.execute("""
                    SELECT EXISTS (
                        SELECT 1 FROM information_schema.views 
                        WHERE table_schema = 'public' AND table_name = %s
                    )
                """, (view,))
                exists = cur.fetchone()['exists']
                status = "✓" if exists else "✗"
                print(f"  View public.{view}: {status}")
            
            print("=" * 60)
            
            return all_exist
            
    except Exception as e:
        logger.error(f"Verification failed: {e}")
        return False

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    
    print("=" * 60)
    print("STEG Dashboard Migration Tool")
    print("=" * 60)
    
    if run_dashboard_migration():
        print("\n✓ Migration completed successfully!")
        
        if verify_migration():
            print("\n✓ Verification passed!")
            print("\nYou can now run the FastAPI application:")
            print("  uvicorn api.app_fastapi:app --reload")
            
            print("\nDefault login credentials (demo mode):")
            print("  Admin:     worker_id='admin'")
            print("  Supervisor: worker_id='sup_tunis'")
            print("  Worker:     worker_id='worker_tunis_1'")
            print("\nUse demo_mode=true in login request")
        else:
            print("\n⚠ Verification found issues. Please check the logs above.")
            sys.exit(1)
    else:
        print("\n✗ Migration failed!")
        sys.exit(1)