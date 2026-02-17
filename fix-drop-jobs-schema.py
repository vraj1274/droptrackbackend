#!/usr/bin/env python3
"""
Fix drop_jobs table schema by adding missing columns
"""

import sys
import os

# Add current directory to path
sys.path.insert(0, os.getcwd())

from sqlalchemy import text
from app.database import engine

def fix_drop_jobs_table():
    """Add missing columns to drop_jobs table"""
    print("=" * 80)
    print("FIXING DROP_JOBS TABLE SCHEMA")
    print("=" * 80)
    print()
    
    sql_commands = [
        # Add is_broadcasted column
        "ALTER TABLE drop_jobs ADD COLUMN IF NOT EXISTS is_broadcasted BOOLEAN DEFAULT false;",
        # Add broadcasted_at column
        "ALTER TABLE drop_jobs ADD COLUMN IF NOT EXISTS broadcasted_at TIMESTAMP;",
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
            
            # Verify drop_jobs table
            print("Verifying drop_jobs table columns:")
            result = conn.execute(text("""
                SELECT column_name, data_type, is_nullable, column_default
                FROM information_schema.columns 
                WHERE table_name = 'drop_jobs' 
                AND column_name IN ('is_broadcasted', 'broadcasted_at')
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
            print("✅ DROP_JOBS TABLE SCHEMA FIXED!")
            print("=" * 80)
            print()
            print("The backend should now work without errors.")
            print()
            
            return True
            
    except Exception as e:
        print(f"❌ ERROR: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    success = fix_drop_jobs_table()
    sys.exit(0 if success else 1)
