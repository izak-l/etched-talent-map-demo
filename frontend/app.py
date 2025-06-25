from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
import psycopg2
from psycopg2.extras import RealDictCursor, Json
from datetime import datetime
from dotenv import load_dotenv
import os
import json
from scraping_service import LinkedInScraper
from ashby_client import test_ashby_connection
from ashby_sync_service import AshbySyncService
import threading
import time

# config
load_dotenv()

# Create the Flask application
app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY")  # Used for session/flash messages (in future)

# Constants
DEFAULT_ITEMS_PER_PAGE = 24

# Initialize scraping service
scraper = LinkedInScraper()

# Global variable to track scraping jobs
scraping_jobs = {}

# Database utility functions
def get_db_connection():
    """Create a connection to the PostgreSQL database."""
    conn = psycopg2.connect(
        host=os.getenv("LOCAL_DB_HOST"),
        port=os.getenv("LOCAL_DB_PORT"),
        database=os.getenv("LOCAL_DB_NAME"),
        user=os.getenv("LOCAL_DB_USER"),
        password=os.getenv("LOCAL_DB_PASSWORD"),
        cursor_factory=RealDictCursor  # Returns results as dictionaries
    )
    # conn = psycopg2.connect(
    #     os.getenv("SUPABASE_DB_URL"),
    #     cursor_factory=RealDictCursor  # Returns results as dictionaries
    # )
    return conn

def close_db_connection(conn):
    """Close the database connection."""
    if conn:
        conn.close()

@app.route('/get_schools')
def get_schools():
    """Get a list of distinct school names for filters."""
    conn = get_db_connection()
    cur = conn.cursor()
    
    cur.execute("""
        SELECT DISTINCT schoolName 
        FROM Educations 
        WHERE schoolName IS NOT NULL 
        ORDER BY schoolName
    """)
    
    schools = [row['schoolname'] for row in cur.fetchall()]
    close_db_connection(conn)
    
    return jsonify(schools)

@app.route('/get_workplaces')
def get_workplaces():
    """Get a list of distinct company names for filters."""
    conn = get_db_connection()
    cur = conn.cursor()
    
    cur.execute("""
        SELECT DISTINCT companyName 
        FROM Positions 
        WHERE companyName IS NOT NULL 
        ORDER BY companyName
    """)
    
    workplaces = [row['companyname'] for row in cur.fetchall()]
    close_db_connection(conn)
    
    return jsonify(workplaces)

# Sources API Routes
@app.route('/api/sources', methods=['GET'])
def get_sources():
    """Get all sources with their candidate counts."""
    conn = get_db_connection()
    cur = conn.cursor()
    
    cur.execute("""
        SELECT 
            s.*,
            CASE 
                WHEN s.name = 'Master' THEN (
                    SELECT COUNT(DISTINCT p.user_id) 
                    FROM People p 
                    WHERE p.user_id IS NOT NULL
                )
                ELSE COUNT(ps.user_id)
            END as candidate_count,
            CASE 
                WHEN s.name = 'Master' THEN (
                    SELECT COUNT(DISTINCT p.user_id) 
                    FROM People p 
                    WHERE p.scrape_status = 'scraped'
                )
                ELSE COUNT(CASE WHEN p.scrape_status = 'scraped' THEN 1 END)
            END as scraped_count
        FROM Sources s
        LEFT JOIN PeopleSources ps ON s.id = ps.source_id
        LEFT JOIN People p ON ps.user_id = p.user_id
        GROUP BY s.id, s.name, s.description, s.source_type, s.metadata_schema, s.created_at, s.updated_at
        ORDER BY s.created_at DESC
    """)
    
    sources = cur.fetchall()
    close_db_connection(conn)
    
    return jsonify([dict(source) for source in sources])

@app.route('/api/sources', methods=['POST'])
def create_source():
    """Create a new source."""
    data = request.get_json()
    
    if not data or not data.get('name'):
        return jsonify({'error': 'Source name is required'}), 400
    
    conn = get_db_connection()
    cur = conn.cursor()
    
    try:
        cur.execute("""
            INSERT INTO Sources (name, description, source_type, metadata_schema)
            VALUES (%s, %s, %s, %s)
            RETURNING *
        """, (
            data['name'],
            data.get('description', ''),
            data.get('source_type', 'manual'),
            Json(data.get('metadata_schema', {}))
        ))
        
        new_source = cur.fetchone()
        conn.commit()
        close_db_connection(conn)
        
        return jsonify(dict(new_source)), 201
        
    except psycopg2.IntegrityError:
        conn.rollback()
        close_db_connection(conn)
        return jsonify({'error': 'Source name already exists'}), 409
    except Exception as e:
        conn.rollback()
        close_db_connection(conn)
        return jsonify({'error': str(e)}), 500

