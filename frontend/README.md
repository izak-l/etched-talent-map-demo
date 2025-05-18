# LinkedIn Profiles Dashboard

A Flask web application to view and explore LinkedIn profile data stored in a PostgreSQL database.

## Phase 1: Project Setup and Core Layout

- Basic Flask project structure
- Database connection utilities
- Main layout with Tailwind CSS
- Home page with database statistics
- Profiles listing page

## Setup Instructions

1. **Install Dependencies**

   ```bash
   pip install -r requirements.txt
   ```

2. **Configure Database Connection**

   Edit the database connection parameters in `.env` if needed:

   ```
   SUPABASE_DB_URL=<your_supabase_connection_url>
   FLASK_SECRET_KEY=<any_string>
   ```

3. **Run the Application**

   ```bash
   python app.py
   ```

   The application will be available at `http://127.0.0.1:5000/`

## Features

- **Home Page**: Displays database statistics including the number of profiles, positions, educations, and skills.
- **Profiles Page**: Lists all LinkedIn profiles with basic information and allows searching by name, headline, or location.