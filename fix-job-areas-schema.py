#!/usr/bin/env python3
"""
Fix job_areas table schema by adding missing created_at column
"""

import sys
import os

# Add current directory to path
sys.path.insert(0, os.getcwd())

from sqlalchemy import text
from app.database import engine

def fix_job_areas_table():
    """Add missing created_at column to job_areas table"""
    print("=" * 80)
    print("FIXING JOB_AREAS TABLE SCHEMA")
    print("=" * 80)
    print()
    
    sql_commands = [
        # Add created_at column with default value
        "ALTER TABLE job_areas ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP;",
        # Add updated_at column for consistency
        "ALTER TABLE job_areas ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP;",
    ]
    
    try:
        with engine.connect() as conn:
            for i, sql in enumerate(sql_commands, 1):
                print(f"[{i}/{len(sql_commands)}] Executing: {sql}")
                conn.execute(text(sql))
                conn.commit()
            
            print()
            print("✅ All columns added successfully!")
            print()
            
            # Verify job_areas table
            print("Verifying job_areas table columns:")
            result = conn.execute(text("""
                SELECT column_name, data_type, is_nullable, column_default
                FROM information_schema.columns 
                WHERE table_name = 'job_areas' 
                AND column_name IN ('created_at', 'updated_at')
                ORDER BY ordinal_position;
            """))
            
            rows = list(result)
            if rows:
                for row in rows:
                    print(f"  ✓ {row[0]}: {row[1]} (nullable: {row[2]}, default: {row[3]})")
            else:
                print("  ⚠️  No matching columns found - they may already exist")
            
            print()
            print("=" * 80)
            print("✅ JOB_AREAS TABLE SCHEMA FIXED!")
            print("=" * 80)
            print()
            
            return True
            
    except Exception as e:
        print(f"❌ ERROR: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    success = fix_job_areas_table()
    sys.exit(0 if success else 1)