@app.route('/api/sources/<int:source_id>', methods=['PUT'])
def update_source(source_id):
    """Update a source's information."""
    data = request.get_json()
    
    if not data:
        return jsonify({'error': 'Request data is required'}), 400
    
    conn = get_db_connection()
    cur = conn.cursor()
    
    try:
        # Check if source exists
        cur.execute("SELECT * FROM Sources WHERE id = %s", (source_id,))
        existing_source = cur.fetchone()
        if not existing_source:
            close_db_connection(conn)
            return jsonify({'error': 'Source not found'}), 404
        
        # Prevent editing Master source
        if existing_source['name'] == 'Master':
            close_db_connection(conn)
            return jsonify({'error': 'Cannot edit the Master source'}), 400
        
        # Build update query dynamically based on provided fields
        update_fields = []
        params = []
        
        if 'name' in data:
            update_fields.append("name = %s")
            params.append(data['name'])
        
        if 'description' in data:
            update_fields.append("description = %s")
            params.append(data['description'])
        
        if 'source_type' in data:
            update_fields.append("source_type = %s")
            params.append(data['source_type'])
        
        if not update_fields:
            close_db_connection(conn)
            return jsonify({'error': 'No fields to update'}), 400
        
        # Add updated_at timestamp
        update_fields.append("updated_at = CURRENT_TIMESTAMP")
        params.append(source_id)
        
        # Execute update
        query = f"UPDATE Sources SET {', '.join(update_fields)} WHERE id = %s RETURNING *"
        cur.execute(query, params)
        
        updated_source = cur.fetchone()
        conn.commit()
        close_db_connection(conn)
        
        return jsonify(dict(updated_source)), 200
        
    except psycopg2.IntegrityError:
        conn.rollback()
        close_db_connection(conn)
        return jsonify({'error': 'Source name already exists'}), 409
    except Exception as e:
        conn.rollback()
        close_db_connection(conn)
        return jsonify({'error': str(e)}), 500

@app.route('/api/sources/<int:source_id>', methods=['DELETE'])
def delete_source(source_id):
    """Delete a source and all its associations."""
    conn = get_db_connection()
    cur = conn.cursor()
    
    try:
        # Check if source exists
        cur.execute("SELECT id FROM Sources WHERE id = %s", (source_id,))
        if not cur.fetchone():
            close_db_connection(conn)
            return jsonify({'error': 'Source not found'}), 404
        
        # Delete the source (CASCADE will handle PeopleSources)
        cur.execute("DELETE FROM Sources WHERE id = %s", (source_id,))
        conn.commit()
        close_db_connection(conn)
        
        return jsonify({'message': 'Source deleted successfully'}), 200
        
    except Exception as e:
        conn.rollback()
        close_db_connection(conn)
        return jsonify({'error': str(e)}), 500

# Scraping API Routes
@app.route('/api/sources/<int:source_id>/scrape', methods=['POST'])
def scrape_source_profiles(source_id):
    """Trigger scraping for all pending profiles in a source."""
    data = request.get_json()
    force_rescrape = data.get('force', False) if data else False
    
    conn = get_db_connection()
    cur = conn.cursor()
    
    try:
        # Get all LinkedIn URLs for this source that need scraping
        if force_rescrape:
            # Scrape all profiles regardless of status
            cur.execute("""
                SELECT ps.linkedin_url
                FROM PeopleSources ps
                WHERE ps.source_id = %s AND ps.linkedin_url != ''
            """, (source_id,))
        else:
            # Only scrape profiles that haven't been scraped or failed
            cur.execute("""
                SELECT ps.linkedin_url
                FROM PeopleSources ps
                LEFT JOIN People p ON ps.user_id = p.user_id
                WHERE ps.source_id = %s 
                AND ps.linkedin_url != ''
                AND (p.scrape_status IS NULL OR p.scrape_status = 'pending' OR p.scrape_status = 'failed')
            """, (source_id,))
        
        urls_to_scrape = [row['linkedin_url'] for row in cur.fetchall()]
        
        if not urls_to_scrape:
            close_db_connection(conn)
            return jsonify({'message': 'No profiles need scraping'}), 200
        
        # Create a unique job ID
        job_id = f"source_{source_id}_{int(time.time())}"
        
        # Initialize job status
        scraping_jobs[job_id] = {
            'status': 'starting',
            'total': len(urls_to_scrape),
            'processed': 0,
            'successful': 0,
            'failed': 0,
            'current_url': None,
            'started_at': datetime.now().isoformat()
        }
        
        # Start scraping in background thread
        def scrape_profiles():
            try:
                scraping_jobs[job_id]['status'] = 'running'
                
                for i, url in enumerate(urls_to_scrape):
                    scraping_jobs[job_id]['current_url'] = url
                    scraping_jobs[job_id]['processed'] = i
                    
                    result = scraper.scrape_single_profile(url, source_id, force_rescrape)
                    
                    if result['success']:
                        scraping_jobs[job_id]['successful'] += 1
                    else:
                        scraping_jobs[job_id]['failed'] += 1
                    
                    # Add delay between requests
                    if i < len(urls_to_scrape) - 1:
                        time.sleep(5)
                
                scraping_jobs[job_id]['status'] = 'completed'
                scraping_jobs[job_id]['processed'] = len(urls_to_scrape)
                scraping_jobs[job_id]['current_url'] = None
                scraping_jobs[job_id]['completed_at'] = datetime.now().isoformat()
                
            except Exception as e:
                scraping_jobs[job_id]['status'] = 'failed'
                scraping_jobs[job_id]['error'] = str(e)
        
        thread = threading.Thread(target=scrape_profiles)
        thread.daemon = True
        thread.start()
        
        close_db_connection(conn)
        return jsonify({
            'job_id': job_id,
            'message': f'Started scraping {len(urls_to_scrape)} profiles',
            'total_profiles': len(urls_to_scrape)
        }), 202
        
    except Exception as e:
        close_db_connection(conn)
        return jsonify({'error': str(e)}), 500

