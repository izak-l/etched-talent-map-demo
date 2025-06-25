#!/usr/bin/env python3

import os
import json
import glob
import psycopg2
from psycopg2.extras import Json
from datetime import datetime
from dotenv import load_dotenv

# DB config (supabase)
load_dotenv()

# Directory with JSON files
RESULTS_DIR = "./results"
# Add support for custom input directory
INPUT_DIR = None  # Can be set via command line arguments

def connect_to_db():
    """Establish connection to PostgreSQL database"""
    try:
        conn = psycopg2.connect(
            host=os.getenv("LOCAL_DB_HOST"),
            port=os.getenv("LOCAL_DB_PORT"),
            database=os.getenv("LOCAL_DB_NAME"),
            user=os.getenv("LOCAL_DB_USER"),
            password=os.getenv("LOCAL_DB_PASSWORD")
        )
        # conn = psycopg2.connect( # remote connection
        #     os.getenv("SUPABASE_DB_URL")
        # )
        return conn
    except Exception as e:
        print(f"Error connecting to the database: {e}")
        return None

def create_tables(conn):
    """Create the required tables if they don't exist"""
    cursor = conn.cursor()
    
    # Create People table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS People (
        user_id SERIAL PRIMARY KEY,
        linkedin_details JSONB
    )
    """)
    
    # Create LinkedinInfo table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS LinkedinInfo (
        user_id INTEGER REFERENCES People(user_id),
        id BIGINT PRIMARY KEY,
        firstName VARCHAR(255),
        lastName VARCHAR(255),
        headline TEXT
    )
    """)
    
    # Create Geo table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS Geo (
        user_id INTEGER REFERENCES People(user_id),
        country VARCHAR(255),
        city VARCHAR(255),
        countryCode VARCHAR(10),
        PRIMARY KEY (user_id)
    )
    """)
    
    # Create Educations table - Using DATE instead of components
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS Educations (
        id SERIAL PRIMARY KEY,
        user_id INTEGER REFERENCES People(user_id),
        schoolName TEXT,
        schoolId VARCHAR(255),
        fieldOfStudy TEXT,
        degree TEXT,
        startDate DATE,
        endDate DATE,
        description TEXT,
        activities TEXT
    )
    """)
    
    # Create Positions table - Using DATE instead of components
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS Positions (
        id SERIAL PRIMARY KEY,
        user_id INTEGER REFERENCES People(user_id),
        companyId BIGINT,
        companyName TEXT,
        title TEXT,
        location TEXT,
        description TEXT,
        employmentType TEXT,
        startDate DATE,
        endDate DATE
    )
    """)
    
    # Create Skills table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS Skills (
        id SERIAL PRIMARY KEY,
        user_id INTEGER REFERENCES People(user_id),
        name TEXT
    )
    """)
    
    # Create Honors table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS Honors (
        id SERIAL PRIMARY KEY,
        user_id INTEGER REFERENCES People(user_id),
        title TEXT
    )
    """)
    
    conn.commit()

def components_to_date(date_dict):
    """Convert date components to a PostgreSQL date in DD-MM-YYYY format"""
    if not isinstance(date_dict, dict):
        return None
        
    year = date_dict.get('year', 0)
    month = date_dict.get('month', 0)
    day = date_dict.get('day', 0)
    
    # Return None for empty dates
    if year == 0:
        return None
        
    # Use default values for missing components
    if month == 0:
        month = 1
    if day == 0:
        day = 1
        
    try:
        # Create date object
        date_obj = datetime(year=year, month=month, day=day).date()
        return date_obj
    except (ValueError, TypeError):
        # Return None for invalid dates
        return None

def profile_exists(conn, profile_id):
    """Check if a profile with the given ID already exists in the database"""
    if not profile_id:
        return False
        
    cursor = conn.cursor()
    
    # Check if profile exists by ID
    cursor.execute("""
    SELECT 1 FROM LinkedinInfo WHERE id = %s LIMIT 1
    """, (profile_id,))
    
    exists = cursor.fetchone() is not None
    
    return exists

def insert_profile_data(conn, profile_data):
    """Insert a LinkedIn profile into the database"""
    cursor = conn.cursor()
    
    try:
        # 1. Insert into People table
        cursor.execute(
            "INSERT INTO People (linkedin_details) VALUES (%s) RETURNING user_id",
            (Json(profile_data),)
        )
        user_id = cursor.fetchone()[0]
        
        # 2. Insert basic info
        profile_id = profile_data.get('id')
        first_name = profile_data.get('firstName', '')
        last_name = profile_data.get('lastName', '')
        headline = profile_data.get('headline', '')
        
        cursor.execute("""
        INSERT INTO LinkedinInfo (user_id, id, firstName, lastName, headline)
        VALUES (%s, %s, %s, %s, %s)
        """, (user_id, profile_id, first_name, last_name, headline))
        
        # 3. Insert location info - Updated to match the correct structure
        geo_info = profile_data.get('geo', {})
        if isinstance(geo_info, dict):
            country = geo_info.get('country', '')
            city = geo_info.get('city', '')
            # full_location = geo_info.get('full', '')
            country_code = geo_info.get('countryCode', '')
            
            cursor.execute("""
            INSERT INTO Geo (user_id, country, city, countryCode)
            VALUES (%s, %s, %s, %s)
            """, (user_id, country, city, country_code))
        
        # 4. Insert education info - Updated with date formatting
        education_list = profile_data.get('educations', [])
        if education_list and isinstance(education_list, list):
            for edu in education_list:
                school_name = edu.get('schoolName', '')
                school_id = edu.get('schoolId', '')
                field_of_study = edu.get('fieldOfStudy', '')
                degree = edu.get('degree', '')
                description = edu.get('description', '')
                activities = edu.get('activities', '')
                
                # Convert date components to DATE objects
                start_date = components_to_date(edu.get('start'))
                end_date = components_to_date(edu.get('end'))
                
                cursor.execute("""
                INSERT INTO Educations (
                    user_id, schoolName, schoolId, fieldOfStudy, degree, 
                    startDate, endDate, description, activities
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (
                    user_id, school_name, school_id, field_of_study, degree, 
                    start_date, end_date, description, activities
                ))
        
        # 5. Insert positions - Updated with date formatting
        positions_list = profile_data.get('position', [])
        if positions_list and isinstance(positions_list, list):
            for position in positions_list:
                company_id = position.get('companyId', 0)
                company_name = position.get('companyName', '')
                title = position.get('title', '')
                location = position.get('location', '')
                description = position.get('description', '')
                employment_type = position.get('employmentType', '')
                
                # Convert date components to DATE objects
                start_date = components_to_date(position.get('start'))
                end_date = components_to_date(position.get('end'))
                
                cursor.execute("""
                INSERT INTO Positions (
                    user_id, companyId, companyName, title, location, description, employmentType,
                    startDate, endDate
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (
                    user_id, company_id, company_name, title, location, description, employment_type,
                    start_date, end_date
                ))
        
        # 6. Insert skills
        skills_list = profile_data.get('skills', [])
        if skills_list and isinstance(skills_list, list):
            for skill in skills_list:
                skill_name = skill.get('name', '')
                if skill_name:
                    cursor.execute("""
                    INSERT INTO Skills (user_id, name)
                    VALUES (%s, %s)
                    """, (user_id, skill_name))
        
        # 7. Insert honors
        honors_list = profile_data.get('honors', [])
        if honors_list and isinstance(honors_list, list):
            for honor in honors_list:
                title = honor.get('title', '')
                if title:
                    cursor.execute("""
                    INSERT INTO Honors (user_id, title)
                    VALUES (%s, %s)
                    """, (user_id, title))
        
        conn.commit()
        return True
    
    except Exception as e:
        conn.rollback()
        print(f"Error inserting profile data: {e}")
        return False

def process_json_files(directory=None):
    """Process all JSON files in the specified directory"""
    conn = connect_to_db()
    if not conn:
        return
    
    create_tables(conn)
    
    # Get all JSON files from the specified directory or use default
    target_dir = directory or RESULTS_DIR
    json_files = glob.glob(os.path.join(target_dir, "*.json"))
    total_files = len(json_files)
    
    print(f"Found {total_files} JSON files to process in {target_dir}")
    
    processed_count = 0
    skipped_count = 0
    
    for i, file_path in enumerate(json_files, 1):
        file_name = os.path.basename(file_path)
        print(f"Processing file {i}/{total_files}: {file_name}")
        
        try:
            # Load JSON data
            with open(file_path, 'r') as f:
                profile_data = json.load(f)
            
            # Get profile ID
            profile_id = profile_data.get('id')
            
            # Check if profile already exists
            if profile_exists(conn, profile_id):
                print(f"Skipping {file_name} - Profile ID {profile_id} already exists in database")
                skipped_count += 1
                continue
            
            # Insert the profile
            success = insert_profile_data(conn, profile_data)
            if success:
                print(f"Successfully inserted data from {file_name}")
                processed_count += 1
            else:
                print(f"Failed to insert data from {file_name}")
                
        except Exception as e:
            print(f"Error processing file {file_path}: {e}")
    
    conn.close()
    
    # Print summary
    print("\nImport completed!")
    print(f"Total files processed: {processed_count}")
    print(f"Files skipped (duplicates): {skipped_count}")
    print(f"Total files examined: {total_files}")

if __name__ == "__main__":
    import sys
    
    # Allow specifying a custom directory via command line
    if len(sys.argv) > 1:
        custom_dir = sys.argv[1]
        process_json_files(custom_dir)
    else:
        process_json_files() 