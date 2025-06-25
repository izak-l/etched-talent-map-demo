#!/usr/bin/env python3
"""
Clean up orphaned people who exist in the People table but are not associated with any source.
These are typically leftover entries from data migrations or incomplete imports.
"""

import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv
import os
import sys

# Load environment variables
load_dotenv()

def get_db_connection():
    """Create a connection to the PostgreSQL database."""
    return psycopg2.connect(
        host=os.getenv("LOCAL_DB_HOST"),
        port=os.getenv("LOCAL_DB_PORT"), 
        database=os.getenv("LOCAL_DB_NAME"),
        user=os.getenv("LOCAL_DB_USER"),
        password=os.getenv("LOCAL_DB_PASSWORD"),
        cursor_factory=RealDictCursor
    )

def find_orphaned_people(cur):
    """Find people who are not in any source."""
    cur.execute("""
        SELECT 
            p.user_id, 
            p.canonical_linkedin_url,
            p.scrape_status,
            p.last_scraped_at,
            li.firstname,
            li.lastname,
            li.headline
        FROM People p
        LEFT JOIN LinkedinInfo li ON p.user_id = li.user_id
        WHERE p.user_id NOT IN (
            SELECT DISTINCT user_id 
            FROM PeopleSources 
            WHERE user_id IS NOT NULL
        )
        ORDER BY li.lastname, li.firstname
    """)
    
    return cur.fetchall()

def get_related_data_counts(cur, user_id):
    """Get counts of related data for a user."""
    counts = {}
    
    # Check LinkedinInfo
    cur.execute("SELECT COUNT(*) as count FROM LinkedinInfo WHERE user_id = %s", (user_id,))
    counts['linkedin_info'] = cur.fetchone()['count']
    
    # Check Positions
    cur.execute("SELECT COUNT(*) as count FROM Positions WHERE user_id = %s", (user_id,))
    counts['positions'] = cur.fetchone()['count']
    
    # Check Educations
    cur.execute("SELECT COUNT(*) as count FROM Educations WHERE user_id = %s", (user_id,))
    counts['educations'] = cur.fetchone()['count']
    
    # Check Skills
    cur.execute("SELECT COUNT(*) as count FROM Skills WHERE user_id = %s", (user_id,))
    counts['skills'] = cur.fetchone()['count']
    
    # Check Honors
    cur.execute("SELECT COUNT(*) as count FROM Honors WHERE user_id = %s", (user_id,))
    counts['honors'] = cur.fetchone()['count']
    
    # Check Geo
    cur.execute("SELECT COUNT(*) as count FROM Geo WHERE user_id = %s", (user_id,))
    counts['geo'] = cur.fetchone()['count']
    
    return counts

def delete_person_and_related_data(cur, user_id):
    """Delete a person and all their related data."""
    deleted_counts = {}
    
    # Delete from all related tables (foreign key constraints should handle this, but let's be explicit)
    tables = ['LinkedinInfo', 'Positions', 'Educations', 'Skills', 'Honors', 'Geo']
    
    for table in tables:
        cur.execute(f"DELETE FROM {table} WHERE user_id = %s", (user_id,))
        deleted_counts[table.lower()] = cur.rowcount
    
    # Finally delete from People table
    cur.execute("DELETE FROM People WHERE user_id = %s", (user_id,))
    deleted_counts['people'] = cur.rowcount
    
    return deleted_counts

