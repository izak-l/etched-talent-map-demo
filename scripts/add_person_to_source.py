#!/usr/bin/env python3
"""
Add a person to a specific source by name.
Usage: python add_person_to_source.py "First Last" "Source Name"
"""

import sys
import psycopg2
from psycopg2.extras import RealDictCursor, Json
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

def find_person_by_name(cur, full_name):
    """Find a person by their full name."""
    parts = full_name.strip().split()
    if len(parts) < 2:
        print(f"Please provide both first and last name")
        return None
    
    first_name = parts[0]
    last_name = " ".join(parts[1:])  # Handle multi-part last names
    
    # Search for the person
    cur.execute("""
        SELECT p.user_id, p.canonical_linkedin_url, li.firstname, li.lastname
        FROM People p
        JOIN LinkedinInfo li ON p.user_id = li.user_id
        WHERE LOWER(li.firstname) LIKE %s AND LOWER(li.lastname) LIKE %s
    """, (f"%{first_name.lower()}%", f"%{last_name.lower()}%"))
    
    people = cur.fetchall()
    
    if len(people) == 0:
        print(f"No person found matching '{full_name}'")
        return None
    elif len(people) == 1:
        return people[0]
    else:
        print(f"Multiple people found matching '{full_name}':")
        for i, person in enumerate(people):
            print(f"  {i+1}. {person['firstname']} {person['lastname']} (ID: {person['user_id']})")
        
        while True:
            try:
                choice = input("Enter the number of the person to add (or 'q' to quit): ")
                if choice.lower() == 'q':
                    return None
                choice = int(choice) - 1
                if 0 <= choice < len(people):
                    return people[choice]
                else:
                    print("Invalid choice. Please try again.")
            except ValueError:
                print("Please enter a valid number or 'q' to quit.")

def find_or_create_source(cur, source_name):
    """Find an existing source or create it if it doesn't exist."""
    # Check if source exists
    cur.execute("SELECT * FROM Sources WHERE name = %s", (source_name,))
    source = cur.fetchone()
    
    if source:
        print(f"Found existing source: {source['name']} (ID: {source['id']})")
        return source['id']
    
    # Create new source
    print(f"Source '{source_name}' not found. Creating it...")
    
    # Determine source type and metadata based on name
    source_type = 'manual'
    metadata_schema = {}
    description = f"Manual source: {source_name}"
    
    if 'olympiad' in source_name.lower() or 'finalists' in source_name.lower():
        source_type = 'olympiad'
        if 'physio' in source_name.lower():
            metadata_schema = {
                'competition': 'PHYSIO',
                'fields': ['division', 'year', 'rank', 'school', 'country']
            }
            description = "Physics Olympiad finalists and high performers"
        elif 'usaco' in source_name.lower():
            metadata_schema = {
                'competition': 'USACO',
                'fields': ['division', 'year', 'rank', 'school']
            }
            description = "USA Computing Olympiad finalists and high performers"
    elif 'company' in source_name.lower() or any(word in source_name.lower() for word in ['employees', 'team', 'staff']):
        source_type = 'company'
        metadata_schema = {
            'fields': ['department', 'role', 'start_date']
        }
    
    cur.execute("""
        INSERT INTO Sources (name, description, source_type, metadata_schema)
        VALUES (%s, %s, %s, %s)
        RETURNING id
    """, (source_name, description, source_type, Json(metadata_schema)))
    
    source_id = cur.fetchone()['id']
    print(f"Created new source: {source_name} (ID: {source_id})")
    return source_id

def add_person_to_source(cur, person, source_id, source_name):
    """Add a person to a source."""
    # Check if person is already in the source
    cur.execute("""
        SELECT id FROM PeopleSources 
        WHERE user_id = %s AND source_id = %s
    """, (person['user_id'], source_id))
    
    if cur.fetchone():
        print(f"{person['firstname']} {person['lastname']} is already in {source_name}")
        return False
    
    # Add person to source
    cur.execute("""
        INSERT INTO PeopleSources (user_id, source_id, linkedin_url, source_metadata)
        VALUES (%s, %s, %s, %s)
        RETURNING id
    """, (
        person['user_id'],
        source_id,
        person['canonical_linkedin_url'] or '',
        Json({'manually_added': True, 'added_by_script': True})
    ))
    
    ps_id = cur.fetchone()['id']
    print(f"Successfully added {person['firstname']} {person['lastname']} to {source_name} (PeopleSource ID: {ps_id})")
    return True

def main():
    if len(sys.argv) != 3:
        print("Usage: python add_person_to_source.py \"First Last\" \"Source Name\"")
        print("Example: python add_person_to_source.py \"Kailiang Fu\" \"PHYSIO Finalists\"")
        sys.exit(1)
    
    full_name = sys.argv[1]
    source_name = sys.argv[2]
    
    conn = get_db_connection()
    cur = conn.cursor()
    
    try:
        print(f"Looking for person: {full_name}")
        person = find_person_by_name(cur, full_name)
        
        if not person:
            return
        
        print(f"Found: {person['firstname']} {person['lastname']} (User ID: {person['user_id']})")
        
        print(f"Looking for source: {source_name}")
        source_id = find_or_create_source(cur, source_name)
        
        success = add_person_to_source(cur, person, source_id, source_name)
        
        if success:
            conn.commit()
            print("\nOperation completed successfully!")
        else:
            print("\nNo changes made.")
        
    except Exception as e:
        conn.rollback()
        print(f"Error occurred: {e}")
        raise
    
    finally:
        conn.close()

if __name__ == "__main__":
    main() 