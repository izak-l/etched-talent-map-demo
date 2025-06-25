#!/usr/bin/env python3

import os
import json
import requests
import urllib.parse
from datetime import datetime, timedelta
import re
import time
import psycopg2
from psycopg2.extras import RealDictCursor, Json
from dotenv import load_dotenv
import logging

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

# Configuration
API_KEY = os.getenv("RAPID_API_KEY")
API_HOST = "linkedin-api8.p.rapidapi.com"
ENDPOINT = "https://linkedin-api8.p.rapidapi.com/get-profile-data-by-url"
REQUEST_DELAY = 5
MAX_AGE_DAYS = 30

class LinkedInScraper:
    """Service class for LinkedIn profile scraping and database operations"""
    
    def __init__(self, db_connection_params=None):
        if not API_KEY:
            raise ValueError("RAPID_API_KEY environment variable is not set")
        
        self.db_params = db_connection_params or {
            'host': os.getenv("LOCAL_DB_HOST"),
            'port': os.getenv("LOCAL_DB_PORT"),
            'database': os.getenv("LOCAL_DB_NAME"),
            'user': os.getenv("LOCAL_DB_USER"),
            'password': os.getenv("LOCAL_DB_PASSWORD")
        }
    
    def get_db_connection(self):
        """Create a database connection"""
        return psycopg2.connect(
            cursor_factory=RealDictCursor,
            **self.db_params
        )
    
    def extract_username_from_url(self, profile_url):
        """Extract the username from a LinkedIn URL"""
        username_match = re.search(r'linkedin\.com/in/([^/]+)', profile_url)
        if username_match:
            username = username_match.group(1)
        else:
            # Fallback: extract everything after the last slash
            username = profile_url.split('/')[-1]
            # Remove any query parameters
            username = username.split('?')[0]
        
        return username.strip()
    
    def normalize_linkedin_url(self, url):
        """Normalize LinkedIn URL to a consistent format"""
        # Remove trailing slashes and query parameters
        url = url.rstrip('/').split('?')[0]
        
        # If it starts with linkedin.com, prepend https://www.
        if url.startswith('linkedin.com'):
            url = f'https://www.{url}'

        return url
    
    def is_recently_scraped(self, linkedin_url):
        """Check if a profile was scraped recently"""
        conn = self.get_db_connection()
        try:
            cur = conn.cursor()
            cur.execute("""
                SELECT last_scraped_at 
                FROM People 
                WHERE canonical_linkedin_url = %s 
                AND last_scraped_at IS NOT NULL
            """, (linkedin_url,))
            
            result = cur.fetchone()
            if not result:
                return False
            
            last_scraped = result['last_scraped_at']
            age = datetime.now().date() - last_scraped.date()
            return age.days <= MAX_AGE_DAYS
            
        finally:
            conn.close()
    
    def fetch_profile_data(self, linkedin_url):
        """Fetch profile data from LinkedIn API"""
        encoded_url = urllib.parse.quote(linkedin_url)
        
        headers = {
            "x-rapidapi-host": API_HOST,
            "x-rapidapi-key": API_KEY
        }
        
        try:
            response = requests.get(f"{ENDPOINT}?url={encoded_url}", headers=headers)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to fetch {linkedin_url}: {e}")
            raise
    
    def components_to_date(self, date_dict):
        """Convert date components to a date object"""
        if not date_dict or not isinstance(date_dict, dict):
            return None
        
        year = date_dict.get('year')
        month = date_dict.get('month', 1)
        day = date_dict.get('day', 1)
        
        if not year:
            return None
        
        try:
            return datetime(year, month, day).date()
        except ValueError:
            return None
    
    def profile_exists_by_id(self, profile_id):
        """Check if a profile exists by LinkedIn ID"""
        if not profile_id:
            return False
        
        conn = self.get_db_connection()
        try:
            cur = conn.cursor()
            cur.execute("SELECT 1 FROM LinkedinInfo WHERE id = %s LIMIT 1", (profile_id,))
            return cur.fetchone() is not None
        finally:
            conn.close()
    
    def insert_profile_data(self, profile_data, linkedin_url, source_id=None):
        """Insert profile data into the database"""
        conn = self.get_db_connection()
        try:
            cur = conn.cursor()
            
            # 1. Validate profile data
            profile_id = profile_data.get('id')
            if not profile_id:
                # If no profile ID, generate a unique one or mark as failed
                logger.warning(f"No profile ID found for {linkedin_url}, marking as failed")
                # Update scrape status to failed
                cur.execute("""
                    UPDATE People 
                    SET scrape_status = 'failed', 
                        last_scraped_at = %s
                    WHERE canonical_linkedin_url = %s
                """, (datetime.now(), linkedin_url))
                
                # If person doesn't exist, create with failed status
                if cur.rowcount == 0:
                    cur.execute("""
                        INSERT INTO People (canonical_linkedin_url, last_scraped_at, scrape_status)
                        VALUES (%s, %s, 'failed')
                        RETURNING user_id
                    """, (linkedin_url, datetime.now()))
                
                conn.commit()
                raise ValueError(f"No LinkedIn profile ID found in API response for {linkedin_url}")
            
            # 2. Check if person already exists by LinkedIn URL (try both normalized and original)
            normalized_url = self.normalize_linkedin_url(linkedin_url)
            
            # Try to find by either the original URL or normalized URL
            cur.execute("""
                SELECT user_id FROM People 
                WHERE canonical_linkedin_url = %s OR canonical_linkedin_url = %s
            """, (linkedin_url, normalized_url))
            
            existing_person = cur.fetchone()
            
            if existing_person:
                user_id = existing_person['user_id']
                # Update existing person with normalized URL
                cur.execute("""
                    UPDATE People 
                    SET linkedin_details = %s, 
                        last_scraped_at = %s, 
                        scrape_status = 'scraped',
                        canonical_linkedin_url = %s
                    WHERE user_id = %s
                """, (Json(profile_data), datetime.now(), normalized_url, user_id))
                
                logger.info(f"Updated existing person with user_id: {user_id}")
            else:
                # Create new person
                cur.execute("""
                    INSERT INTO People (linkedin_details, canonical_linkedin_url, last_scraped_at, scrape_status)
                    VALUES (%s, %s, %s, 'scraped')
                    RETURNING user_id
                """, (Json(profile_data), normalized_url, datetime.now()))
                user_id = cur.fetchone()['user_id']
                
                logger.info(f"Created new person with user_id: {user_id}")
            
            # 3. Insert/Update LinkedinInfo (only if we have valid profile_id)
            first_name = profile_data.get('firstName', '')
            last_name = profile_data.get('lastName', '')
            headline = profile_data.get('headline', '')
            
            # Check if LinkedinInfo already exists for this profile ID
            cur.execute("SELECT user_id FROM LinkedinInfo WHERE id = %s", (profile_id,))
            existing_linkedin_info = cur.fetchone()
            
            if existing_linkedin_info:
                # Update existing LinkedinInfo
                cur.execute("""
                    UPDATE LinkedinInfo 
                    SET firstName = %s, lastName = %s, headline = %s, user_id = %s
                    WHERE id = %s
                """, (first_name, last_name, headline, user_id, profile_id))
            else:
                # Insert new LinkedinInfo
                cur.execute("""
                    INSERT INTO LinkedinInfo (user_id, id, firstName, lastName, headline)
                    VALUES (%s, %s, %s, %s, %s)
                """, (user_id, profile_id, first_name, last_name, headline))
            
            # 4. Insert location info
            geo_info = profile_data.get('geo', {})
            if isinstance(geo_info, dict):
                country = geo_info.get('country', '')
                city = geo_info.get('city', '')
                country_code = geo_info.get('countryCode', '')
                
                cur.execute("""
                    INSERT INTO Geo (user_id, country, city, countryCode)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (user_id) DO UPDATE SET
                        country = EXCLUDED.country,
                        city = EXCLUDED.city,
                        countryCode = EXCLUDED.countryCode
                """, (user_id, country, city, country_code))
            
            # 5. Clear and re-insert education info
            cur.execute("DELETE FROM Educations WHERE user_id = %s", (user_id,))
            education_list = profile_data.get('educations', [])
            if education_list and isinstance(education_list, list):
                for edu in education_list:
                    school_name = edu.get('schoolName', '')
                    school_id = edu.get('schoolId', '')
                    field_of_study = edu.get('fieldOfStudy', '')
                    degree = edu.get('degree', '')
                    description = edu.get('description', '')
                    activities = edu.get('activities', '')
                    
                    start_date = self.components_to_date(edu.get('start'))
                    end_date = self.components_to_date(edu.get('end'))
                    
                    cur.execute("""
                        INSERT INTO Educations (
                            user_id, schoolName, schoolId, fieldOfStudy, degree, 
                            startDate, endDate, description, activities
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """, (
                        user_id, school_name, school_id, field_of_study, degree, 
                        start_date, end_date, description, activities
                    ))
            
            # 6. Clear and re-insert positions
            cur.execute("DELETE FROM Positions WHERE user_id = %s", (user_id,))
            positions_list = profile_data.get('position', [])
            if positions_list and isinstance(positions_list, list):
                for position in positions_list:
                    company_id = position.get('companyId', 0)
                    company_name = position.get('companyName', '')
                    title = position.get('title', '')
                    location = position.get('location', '')
                    description = position.get('description', '')
                    employment_type = position.get('employmentType', '')
                    
                    start_date = self.components_to_date(position.get('start'))
                    end_date = self.components_to_date(position.get('end'))
                    
                    cur.execute("""
                        INSERT INTO Positions (
                            user_id, companyId, companyName, title, location, description, employmentType,
                            startDate, endDate
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """, (
                        user_id, company_id, company_name, title, location, description, employment_type,
                        start_date, end_date
                    ))
            
            # 7. Clear and re-insert skills
            cur.execute("DELETE FROM Skills WHERE user_id = %s", (user_id,))
            skills_list = profile_data.get('skills', [])
            if skills_list and isinstance(skills_list, list):
                for skill in skills_list:
                    skill_name = skill.get('name', '')
                    if skill_name:
                        cur.execute("""
                            INSERT INTO Skills (user_id, name)
                            VALUES (%s, %s)
                        """, (user_id, skill_name))
            
            # 8. Clear and re-insert honors
            cur.execute("DELETE FROM Honors WHERE user_id = %s", (user_id,))
            honors_list = profile_data.get('honors', [])
            if honors_list and isinstance(honors_list, list):
                for honor in honors_list:
                    title = honor.get('title', '')
                    if title:
                        cur.execute("""
                            INSERT INTO Honors (user_id, title)
                            VALUES (%s, %s)
                        """, (user_id, title))
            
            # 9. Update PeopleSources if source_id provided
            if source_id:
                # Update PeopleSources to link this user_id and ensure URL is normalized
                cur.execute("""
                    UPDATE PeopleSources 
                    SET linkedin_url = %s
                    WHERE (linkedin_url = %s OR linkedin_url = %s) AND source_id = %s
                """, (normalized_url, linkedin_url, normalized_url, source_id))
                
                # If no rows were updated, try to match by user_id
                if cur.rowcount == 0:
                    cur.execute("""
                        UPDATE PeopleSources 
                        SET linkedin_url = %s
                        WHERE user_id = %s AND source_id = %s
                    """, (normalized_url, user_id, source_id))
                    
                logger.info(f"Updated PeopleSources for user_id: {user_id}, source_id: {source_id}")
            
            conn.commit()
            return user_id
            
        except Exception as e:
            conn.rollback()
            logger.error(f"Error inserting profile data: {e}")
            raise
        finally:
            conn.close()
    
    def scrape_single_profile(self, linkedin_url, source_id=None, force=False):
        """Scrape a single LinkedIn profile"""
        normalized_url = self.normalize_linkedin_url(linkedin_url)
        
        logger.info(f"Scraping profile: {linkedin_url} -> normalized: {normalized_url}")
        
        # Check if recently scraped (unless forced)
        if not force and self.is_recently_scraped(normalized_url):
            return {
                'success': False,
                'message': f'Profile was scraped recently (within {MAX_AGE_DAYS} days)',
                'skipped': True
            }
        
        try:
            # Fetch profile data
            profile_data = self.fetch_profile_data(normalized_url)
            
            # Insert into database - use original URL for matching in database
            user_id = self.insert_profile_data(profile_data, linkedin_url, source_id)
            
            logger.info(f"Successfully scraped and inserted profile with user_id: {user_id}")
            
            return {
                'success': True,
                'user_id': user_id,
                'profile_id': profile_data.get('id'),
                'name': f"{profile_data.get('firstName', '')} {profile_data.get('lastName', '')}".strip(),
                'linkedin_url': normalized_url
            }
            
        except Exception as e:
            logger.error(f"Failed to scrape {linkedin_url}: {e}")
            return {
                'success': False,
                'message': str(e),
                'linkedin_url': normalized_url
            }
    
    def scrape_bulk_profiles(self, linkedin_urls, source_id=None, force=False, delay=None):
        """Scrape multiple LinkedIn profiles with optional delay"""
        if delay is None:
            delay = REQUEST_DELAY
        
        results = {
            'processed': 0,
            'successful': 0,
            'skipped': 0,
            'failed': 0,
            'results': []
        }
        
        for i, url in enumerate(linkedin_urls):
            # Add delay between requests (except for the first one)
            if i > 0 and delay > 0:
                logger.info(f"Waiting {delay} seconds before next request...")
                time.sleep(delay)
            
            result = self.scrape_single_profile(url, source_id, force)
            results['processed'] += 1
            results['results'].append(result)
            
            if result['success']:
                results['successful'] += 1
                logger.info(f"Successfully scraped: {result.get('name', 'Unknown')} ({result.get('linkedin_url')})")
            elif result.get('skipped'):
                results['skipped'] += 1
                logger.info(f"Skipped (recently scraped): {url}")
            else:
                results['failed'] += 1
                logger.error(f"Failed to scrape: {url} - {result.get('message', 'Unknown error')}")
        
        return results 