from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime
from dotenv import load_dotenv
import os

# config
load_dotenv()

# Create the Flask application
app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY")  # Used for session/flash messages (in future)

# Constants
DEFAULT_ITEMS_PER_PAGE = 24

# Database utility functions
def get_db_connection():
    """Create a connection to the PostgreSQL database."""
    conn = psycopg2.connect(
        os.getenv("SUPABASE_DB_URL"),
        cursor_factory=RealDictCursor  # Returns results as dictionaries
    )
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

# Routes
@app.route('/')
def index():
    """Home page route."""
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

# Run the application
if __name__ == '__main__':
    app.run(debug=True) 