def main():
    """Main function to identify and optionally delete orphaned people."""
    
    # Check for dry-run flag
    dry_run = '--dry-run' in sys.argv or '-n' in sys.argv
    force = '--force' in sys.argv or '-f' in sys.argv
    
    if dry_run:
        print("🔍 DRY RUN MODE - No changes will be made")
        print()
    
    conn = get_db_connection()
    cur = conn.cursor()
    
    try:
        print("Finding orphaned people...")
        orphaned_people = find_orphaned_people(cur)
        
        if not orphaned_people:
            print("✅ No orphaned people found! Database is clean.")
            return
        
        print(f"Found {len(orphaned_people)} orphaned people:")
        print()
        
        total_related_data = 0
        
        for i, person in enumerate(orphaned_people, 1):
            name = f"{person['firstname'] or 'Unknown'} {person['lastname'] or 'Unknown'}"
            linkedin_url = person['canonical_linkedin_url'] or 'No URL'
            status = person['scrape_status'] or 'Unknown'
            
            print(f"{i}. {name}")
            print(f"   User ID: {person['user_id']}")
            print(f"   LinkedIn: {linkedin_url}")
            print(f"   Status: {status}")
            
            # Get related data counts
            counts = get_related_data_counts(cur, person['user_id'])
            related_total = sum(counts.values())
            total_related_data += related_total
            
            if related_total > 0:
                print(f"   Related data: {related_total} records", end="")
                details = []
                for table, count in counts.items():
                    if count > 0:
                        details.append(f"{count} {table}")
                if details:
                    print(f" ({', '.join(details)})")
                else:
                    print()
            else:
                print(f"   Related data: None")
            print()
        
        print(f"Total orphaned people: {len(orphaned_people)}")
        print(f"Total related data records: {total_related_data}")
        print()
        
        if dry_run:
            print("🔍 This was a dry run. Use --force to actually delete these records.")
            return
        
        if not force:
            print("⚠️  This will permanently delete these people and all their related data!")
            print("   Use --force flag to proceed, or --dry-run to preview without changes.")
            
            response = input("Do you want to proceed? (yes/no): ").lower().strip()
            if response not in ['yes', 'y']:
                print("Operation cancelled.")
                return
        
        print("Deleting orphaned people and their related data...")
        
        total_deleted = 0
        total_related_deleted = 0
        
        for person in orphaned_people:
            name = f"{person['firstname'] or 'Unknown'} {person['lastname'] or 'Unknown'}"
            
            # Delete person and related data
            deleted_counts = delete_person_and_related_data(cur, person['user_id'])
            
            people_deleted = deleted_counts.get('people', 0)
            related_deleted = sum(v for k, v in deleted_counts.items() if k != 'people')
            
            total_deleted += people_deleted
            total_related_deleted += related_deleted
            
            print(f"✅ Deleted {name} (User ID: {person['user_id']}) and {related_deleted} related records")
        
        conn.commit()
        
        print()
        print(f"🎉 Cleanup completed successfully!")
        print(f"   Deleted {total_deleted} orphaned people")
        print(f"   Deleted {total_related_deleted} related data records")
        print()
        
        # Show final stats
        cur.execute("SELECT COUNT(*) as count FROM People")
        remaining_people = cur.fetchone()['count']
        
        cur.execute("""
            SELECT COUNT(*) as count 
            FROM People p
            WHERE p.user_id NOT IN (
                SELECT DISTINCT user_id 
                FROM PeopleSources 
                WHERE user_id IS NOT NULL
            )
        """)
        remaining_orphaned = cur.fetchone()['count']
        
        print(f"Database now has {remaining_people} total people")
        print(f"Remaining orphaned people: {remaining_orphaned}")
        
    except Exception as e:
        conn.rollback()
        print(f"❌ Error occurred: {e}")
        raise
    
    finally:
        conn.close()

def print_usage():
    """Print usage information."""
    print("Usage: python cleanup_orphaned_people.py [options]")
    print()
    print("Options:")
    print("  --dry-run, -n    Preview what would be deleted without making changes")
    print("  --force, -f      Delete without confirmation prompt")
    print()
    print("Examples:")
    print("  python cleanup_orphaned_people.py --dry-run    # Preview orphaned people")
    print("  python cleanup_orphaned_people.py --force      # Delete without prompt")
    print("  python cleanup_orphaned_people.py              # Interactive mode")

if __name__ == "__main__":
    if '--help' in sys.argv or '-h' in sys.argv:
        print_usage()
        sys.exit(0)
    
    main() 