@app.route('/api/scrape/job/<job_id>', methods=['GET'])
def get_scraping_job_status(job_id):
    """Get the status of a scraping job."""
    if job_id not in scraping_jobs:
        return jsonify({'error': 'Job not found'}), 404
    
    return jsonify(scraping_jobs[job_id])

@app.route('/api/scrape/single', methods=['POST'])
def scrape_single_profile():
    """Scrape a single LinkedIn profile."""
    data = request.get_json()
    
    if not data or not data.get('linkedin_url'):
        return jsonify({'error': 'LinkedIn URL is required'}), 400
    
    linkedin_url = data['linkedin_url']
    source_id = data.get('source_id')
    force = data.get('force', False)
    
    try:
        result = scraper.scrape_single_profile(linkedin_url, source_id, force)
        
        if result['success']:
            return jsonify({
                'success': True,
                'message': f"Successfully scraped profile for {result.get('name', 'Unknown')}",
                'user_id': result.get('user_id'),
                'profile_id': result.get('profile_id')
            }), 200
        else:
            status_code = 409 if result.get('skipped') else 400
            return jsonify({
                'success': False,
                'message': result.get('message', 'Failed to scrape profile')
            }), status_code
            
    except Exception as e:
        return jsonify({
            'success': False,
            'message': f'Error: {str(e)}'
        }), 500

@app.route('/api/sources/<int:source_id>/scrape-selected', methods=['POST'])
def scrape_selected_profiles(source_id):
    """Scrape only selected profiles in a source."""
    data = request.get_json()
    
    if not data or not data.get('candidate_ids'):
        return jsonify({'error': 'No candidates selected'}), 400
    
    candidate_ids = data['candidate_ids']
    force_rescrape = data.get('force', False)
    
    conn = get_db_connection()
    cur = conn.cursor()
    
    try:
        # Get LinkedIn URLs for the selected candidates
        placeholders = ','.join(['%s'] * len(candidate_ids))
        cur.execute(f"""
            SELECT ps.linkedin_url, ps.id as people_source_id
            FROM PeopleSources ps
            WHERE ps.source_id = %s 
            AND ps.id IN ({placeholders})
            AND ps.linkedin_url != ''
        """, [source_id] + candidate_ids)
        
        selected_profiles = cur.fetchall()
        
        if not selected_profiles:
            close_db_connection(conn)
            return jsonify({'message': 'No valid LinkedIn URLs found for selected candidates'}), 200
        
        urls_to_scrape = [profile['linkedin_url'] for profile in selected_profiles]
        
        # Create a unique job ID
        job_id = f"selected_{source_id}_{int(time.time())}"
        
        # Initialize job status
        scraping_jobs[job_id] = {
            'status': 'starting',
            'total': len(urls_to_scrape),
            'processed': 0,
            'successful': 0,
            'failed': 0,
            'current_url': None,
            'started_at': datetime.now().isoformat()
        }
        
        # Start scraping in background thread
        def scrape_selected():
            try:
                scraping_jobs[job_id]['status'] = 'running'
                
                for i, url in enumerate(urls_to_scrape):
                    scraping_jobs[job_id]['current_url'] = url
                    scraping_jobs[job_id]['processed'] = i
                    
                    result = scraper.scrape_single_profile(url, source_id, force_rescrape)
                    
                    if result['success']:
                        scraping_jobs[job_id]['successful'] += 1
                    else:
                        scraping_jobs[job_id]['failed'] += 1
                    
                    # Add delay between requests
                    if i < len(urls_to_scrape) - 1:
                        time.sleep(5)
                
                scraping_jobs[job_id]['status'] = 'completed'
                scraping_jobs[job_id]['processed'] = len(urls_to_scrape)
                scraping_jobs[job_id]['current_url'] = None
                scraping_jobs[job_id]['completed_at'] = datetime.now().isoformat()
                
            except Exception as e:
                scraping_jobs[job_id]['status'] = 'failed'
                scraping_jobs[job_id]['error'] = str(e)
        
        thread = threading.Thread(target=scrape_selected)
        thread.daemon = True
        thread.start()
        
        close_db_connection(conn)
        return jsonify({
            'job_id': job_id,
            'message': f'Started scraping {len(urls_to_scrape)} selected profiles',
            'total_profiles': len(urls_to_scrape)
        }), 202
        
    except Exception as e:
        close_db_connection(conn)
        return jsonify({'error': str(e)}), 500

