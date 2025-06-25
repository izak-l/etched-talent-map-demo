#!/usr/bin/env python3
"""
Delete a specific person by their exact LinkedIn URL.
Usage: python delete_specific_person.py "linkedin_url"
"""

import sys
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv
import os

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

def find_person_by_linkedin_url(cur, linkedin_url):
    """Find a person by their exact LinkedIn URL."""
    # Search in canonical_linkedin_url field
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
        WHERE p.canonical_linkedin_url = %s
    """, (linkedin_url,))
    
    person = cur.fetchone()
    
    if person:
        return person
    
    # Also check PeopleSources table in case the URL is stored there
    cur.execute("""
        SELECT DISTINCT
            p.user_id, 
            p.canonical_linkedin_url,
            p.scrape_status,
            p.last_scraped_at,
            li.firstname,
            li.lastname,
            li.headline,
            ps.linkedin_url as people_sources_url
        FROM People p
        LEFT JOIN LinkedinInfo li ON p.user_id = li.user_id
        LEFT JOIN PeopleSources ps ON p.user_id = ps.user_id
        WHERE ps.linkedin_url = %s
    """, (linkedin_url,))
    
    return cur.fetchone()

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
    
    # Check PeopleSources
    cur.execute("SELECT COUNT(*) as count FROM PeopleSources WHERE user_id = %s", (user_id,))
    counts['people_sources'] = cur.fetchone()['count']
    
    return counts

def delete_person_and_related_data(cur, user_id):
    """Delete a person and all their related data."""
    deleted_counts = {}
    
    # Delete from all related tables
    tables = ['PeopleSources', 'LinkedinInfo', 'Positions', 'Educations', 'Skills', 'Honors', 'Geo']
    
    for table in tables:
        cur.execute(f"DELETE FROM {table} WHERE user_id = %s", (user_id,))
        deleted_counts[table.lower()] = cur.rowcount
    
    # Finally delete from People table
    cur.execute("DELETE FROM People WHERE user_id = %s", (user_id,))
    deleted_counts['people'] = cur.rowcount
    
    return deleted_counts

def main():
    if len(sys.argv) != 2:
        print("Usage: python delete_specific_person.py \"linkedin_url\"")
        print("Example: python delete_specific_person.py \"linkedin.com/in/kailiang-fu\"")
        sys.exit(1)
    
    linkedin_url = sys.argv[1].strip()
    
    # Normalize the URL - remove https:// and www. if present
    if linkedin_url.startswith('https://'):
        linkedin_url = linkedin_url[8:]
    if linkedin_url.startswith('www.'):
        linkedin_url = linkedin_url[4:]
    
    print(f"Looking for person with LinkedIn URL: {linkedin_url}")
    
    conn = get_db_connection()
    cur = conn.cursor()
    
    try:
        person = find_person_by_linkedin_url(cur, linkedin_url)
        
        if not person:
            print(f"❌ No person found with LinkedIn URL: {linkedin_url}")
            return
        
        name = f"{person['firstname'] or 'Unknown'} {person['lastname'] or 'Unknown'}"
        
        print(f"Found person:")
        print(f"  Name: {name}")
        print(f"  User ID: {person['user_id']}")
        print(f"  Canonical URL: {person['canonical_linkedin_url']}")
        print(f"  Scrape Status: {person['scrape_status']}")
        
        # Get related data counts
        counts = get_related_data_counts(cur, person['user_id'])
        total_related = sum(counts.values())
        
        print(f"  Related data: {total_related} records")
        for table, count in counts.items():
            if count > 0:
                print(f"    - {count} {table} records")
        
        print()
        print("⚠️  This will permanently delete this person and ALL their related data!")
        
        # Check if person is in any sources
        cur.execute("""
            SELECT s.name 
            FROM PeopleSources ps
            JOIN Sources s ON ps.source_id = s.id
            WHERE ps.user_id = %s
        """, (person['user_id'],))
        
        sources = cur.fetchall()
        if sources:
            print(f"  Person is currently in {len(sources)} source(s):")
            for source in sources:
                print(f"    - {source['name']}")
        else:
            print("  Person is not in any sources (orphaned)")
        
        print()
        response = input("Do you want to proceed with deletion? (yes/no): ").lower().strip()
        if response not in ['yes', 'y']:
            print("Operation cancelled.")
            return
        
        print(f"Deleting {name} and all related data...")
        
        # Delete person and related data
        deleted_counts = delete_person_and_related_data(cur, person['user_id'])
        
        conn.commit()
        
        print()
        print(f"✅ Successfully deleted {name}!")
        print("Deleted records:")
        for table, count in deleted_counts.items():
            if count > 0:
                print(f"  - {count} {table} records")
        
        total_deleted = sum(deleted_counts.values())
        print(f"Total records deleted: {total_deleted}")
        
    except Exception as e:
        conn.rollback()
        print(f"❌ Error occurred: {e}")
        raise
    
    finally:
        conn.close()

if __name__ == "__main__":
    main() 