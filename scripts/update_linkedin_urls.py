#!/usr/bin/env python3

import os
import json
import psycopg2
from psycopg2.extras import RealDictCursor
import glob
import re
from datetime import datetime
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

def get_db_connection():
    """Create a connection to the PostgreSQL database."""
    conn = psycopg2.connect(
        host=os.getenv("LOCAL_DB_HOST"),
        port=os.getenv("LOCAL_DB_PORT"),
        database=os.getenv("LOCAL_DB_NAME"),
        user=os.getenv("LOCAL_DB_USER"),
        password=os.getenv("LOCAL_DB_PASSWORD"),
        cursor_factory=RealDictCursor
    )
    return conn

def extract_linkedin_info_from_filename(filename):
    """Extract LinkedIn username, profile ID, and scrape date from filename."""
    # Pattern: username_profileid_DD-MM-YYYY.json
    match = re.match(r'([^_]+)_(\d+|unknownid)_(\d{2}-\d{2}-\d{4})\.json', filename)
    
    if match:
        username = match.group(1)
        profile_id = match.group(2)
        date_str = match.group(3)
        
        # Convert profile_id to int if it's not 'unknownid'
        if profile_id != 'unknownid':
            try:
                profile_id = int(profile_id)
            except ValueError:
                profile_id = None
        else:
            profile_id = None
            
        # Parse the scrape date
        try:
            scrape_date = datetime.strptime(date_str, '%d-%m-%Y')
        except ValueError:
            scrape_date = None
            
        # Construct LinkedIn URL
        linkedin_url = f"https://www.linkedin.com/in/{username}"
        
        return linkedin_url, profile_id, scrape_date
    
    return None, None, None

def main():
    """Main function to update LinkedIn URLs in the database."""
    
    # Get all JSON files in the results directory
    results_dir = "results"
    if not os.path.exists(results_dir):
        print(f"Results directory '{results_dir}' not found!")
        return
    
    json_files = glob.glob(os.path.join(results_dir, "*.json"))
    
    if not json_files:
        print("No JSON files found in results directory!")
        return
    
    print(f"Found {len(json_files)} JSON files to process")
    
    # Connect to database
    conn = get_db_connection()
    cur = conn.cursor()
    
    # Track statistics
    stats = {
        'processed': 0,
        'matched_by_id': 0,
        'updated_people': 0,
        'updated_people_sources': 0,
        'updated_last_scraped': 0,
        'updated_scrape_status': 0,
        'no_profile_id': 0,
        'no_match': 0
    }
    
    try:
        for file_path in json_files:
            filename = os.path.basename(file_path)
            stats['processed'] += 1
            
            # Extract LinkedIn info from filename
            linkedin_url, profile_id, scrape_date = extract_linkedin_info_from_filename(filename)
            
            if not linkedin_url:
                print(f"Could not extract LinkedIn URL from filename: {filename}")
                stats['no_match'] += 1
                continue
            
            # Try to find the person in the database
            user_id = None
            
            if profile_id:
                # First try to match by LinkedIn profile ID
                cur.execute("""
                    SELECT p.user_id 
                    FROM People p
                    JOIN LinkedinInfo li ON p.user_id = li.user_id
                    WHERE li.id = %s
                """, (profile_id,))
                
                result = cur.fetchone()
                if result:
                    user_id = result['user_id']
                    stats['matched_by_id'] += 1
            else:
                stats['no_profile_id'] += 1
            
            if user_id:
                # Update the People table with canonical LinkedIn URL
                cur.execute("""
                    UPDATE People 
                    SET canonical_linkedin_url = %s 
                    WHERE user_id = %s AND canonical_linkedin_url IS NULL
                """, (linkedin_url, user_id))
                
                if cur.rowcount > 0:
                    stats['updated_people'] += 1
                    print(f"Updated People table for user_id {user_id}: {linkedin_url}")
                
                # Update last_scraped_at if we have a scrape date
                if scrape_date:
                    cur.execute("""
                        UPDATE People 
                        SET last_scraped_at = %s 
                        WHERE user_id = %s AND last_scraped_at IS NULL
                    """, (scrape_date, user_id))
                    
                    if cur.rowcount > 0:
                        stats['updated_last_scraped'] += 1
                        print(f"Updated last_scraped_at for user_id {user_id}: {scrape_date.strftime('%Y-%m-%d')}")
                
                # Update scrape_status to 'scraped' for profiles that have been scraped
                cur.execute("""
                    UPDATE People 
                    SET scrape_status = 'scraped' 
                    WHERE user_id = %s AND scrape_status != 'scraped'
                """, (user_id,))
                
                if cur.rowcount > 0:
                    stats['updated_scrape_status'] += 1
                    print(f"Updated scrape_status to 'scraped' for user_id {user_id}")
                
                # Update PeopleSources table with LinkedIn URL
                cur.execute("""
                    UPDATE PeopleSources 
                    SET linkedin_url = %s 
                    WHERE user_id = %s AND (linkedin_url IS NULL OR linkedin_url = '')
                """, (linkedin_url, user_id))
                
                if cur.rowcount > 0:
                    stats['updated_people_sources'] += cur.rowcount
                    print(f"Updated PeopleSources for user_id {user_id}: {linkedin_url}")
            else:
                print(f"No matching user found for {filename} (profile_id: {profile_id})")
                stats['no_match'] += 1
        
        # Commit all changes
        conn.commit()
        print("\nAll updates committed successfully!")
        
    except Exception as e:
        conn.rollback()
        print(f"Error occurred: {e}")
        raise
    
    finally:
        conn.close()
    
    # Print statistics
    print("\n" + "="*50)
    print("UPDATE STATISTICS")
    print("="*50)
    print(f"Files processed: {stats['processed']}")
    print(f"Matched by LinkedIn ID: {stats['matched_by_id']}")
    print(f"Files without profile ID: {stats['no_profile_id']}")
    print(f"No database match found: {stats['no_match']}")
    print(f"People table updated: {stats['updated_people']}")
    print(f"PeopleSources entries updated: {stats['updated_people_sources']}")
    print(f"Last scraped dates updated: {stats['updated_last_scraped']}")
    print(f"Scrape status updated: {stats['updated_scrape_status']}")
    
    # Show some examples of updated URLs
    print("\nVerifying updates...")
    conn = get_db_connection()
    cur = conn.cursor()
    
    cur.execute("""
        SELECT p.user_id, p.canonical_linkedin_url, p.last_scraped_at, p.scrape_status, li.firstname, li.lastname
        FROM People p
        JOIN LinkedinInfo li ON p.user_id = li.user_id
        WHERE p.canonical_linkedin_url IS NOT NULL
        LIMIT 5
    """)
    
    examples = cur.fetchall()
    if examples:
        print("\nSample updated records:")
        for example in examples:
            scrape_date = example['last_scraped_at'].strftime('%Y-%m-%d') if example['last_scraped_at'] else 'None'
            print(f"  {example['firstname']} {example['lastname']}: {example['canonical_linkedin_url']} (scraped: {scrape_date}, status: {example['scrape_status']})")
    
    conn.close()

if __name__ == "__main__":
    main() 