# Source Detail Routes
@app.route('/source/<int:source_id>')
def source_detail(source_id):
    """Display the spreadsheet view for a specific source."""
    conn = get_db_connection()
    cur = conn.cursor()
    
    # Get source info
    cur.execute("SELECT * FROM Sources WHERE id = %s", (source_id,))
    source = cur.fetchone()
    
    if not source:
        flash("Source not found")
        return redirect(url_for('dashboard'))
    
    # Check if this is the Master source (special case)
    if source['name'] == 'Master':
        # For Master source, show ALL people from ALL sources
        cur.execute("""
            SELECT DISTINCT
                COALESCE(MIN(ps.id), -1 * p.user_id) as people_source_id,  -- Use negative user_id for orphaned people
                COALESCE(p.canonical_linkedin_url, '') as linkedin_url,
                CASE 
                    WHEN COUNT(ps.id) > 0 THEN
                        JSONB_BUILD_OBJECT(
                            'sources', ARRAY_AGG(DISTINCT s.name ORDER BY s.name) FILTER (WHERE s.name IS NOT NULL),
                            'source_ids', ARRAY_AGG(DISTINCT ps.source_id ORDER BY ps.source_id) FILTER (WHERE ps.source_id IS NOT NULL),
                            'is_master_view', true
                        )
                    ELSE
                        JSONB_BUILD_OBJECT(
                            'sources', ARRAY[]::text[],
                            'source_ids', ARRAY[]::integer[],
                            'is_master_view', true,
                            'orphaned', true
                        )
                END as source_metadata,
                MIN(ps.added_at) as added_at,  -- Will be NULL for orphaned people
                p.user_id,
                p.last_scraped_at,
                p.scrape_status,
                li.firstname,
                li.lastname,
                li.headline
            FROM People p
            LEFT JOIN PeopleSources ps ON p.user_id = ps.user_id
            LEFT JOIN Sources s ON ps.source_id = s.id AND s.name != 'Master'
            LEFT JOIN LinkedinInfo li ON p.user_id = li.user_id
            WHERE p.user_id IS NOT NULL
            GROUP BY p.user_id, p.canonical_linkedin_url, p.last_scraped_at, p.scrape_status, 
                     li.firstname, li.lastname, li.headline
            ORDER BY MIN(ps.added_at) DESC NULLS LAST
        """)
    else:
        # For regular sources, show only people in that specific source
        cur.execute("""
            SELECT 
                ps.id as people_source_id,
                ps.linkedin_url,
                ps.source_metadata,
                ps.added_at,
                p.user_id,
                p.last_scraped_at,
                p.scrape_status,
                li.firstname,
                li.lastname,
                li.headline
            FROM PeopleSources ps
            LEFT JOIN People p ON ps.user_id = p.user_id
            LEFT JOIN LinkedinInfo li ON p.user_id = li.user_id
            WHERE ps.source_id = %s
            ORDER BY ps.added_at DESC
        """, (source_id,))
    
    candidates = cur.fetchall()
    close_db_connection(conn)
    
    current_year = datetime.now().year
    
    return render_template(
        'source_detail.html',
        source=source,
        candidates=candidates,
        year=current_year
    )

