#!/usr/bin/env python3

import os
import json
import requests
import urllib.parse
from datetime import datetime, timedelta
import re
import time
import glob
from dotenv import load_dotenv

load_dotenv()

# CONFIGURATION
API_KEY = os.getenv("RAPID_API_KEY")
API_HOST = "linkedin-api8.p.rapidapi.com"
ENDPOINT = "https://linkedin-api8.p.rapidapi.com/get-profile-data-by-url"
INPUT_FILE = "linkedin_urls.txt"  # Change if needed
OUTPUT_DIR = "./results"
REQUEST_DELAY = 5
DATE = datetime.now().strftime('%d-%m-%Y')
MAX_AGE_DAYS = 30  # Skip profiles fetched within the last month

# Create output directory if it doesn't exist
os.makedirs(OUTPUT_DIR, exist_ok=True)

if not API_KEY:
    raise ValueError("RAPID_API_KEY environment variable is not set. Please add it to your .env file.")

def extract_username_from_url(profile_url):
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

def get_existing_profiles():
    """Get a dictionary of existing profiles with usernames as keys and dates as values"""
    existing_profiles = {}
    
    # Get all JSON files in the output directory
    json_files = glob.glob(os.path.join(OUTPUT_DIR, "*.json"))
    
    for file_path in json_files:
        filename = os.path.basename(file_path)
        
        # Extract username and date from filename
        # Pattern: username_profileid_DD-MM-YYYY.json
        match = re.match(r'([^_]+)_\d+_(\d{2}-\d{2}-\d{4})\.json', filename)
        
        if match:
            username = match.group(1)
            date_str = match.group(2)
            
            try:
                # Convert date string to datetime object
                file_date = datetime.strptime(date_str, '%d-%m-%Y')
                existing_profiles[username] = file_date
            except ValueError:
                # Skip files with invalid dates
                continue
    
    return existing_profiles

def is_recently_fetched(username, existing_profiles):
    """Check if a profile was fetched recently (within MAX_AGE_DAYS)"""
    if username not in existing_profiles:
        return False
        
    file_date = existing_profiles[username]
    current_date = datetime.now()
    age = current_date - file_date
    
    return age.days <= MAX_AGE_DAYS

# Get existing profiles
existing_profiles = get_existing_profiles()
print(f"Found {len(existing_profiles)} existing profiles in {OUTPUT_DIR}")

# Read LinkedIn URLs from input file
with open(INPUT_FILE, 'r') as file:
    linkedin_urls = [line.strip() for line in file if line.strip()]

# Initialize counters
total_urls = len(linkedin_urls)
processed = 0
skipped = 0
failed = 0

print(f"Processing {total_urls} LinkedIn URLs")

# Process each URL
for index, profile_url in enumerate(linkedin_urls):
    # Extract username from URL
    username = extract_username_from_url(profile_url)
    
    # Check if the profile was recently fetched
    if is_recently_fetched(username, existing_profiles):
        print(f"Skipping {profile_url} - Already fetched within the last {MAX_AGE_DAYS} days")
        skipped += 1
        continue
    
    # Add delay between requests (except before the first one)
    if index > 0 and processed > 0:  # Only delay if we made at least one request
        print(f"Waiting {REQUEST_DELAY} seconds before next request...")
        time.sleep(REQUEST_DELAY)
    
    print(f"Processing: {profile_url}")
    
    # URL encode the LinkedIn profile URL
    encoded_url = urllib.parse.quote(profile_url)
    
    # Prepare API request
    headers = {
        "x-rapidapi-host": API_HOST,
        "x-rapidapi-key": API_KEY
    }
    
    try:
        # Make API request
        response = requests.get(f"{ENDPOINT}?url={encoded_url}", headers=headers)
        response.raise_for_status()  # Raise exception for HTTP errors
        data = response.json()
        
        # Extract ID from response
        profile_id = data.get('id') or data.get('profileId') or "unknownid"
        
        # Format the filename to match the pattern in rapid-api-responses
        filename = f"{username}_{profile_id}_{DATE}.json"
        
        # Save response to file
        output_path = os.path.join(OUTPUT_DIR, filename)
        with open(output_path, 'w') as output_file:
            json.dump(data, output_file, indent=2)
        
        print(f"Saved: {filename}")
        processed += 1
    
    except requests.exceptions.RequestException as e:
        print(f"Failed to fetch {profile_url}: {e}")
        failed += 1
        continue

# Print summary
print("\nFetch completed!")
print(f"Profiles processed: {processed}")
print(f"Profiles skipped (recently fetched): {skipped}")
print(f"Profiles failed: {failed}")
print(f"Total URLs: {total_urls}") 