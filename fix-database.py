#!/usr/bin/env python3
"""
Fix database schema by adding missing columns
"""

import sys
import os

# Add current directory to path (should be run from backend directory)
sys.path.insert(0, os.getcwd())

from sqlalchemy import text
from app.database import engine

def fix_database():
    """Add missing columns to database"""
    print("=" * 80)
    print("FIXING DATABASE SCHEMA")
    print("=" * 80)
    print()
    
    sql_commands = [
        # Clients table
        "ALTER TABLE clients ADD COLUMN IF NOT EXISTS website VARCHAR(255);",
        "ALTER TABLE clients ADD COLUMN IF NOT EXISTS description TEXT;",
        "ALTER TABLE clients ADD COLUMN IF NOT EXISTS street VARCHAR(255);",
        "ALTER TABLE clients ADD COLUMN IF NOT EXISTS city VARCHAR(100);",
        "ALTER TABLE clients ADD COLUMN IF NOT EXISTS state VARCHAR(100);",
        "ALTER TABLE clients ADD COLUMN IF NOT EXISTS zip_code VARCHAR(20);",
        "ALTER TABLE clients ADD COLUMN IF NOT EXISTS email_notifications BOOLEAN DEFAULT true;",
        "ALTER TABLE clients ADD COLUMN IF NOT EXISTS sms_notifications BOOLEAN DEFAULT true;",
        "ALTER TABLE clients ADD COLUMN IF NOT EXISTS timezone VARCHAR(50) DEFAULT 'UTC';",
        "ALTER TABLE clients ADD COLUMN IF NOT EXISTS language VARCHAR(10) DEFAULT 'en';",
        
        # Droppers table
        "ALTER TABLE droppers ADD COLUMN IF NOT EXISTS emergency_contact_name VARCHAR(255);",
        "ALTER TABLE droppers ADD COLUMN IF NOT EXISTS emergency_contact_phone VARCHAR(20);",
        "ALTER TABLE droppers ADD COLUMN IF NOT EXISTS email_notifications BOOLEAN DEFAULT true;",
        "ALTER TABLE droppers ADD COLUMN IF NOT EXISTS sms_notifications BOOLEAN DEFAULT true;",
        "ALTER TABLE droppers ADD COLUMN IF NOT EXISTS timezone VARCHAR(50) DEFAULT 'UTC';",
        "ALTER TABLE droppers ADD COLUMN IF NOT EXISTS language VARCHAR(10) DEFAULT 'en';",
    ]
    
    try:
        with engine.connect() as conn:
            for i, sql in enumerate(sql_commands, 1):
                print(f"[{i}/{len(sql_commands)}] Executing: {sql[:60]}...")
                conn.execute(text(sql))
                conn.commit()
            
            print()
            print("✅ All columns added successfully!")
            print()
            
            # Verify clients table
            print("Verifying clients table columns:")
            result = conn.execute(text("""
                SELECT column_name, data_type 
                FROM information_schema.columns 
                WHERE table_name = 'clients' 
                ORDER BY ordinal_position;
            """))
            
            for row in result:
                print(f"  ✓ {row[0]}: {row[1]}")
            
            print()
            print("=" * 80)
            print("✅ DATABASE SCHEMA FIXED!")
            print("=" * 80)
            print()
            print("You can now restart the backend and try logging in again.")
            print()
            
            return True
            
    except Exception as e:
        print(f"❌ ERROR: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    success = fix_database()
    sys.exit(0 if success else 1)