@app.route('/api/sources/<int:source_id>/candidates', methods=['POST'])
def add_candidate_to_source(source_id):
    """Add a new candidate to a source."""
    data = request.get_json()
    
    if not data or not data.get('linkedin_url'):
        return jsonify({'error': 'LinkedIn URL is required'}), 400
    
    # Don't allow adding candidates directly to Master source
    conn = get_db_connection()
    cur = conn.cursor()
    
    # Check if this is the Master source
    cur.execute("SELECT name FROM Sources WHERE id = %s", (source_id,))
    source = cur.fetchone()
    if source and source['name'] == 'Master':
        close_db_connection(conn)
        return jsonify({'error': 'Cannot add candidates directly to Master source. Add them to a specific source instead.'}), 400
    
    try:
        linkedin_url = data['linkedin_url'].strip()
        
        # Normalize the LinkedIn URL before storing
        normalized_url = scraper.normalize_linkedin_url(linkedin_url)
        
        # Check if person already exists by LinkedIn URL
        cur.execute("""
            SELECT user_id FROM People WHERE canonical_linkedin_url = %s
        """, (normalized_url,))
        
        existing_person = cur.fetchone()
        
        if existing_person:
            user_id = existing_person['user_id']
        else:
            # Create new person
            cur.execute("""
                INSERT INTO People (canonical_linkedin_url, scrape_status)
                VALUES (%s, 'pending')
                RETURNING user_id
            """, (normalized_url,))
            user_id = cur.fetchone()['user_id']
        
        # Add to the specified source (if not already there)
        try:
            cur.execute("""
                INSERT INTO PeopleSources (user_id, source_id, linkedin_url, source_metadata)
                VALUES (%s, %s, %s, %s)
                RETURNING id
            """, (
                user_id,
                source_id,
                normalized_url,
                Json(data.get('source_metadata', {}))
            ))
            
            people_source_id = cur.fetchone()['id']
            conn.commit()
            
            return jsonify({
                'id': people_source_id,
                'user_id': user_id,
                'message': 'Candidate added successfully'
            }), 201
            
        except psycopg2.IntegrityError:
            # Person already in this source
            conn.rollback()
            return jsonify({'error': 'Candidate already exists in this source'}), 409
            
    except Exception as e:
        conn.rollback()
        close_db_connection(conn)
        return jsonify({'error': str(e)}), 500
    
    close_db_connection(conn)

# Dashboard Route (Updated)
@app.route('/')
def dashboard():
    """Main dashboard showing all sources."""
    conn = get_db_connection()
    cur = conn.cursor()
    
    # Get all sources with stats (special handling for Master)
    cur.execute("""
        SELECT 
            s.*,
            CASE 
                WHEN s.name = 'Master' THEN (
                    SELECT COUNT(DISTINCT p.user_id) 
                    FROM People p 
                    WHERE p.user_id IS NOT NULL
                )
                ELSE COUNT(ps.user_id)
            END as candidate_count,
            CASE 
                WHEN s.name = 'Master' THEN (
                    SELECT COUNT(DISTINCT p.user_id) 
                    FROM People p 
                    WHERE p.scrape_status = 'scraped'
                )
                ELSE COUNT(CASE WHEN p.scrape_status = 'scraped' THEN 1 END)
            END as scraped_count,
            CASE 
                WHEN s.name = 'Master' THEN (
                    SELECT COUNT(DISTINCT p.user_id) 
                    FROM People p 
                    WHERE p.scrape_status = 'pending'
                )
                ELSE COUNT(CASE WHEN p.scrape_status = 'pending' THEN 1 END)
            END as pending_count
        FROM Sources s
        LEFT JOIN PeopleSources ps ON s.id = ps.source_id
        LEFT JOIN People p ON ps.user_id = p.user_id
        GROUP BY s.id, s.name, s.description, s.source_type, s.metadata_schema, s.created_at, s.updated_at
        ORDER BY s.created_at DESC
    """)
    
    sources = cur.fetchall()
    
    # Get overall stats
    cur.execute("""
        SELECT 
            COUNT(DISTINCT p.user_id) as total_people,
            COUNT(DISTINCT s.id) as total_sources,
            COUNT(CASE WHEN p.scrape_status = 'scraped' THEN 1 END) as total_scraped,
            COUNT(CASE WHEN p.scrape_status = 'pending' THEN 1 END) as total_pending
        FROM People p
        LEFT JOIN PeopleSources ps ON p.user_id = ps.user_id
        LEFT JOIN Sources s ON ps.source_id = s.id
    """)
    
    stats = cur.fetchone()
    close_db_connection(conn)
    
    current_year = datetime.now().year
    
    return render_template(
        'dashboard.html',
        sources=sources,
        stats=stats,
        year=current_year
    )

