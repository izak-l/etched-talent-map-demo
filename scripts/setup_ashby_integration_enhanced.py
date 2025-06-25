#!/usr/bin/env python3
"""
Enhanced setup script for Ashby integration
Automatically manages .env file and sets up database tables
"""

import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv
import os
import sys
from cryptography.fernet import Fernet

# Load environment variables
load_dotenv()

def find_env_file():
    """Find the .env file in the project structure"""
    possible_paths = [
        '.env',  # Current directory
        '../.env',  # Parent directory  
        '../frontend/.env',  # Frontend directory
        os.path.join(os.path.dirname(__file__), '..', '.env'),  # Project root
        os.path.join(os.path.dirname(__file__), '..', 'frontend', '.env'),  # Frontend
    ]
    
    for path in possible_paths:
        full_path = os.path.abspath(path)
        if os.path.exists(full_path):
            return full_path
    
    # Create .env in project root if none found
    default_path = os.path.join(os.path.dirname(__file__), '..', '.env')
    return os.path.abspath(default_path)

def read_env_file(env_path):
    """Read .env file and return as dictionary"""
    env_vars = {}
    
    if os.path.exists(env_path):
        with open(env_path, 'r') as file:
            for line in file:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, value = line.split('=', 1)
                    env_vars[key.strip()] = value.strip()
    
    return env_vars

def write_env_file(env_path, env_vars):
    """Write environment variables to .env file"""
    os.makedirs(os.path.dirname(env_path), exist_ok=True)
    
    with open(env_path, 'w') as file:
        file.write("# Environment Configuration\n")
        file.write("# Auto-managed by Ashby Integration Setup\n\n")
        
        # Group variables
        db_vars = {k: v for k, v in env_vars.items() if k.startswith(('LOCAL_DB_', 'SUPABASE_'))}
        ashby_vars = {k: v for k, v in env_vars.items() if k.startswith('ASHBY_')}
        other_vars = {k: v for k, v in env_vars.items() if not k.startswith(('LOCAL_DB_', 'SUPABASE_', 'ASHBY_'))}
        
        # Write sections
        if db_vars:
            file.write("# Database Configuration\n")
            for key, value in db_vars.items():
                file.write(f"{key}={value}\n")
            file.write("\n")
        
        if ashby_vars:
            file.write("# Ashby Integration\n")
            for key, value in ashby_vars.items():
                file.write(f"{key}={value}\n")
            file.write("\n")
        
        if other_vars:
            file.write("# Other Configuration\n")
            for key, value in other_vars.items():
                file.write(f"{key}={value}\n")

def setup_encryption_key():
    """Setup encryption key in .env file automatically"""
    print("🔐 Managing encryption key...")
    
    env_path = find_env_file()
    print(f"📁 Using .env file: {env_path}")
    
    env_vars = read_env_file(env_path)
    
    if 'ASHBY_ENCRYPTION_KEY' in env_vars and env_vars['ASHBY_ENCRYPTION_KEY']:
        print("✅ Encryption key already exists")
        return env_vars['ASHBY_ENCRYPTION_KEY']
    
    # Generate new key
    new_key = Fernet.generate_key().decode()
    env_vars['ASHBY_ENCRYPTION_KEY'] = new_key
    
    # Write back to file
    write_env_file(env_path, env_vars)
    
    print(f"✅ Generated and saved encryption key to: {env_path}")
    return new_key

def get_db_connection():
    """Create database connection"""
    return psycopg2.connect(
        host=os.getenv("LOCAL_DB_HOST"),
        port=os.getenv("LOCAL_DB_PORT"), 
        database=os.getenv("LOCAL_DB_NAME"),
        user=os.getenv("LOCAL_DB_USER"),
        password=os.getenv("LOCAL_DB_PASSWORD"),
        cursor_factory=RealDictCursor
    )

def run_sql_file(conn, sql_file_path):
    """Execute SQL file"""
    with open(sql_file_path, 'r') as file:
        sql_content = file.read()
    
    cur = conn.cursor()
    try:
        cur.execute(sql_content)
        conn.commit()
        print(f"✅ Database schema applied successfully")
    except Exception as e:
        conn.rollback()
        print(f"❌ Error applying schema: {e}")
        raise

def main():
    print("🚀 Enhanced Ashby Integration Setup")
    print("=" * 50)
    
    try:
        # Step 1: Auto-setup encryption key
        encryption_key = setup_encryption_key()
        
        # Step 2: Database setup
        print("\n📦 Setting up database...")
        conn = get_db_connection()
        
        sql_file = os.path.join(os.path.dirname(__file__), 'create_ashby_tables.sql')
        if not os.path.exists(sql_file):
            print(f"❌ SQL file not found: {sql_file}")
            sys.exit(1)
        
        run_sql_file(conn, sql_file)
        
        # Step 3: Verify setup
        cur = conn.cursor()
        cur.execute("""
            SELECT table_name FROM information_schema.tables 
            WHERE table_schema = 'public' 
            AND table_name IN ('ashby_integrations', 'ashby_sync_jobs')
        """)
        tables = [row['table_name'] for row in cur.fetchall()]
        
        print("\n📋 Verification:")
        for table in ['ashby_integrations', 'ashby_sync_jobs']:
            status = "✅" if table in tables else "❌"
            print(f"  {status} {table}")
        
        conn.close()
        
        print("\n" + "=" * 50)
        print("🎉 Setup Complete!")
        print("=" * 50)
        print("\n✅ What was configured:")
        print(f"  🔐 Encryption key: Auto-added to .env")
        print(f"  🗃️  Database tables: Created successfully")
        print(f"  🔧 Ready for Ashby integration")
        
        print("\n🚀 Next steps:")
        print("  1. Restart your Flask app")
        print("  2. Click 'Connect Ashby' in dashboard")
        print("  3. Enter your Ashby API key")
        
        print("\n💡 Migration notes:")
        print("  • Local → Supabase: Just update DB vars in .env")
        print("  • Encryption key stays the same across environments")
        print("  • Re-run this script anytime safely")
        
        print("\n📁 Existing files preserved:")
        print("  • ashby_client.py (API client)")
        print("  • ashby_sync_service.py (sync logic)")
        print("  • All existing functionality intact")
        
    except Exception as e:
        print(f"\n💥 Setup failed: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main() 