# Keep existing routes for backward compatibility
@app.route('/legacy')
def index():
    """Legacy home page route."""
    # Get query parameters for filtering
    search_term = request.args.get('search', '')
    school_filter = request.args.get('school', '')
    workplace_filter = request.args.get('workplace', '')
    page = request.args.get('page', 1, type=int)
    items_per_page = request.args.get('items_per_page', DEFAULT_ITEMS_PER_PAGE, type=int)
    offset = (page - 1) * items_per_page
    
    conn = get_db_connection()
    cur = conn.cursor()
    
    # Get basic stats about the database
    stats = {}
    
    # Count profiles
    cur.execute("SELECT COUNT(*) as profile_count FROM LinkedinInfo")
    stats['profile_count'] = cur.fetchone()['profile_count']
    
    # Count positions
    cur.execute("SELECT COUNT(*) as position_count FROM Positions")
    stats['position_count'] = cur.fetchone()['position_count']
    
    # Count educations
    cur.execute("SELECT COUNT(*) as education_count FROM Educations")
    stats['education_count'] = cur.fetchone()['education_count']
    
    # Count skills
    cur.execute("SELECT COUNT(*) as skill_count FROM Skills")
    stats['skill_count'] = cur.fetchone()['skill_count']
    
    # Build base query for candidates
    query = """
        SELECT 
            p.user_id, 
            li.id as linkedin_id,
            li.firstname, 
            li.lastname,
            li.headline,
            g.country,
            g.city,
            (SELECT pos.companyname 
             FROM Positions pos 
             WHERE pos.user_id = p.user_id 
             ORDER BY pos.enddate DESC NULLS FIRST, pos.startdate DESC 
             LIMIT 1) as latest_companyname,
            (SELECT edu.schoolname 
             FROM Educations edu 
             WHERE edu.user_id = p.user_id 
             ORDER BY edu.enddate DESC NULLS FIRST, edu.startdate DESC 
             LIMIT 1) as latest_schoolname,
            (SELECT array_agg(s.name) 
             FROM (SELECT s1.name FROM Skills s1 
                   WHERE s1.user_id = p.user_id 
                   LIMIT 3) s) as skill_tags,
            (SELECT array_agg(h.title) 
             FROM (SELECT h1.title FROM Honors h1 
                   WHERE h1.user_id = p.user_id 
                   LIMIT 2) h) as award_titles
        FROM People p
        JOIN LinkedinInfo li ON p.user_id = li.user_id
        LEFT JOIN Geo g ON p.user_id = g.user_id
    """
    
    # Add WHERE clauses based on filters
    where_clauses = []
    query_params = []
    
    if search_term:
        where_clauses.append("(li.firstname ILIKE %s OR li.lastname ILIKE %s OR li.headline ILIKE %s)")
        search_pattern = f"%{search_term}%"
        query_params.extend([search_pattern, search_pattern, search_pattern])
    
    if school_filter:
        where_clauses.append("""
            EXISTS (SELECT 1 FROM Educations edu 
                   WHERE edu.user_id = p.user_id AND edu.schoolname = %s)
        """)
        query_params.append(school_filter)
    
    if workplace_filter:
        where_clauses.append("""
            EXISTS (SELECT 1 FROM Positions pos 
                   WHERE pos.user_id = p.user_id AND pos.companyname = %s)
        """)
        query_params.append(workplace_filter)
    
    if where_clauses:
        query += " WHERE " + " AND ".join(where_clauses)
    
    # Add ORDER BY, LIMIT, OFFSET for pagination
    query += " ORDER BY li.lastname, li.firstname LIMIT %s OFFSET %s"
    query_params.extend([items_per_page, offset])
    
    # Execute query
    cur.execute(query, query_params)
    candidates = cur.fetchall()
    
    # Get total count for pagination
    count_query = """
        SELECT COUNT(*) as total_count
        FROM People p
        JOIN LinkedinInfo li ON p.user_id = li.user_id
    """
    
    if where_clauses:
        count_query += " WHERE " + " AND ".join(where_clauses)
    
    cur.execute(count_query, query_params[:-2] if query_params else [])  # Remove LIMIT and OFFSET params
    total_count = cur.fetchone()['total_count']
    total_pages = (total_count + items_per_page - 1) // items_per_page  # Ceiling division
    
    # Get distinct schools and workplaces for filters
    cur.execute("""
        SELECT DISTINCT schoolname 
        FROM Educations 
        WHERE schoolname IS NOT NULL 
        ORDER BY schoolname
    """)
    schools = [row['schoolname'] for row in cur.fetchall()]
    
    cur.execute("""
        SELECT DISTINCT companyname 
        FROM Positions 
        WHERE companyname IS NOT NULL 
        ORDER BY companyname
    """)
    workplaces = [row['companyname'] for row in cur.fetchall()]
    
    close_db_connection(conn)
    
    # Pass the current year to the template for the footer
    current_year = datetime.now().year
    
    return render_template(
        'index.html',
        stats=stats,
        candidates=candidates,
        schools=schools,
        workplaces=workplaces,
        year=current_year,
        search_term=search_term,
        school_filter=school_filter,
        workplace_filter=workplace_filter,
        page=page,
        items_per_page=items_per_page,
        total_pages=total_pages,
        total_count=total_count
    )

@app.route('/profiles')
def profiles():
    """List all profiles."""
    conn = get_db_connection()
    cur = conn.cursor()
    
    # Get all profiles with basic info
    cur.execute("""
        SELECT 
            li.id, 
            li.firstname, 
            li.lastname, 
            li.headline,
            g.country,
            g.city
        FROM LinkedinInfo li
        LEFT JOIN Geo g ON li.user_id = g.user_id
        ORDER BY li.lastname, li.firstname
    """)
    profiles = cur.fetchall()
    
    close_db_connection(conn)
    
    # Pass the current year to the template for the footer
    current_year = datetime.now().year
    
    return render_template('profiles.html', profiles=profiles, year=current_year)

@app.route('/profile/<int:profile_id>')
def profile_detail(profile_id):
    """Display details for a specific profile."""
    conn = get_db_connection()
    cur = conn.cursor()
    
    # Get basic profile info
    cur.execute("""
        SELECT 
            li.id, 
            li.user_id,
            li.firstname, 
            li.lastname, 
            li.headline,
            g.country,
            g.city
        FROM LinkedinInfo li
        LEFT JOIN Geo g ON li.user_id = g.user_id
        WHERE li.id = %s
    """, (profile_id,))
    profile = cur.fetchone()
    
    if not profile:
        close_db_connection(conn)
        flash("Profile not found")
        return redirect(url_for('profiles'))
    
    # Get positions
    cur.execute("""
        SELECT * FROM Positions 
        WHERE user_id = %s
        ORDER BY startDate DESC
    """, (profile['user_id'],))
    positions = cur.fetchall()
    
    # Get educations
    cur.execute("""
        SELECT * FROM Educations 
        WHERE user_id = %s
        ORDER BY startDate DESC
    """, (profile['user_id'],))
    educations = cur.fetchall()
    
    # Get skills
    cur.execute("""
        SELECT * FROM Skills 
        WHERE user_id = %s
    """, (profile['user_id'],))
    skills = cur.fetchall()
    
    close_db_connection(conn)
    
    # Pass the current year to the template for the footer
    current_year = datetime.now().year
    
    return render_template(
        'profile_detail.html', 
        profile=profile, 
        positions=positions, 
        educations=educations, 
        skills=skills, 
        year=current_year
    )

@app.route('/candidate/<int:user_id>')
def candidate_detail(user_id):
    """Display comprehensive details for a candidate."""
    conn = get_db_connection()
    cur = conn.cursor()
    
    # Get main info
    cur.execute("""
        SELECT p.user_id, p.linkedin_details, li.id as linkedin_id, li.firstName, li.lastName, li.headline, 
               g.country, g.city, g.countryCode
        FROM People p
        JOIN LinkedinInfo li ON p.user_id = li.user_id
        LEFT JOIN Geo g ON p.user_id = g.user_id
        WHERE p.user_id = %s
    """, (user_id,))
    
    candidate = cur.fetchone()
    
    if not candidate:
        close_db_connection(conn)
        flash("Candidate not found")
        return redirect(url_for('index'))
    
    # Get educations
    cur.execute("""
        SELECT schoolName, schoolId, fieldOfStudy, degree, startDate, endDate, description, activities
        FROM Educations
        WHERE user_id = %s 
        ORDER BY endDate DESC NULLS FIRST, startDate DESC
    """, (user_id,))
    educations = cur.fetchall()
    
    # Get positions
    cur.execute("""
        SELECT companyId, companyName, title, location, description, employmentType, startDate, endDate
        FROM Positions
        WHERE user_id = %s 
        ORDER BY endDate DESC NULLS FIRST, startDate DESC
    """, (user_id,))
    positions = cur.fetchall()
    
    # Get skills
    cur.execute("""
        SELECT name FROM Skills WHERE user_id = %s ORDER BY name
    """, (user_id,))
    skills = cur.fetchall()
    
    # Get honors
    cur.execute("""
        SELECT title FROM Honors WHERE user_id = %s ORDER BY title
    """, (user_id,))
    honors = cur.fetchall()
    
    close_db_connection(conn)
    
    # Pass the current year to the template for the footer
    current_year = datetime.now().year
    
    return render_template(
        'candidate_detail.html',
        candidate=candidate,
        educations=educations,
        positions=positions,
        skills=skills,
        honors=honors,
        year=current_year
    )

# Ashby Integration API Routes
@app.route('/api/ashby/validate', methods=['POST'])
def validate_ashby_api_key():
    """Validate Ashby API key"""
    data = request.get_json()
    
    if not data or not data.get('api_key'):
        return jsonify({'error': 'API key is required'}), 400
    
    api_key = data['api_key'].strip()
    success, message = test_ashby_connection(api_key)
    
    return jsonify({
        'valid': success,
        'message': message
    })

@app.route('/api/ashby/connect', methods=['POST'])
def connect_ashby():
    """Save Ashby integration and start initial sync"""
    data = request.get_json()
    
    if not data or not data.get('api_key'):
        return jsonify({'error': 'API key is required'}), 400
    
    api_key = data['api_key'].strip()
    
    # Validate API key first
    success, message = test_ashby_connection(api_key)
    if not success:
        return jsonify({'error': f'Invalid API key: {message}'}), 400
    
    try:
        sync_service = AshbySyncService()
        integration_id = sync_service.save_integration(api_key)
        
        # Start initial sync in background
        def run_initial_sync():
            try:
                sync_service_bg = AshbySyncService()
                stats = sync_service_bg.sync_candidates(api_key, is_incremental=False)
                app.logger.info(f"Initial Ashby sync completed: {stats}")
            except Exception as e:
                app.logger.error(f"Initial Ashby sync failed: {e}")
        
        sync_thread = threading.Thread(target=run_initial_sync)
        sync_thread.daemon = True
        sync_thread.start()
        
        return jsonify({
            'success': True,
            'integration_id': integration_id,
            'message': 'Ashby integration connected successfully. Initial sync started in background.'
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/ashby/sync', methods=['POST'])
def trigger_ashby_sync():
    """Trigger an Ashby sync (incremental by default)"""
    data = request.get_json()
    force_full = data.get('force_full', False) if data else False
    
    try:
        sync_service = AshbySyncService()
        integration = sync_service.get_active_integration()
        
        if not integration:
            return jsonify({'error': 'No active Ashby integration found'}), 404
        
        # Decrypt API key for sync
        decrypted_key = sync_service.decrypt_api_key(integration['api_key_encrypted'])
        
        # Start sync in background
        def run_sync():
            try:
                sync_service_bg = AshbySyncService()
                is_incremental = not force_full and integration.get('sync_token') is not None
                stats = sync_service_bg.sync_candidates(decrypted_key, is_incremental=is_incremental)
                app.logger.info(f"Ashby sync completed: {stats}")
            except Exception as e:
                app.logger.error(f"Ashby sync failed: {e}")
        
        sync_thread = threading.Thread(target=run_sync)
        sync_thread.daemon = True
        sync_thread.start()
        
        sync_type = "incremental" if not force_full and integration.get('sync_token') else "full"
        
        return jsonify({
            'success': True,
            'message': f'Ashby {sync_type} sync started in background.'
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/ashby/status', methods=['GET'])
def ashby_integration_status():
    """Get Ashby integration status"""
    try:
        sync_service = AshbySyncService()
        integration = sync_service.get_active_integration()
        
        if not integration:
            return jsonify({
                'connected': False,
                'message': 'No Ashby integration configured'
            })
        
        # Get recent sync jobs
        conn = get_db_connection()
        try:
            cur = conn.cursor()
            cur.execute("""
                SELECT job_type, status, candidates_processed, candidates_created,
                       candidates_updated, candidates_skipped, started_at, completed_at,
                       error_message
                FROM ashby_sync_jobs 
                WHERE integration_id = %s 
                ORDER BY started_at DESC 
                LIMIT 5
            """, (integration['id'],))
            
            recent_jobs = [dict(job) for job in cur.fetchall()]
        finally:
            close_db_connection(conn)
        
        return jsonify({
            'connected': True,
            'last_sync': integration['last_sync_at'].isoformat() if integration['last_sync_at'] else None,
            'has_sync_token': integration['sync_token'] is not None,
            'recent_jobs': recent_jobs
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# Run the application
if __name__ == '__main__':
    app.run(debug=True) 