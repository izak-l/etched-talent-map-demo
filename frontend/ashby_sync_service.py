"""
Ashby Sync Service
Handles the business logic for syncing candidates from Ashby to our database
"""

import psycopg2
from psycopg2.extras import RealDictCursor, Json
import logging
from typing import Dict, List, Optional, Tuple
from datetime import datetime
import hashlib
from ashby_client import AshbyClient, AshbyAPIError
from scraping_service import LinkedInScraper
import os
from cryptography.fernet import Fernet

logger = logging.getLogger(__name__)

class AshbySyncService:
    """Service for syncing Ashby candidates to our database"""
    
    def __init__(self):
        self.scraper = LinkedInScraper()
        
        # Initialize encryption for API keys
        self.encryption_key = os.getenv("ASHBY_ENCRYPTION_KEY")
        if not self.encryption_key:
            # Generate a key if not provided (should be set in production)
            self.encryption_key = Fernet.generate_key()
            logger.warning("No ASHBY_ENCRYPTION_KEY found, using generated key. Set this in production!")
        
        self.cipher = Fernet(self.encryption_key)
    
    def get_db_connection(self):
        """Create a new database connection"""
        import psycopg2
        from psycopg2.extras import RealDictCursor
        import os
        
        return psycopg2.connect(
            host=os.getenv("LOCAL_DB_HOST"),
            port=os.getenv("LOCAL_DB_PORT"), 
            database=os.getenv("LOCAL_DB_NAME"),
            user=os.getenv("LOCAL_DB_USER"),
            password=os.getenv("LOCAL_DB_PASSWORD"),
            cursor_factory=RealDictCursor
        )
    
    def encrypt_api_key(self, api_key: str) -> str:
        """Encrypt API key for storage"""
        return self.cipher.encrypt(api_key.encode()).decode()
    
    def decrypt_api_key(self, encrypted_key: str) -> str:
        """Decrypt API key for use"""
        return self.cipher.decrypt(encrypted_key.encode()).decode()
    
    def save_integration(self, api_key: str) -> int:
        """
        Save Ashby integration configuration
        Returns: integration_id
        """
        conn = self.get_db_connection()
        cur = conn.cursor()
        
        try:
            # Encrypt the API key
            encrypted_key = self.encrypt_api_key(api_key)
            
            # Deactivate any existing integrations
            cur.execute("UPDATE ashby_integrations SET is_active = false")
            
            # Create new integration
            cur.execute("""
                INSERT INTO ashby_integrations (api_key_encrypted, is_active)
                VALUES (%s, true)
                RETURNING id
            """, (encrypted_key,))
            
            integration_id = cur.fetchone()['id']
            conn.commit()
            
            logger.info(f"Created Ashby integration with ID: {integration_id}")
            return integration_id
            
        except Exception as e:
            conn.rollback()
            logger.error(f"Failed to save integration: {e}")
            raise
        finally:
            conn.close()
    
    def get_active_integration(self) -> Optional[Dict]:
        """Get the active Ashby integration"""
        conn = self.get_db_connection()
        try:
            cur = conn.cursor()
            cur.execute("""
                SELECT id, api_key_encrypted, sync_token, last_sync_at
                FROM ashby_integrations 
                WHERE is_active = true 
                ORDER BY created_at DESC 
                LIMIT 1
            """)
            
            integration = cur.fetchone()
            if integration:
                return dict(integration)
            return None
        finally:
            conn.close()
    
    def create_sync_job(self, integration_id: int, job_type: str) -> int:
        """
        Create a new sync job record
        Returns: job_id
        """
        conn = self.get_db_connection()
        try:
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO ashby_sync_jobs (integration_id, job_type)
                VALUES (%s, %s)
                RETURNING id
            """, (integration_id, job_type))
            
            job_id = cur.fetchone()['id']
            conn.commit()
            return job_id
        finally:
            conn.close()
    
    def update_sync_job(self, job_id: int, **kwargs):
        """Update sync job with provided fields"""
        if not kwargs:
            return
        
        conn = self.get_db_connection()
        try:
            cur = conn.cursor()
            
            # Build dynamic update query
            update_fields = []
            values = []
            
            for field, value in kwargs.items():
                if field in ['status', 'candidates_processed', 'candidates_created', 
                            'candidates_updated', 'candidates_skipped', 'error_message']:
                    update_fields.append(f"{field} = %s")
                    values.append(value)
            
            if 'status' in kwargs and kwargs['status'] in ['completed', 'failed']:
                update_fields.append("completed_at = CURRENT_TIMESTAMP")
            
            if update_fields:
                values.append(job_id)
                query = f"UPDATE ashby_sync_jobs SET {', '.join(update_fields)} WHERE id = %s"
                cur.execute(query, values)
                conn.commit()
        finally:
            conn.close()
    
    def find_existing_candidate(self, ashby_candidate: Dict) -> Optional[int]:
        """
        Find existing candidate by Ashby ID, email, or LinkedIn URL
        Returns: user_id if found, None otherwise
        """
        conn = self.get_db_connection()
        try:
            cur = conn.cursor()
            ashby_id = ashby_candidate.get("id")
            
            # 1. Check by Ashby ID in source metadata
            if ashby_id:
                cur.execute("""
                    SELECT DISTINCT p.user_id
                    FROM People p
                    JOIN PeopleSources ps ON p.user_id = ps.user_id
                    WHERE ps.source_metadata->>'ashby_candidate_id' = %s
                """, (ashby_id,))
                
                result = cur.fetchone()
                if result:
                    return result['user_id']
            
            # 2. Check by LinkedIn URL
            linkedin_url = None
            for link in ashby_candidate.get("socialLinks", []):
                if link.get("type") == "LinkedIn":
                    linkedin_url = link.get("url")
                    break
            
            if linkedin_url:
                normalized_url = self.scraper.normalize_linkedin_url(linkedin_url)
                cur.execute("""
                    SELECT user_id FROM People WHERE canonical_linkedin_url = %s
                """, (normalized_url,))
                
                result = cur.fetchone()
                if result:
                    return result['user_id']
            
            return None
        finally:
            conn.close()
    
    def create_or_get_ashby_source(self) -> int:
        """Get or create the Ashby source"""
        conn = self.get_db_connection()
        try:
            cur = conn.cursor()
            
            # Check if Ashby source exists
            cur.execute("SELECT id FROM Sources WHERE source_type = 'ashby' LIMIT 1")
            result = cur.fetchone()
            
            if result:
                return result['id']
            
            # Create Ashby source
            cur.execute("""
                INSERT INTO Sources (name, description, source_type, metadata_schema)
                VALUES (%s, %s, %s, %s)
                RETURNING id
            """, (
                "Ashby Candidates",
                "Candidates imported from Ashby ATS",
                "ashby",
                Json({
                    "sync_enabled": True,
                    "last_full_sync": None,
                    "candidate_count": 0
                })
            ))
            
            source_id = cur.fetchone()['id']
            conn.commit()
            
            logger.info(f"Created Ashby source with ID: {source_id}")
            return source_id
        finally:
            conn.close()
    
    def process_candidate(self, ashby_candidate: Dict, source_id: int) -> Tuple[str, int]:
        """
        Process a single candidate from Ashby
        Returns: (action_taken, user_id)
        action_taken: 'created', 'updated', 'skipped'
        """
        # Check if candidate already exists
        existing_user_id = self.find_existing_candidate(ashby_candidate)
        
        # Extract LinkedIn URL
        linkedin_url = None
        for link in ashby_candidate.get("socialLinks", []):
            if link.get("type") == "LinkedIn":
                linkedin_url = link.get("url")
                break
        
        # Normalize LinkedIn URL if present
        normalized_linkedin_url = None
        if linkedin_url:
            normalized_linkedin_url = self.scraper.normalize_linkedin_url(linkedin_url)
        
        # Prepare source metadata
        client = AshbyClient("")  # We don't need API key for formatting
        source_metadata = client.format_candidate_for_storage(ashby_candidate)
        
        conn = self.get_db_connection()
        try:
            cur = conn.cursor()
            
            if existing_user_id:
                # Update existing candidate
                try:
                    # Check if they're already in the Ashby source
                    cur.execute("""
                        SELECT id FROM PeopleSources 
                        WHERE user_id = %s AND source_id = %s
                    """, (existing_user_id, source_id))
                    
                    if cur.fetchone():
                        # Update existing entry in Ashby source
                        cur.execute("""
                            UPDATE PeopleSources 
                            SET source_metadata = %s, linkedin_url = %s
                            WHERE user_id = %s AND source_id = %s
                        """, (Json(source_metadata), linkedin_url or "", existing_user_id, source_id))
                    else:
                        # Add to Ashby source
                        cur.execute("""
                            INSERT INTO PeopleSources (user_id, source_id, linkedin_url, source_metadata)
                            VALUES (%s, %s, %s, %s)
                        """, (existing_user_id, source_id, linkedin_url or "", Json(source_metadata)))
                    
                    conn.commit()
                    return "updated", existing_user_id
                    
                except psycopg2.IntegrityError:
                    conn.rollback()
                    return "skipped", existing_user_id
            
            else:
                # Create new candidate
                try:
                    # Create in People table
                    cur.execute("""
                        INSERT INTO People (canonical_linkedin_url, scrape_status)
                        VALUES (%s, 'pending')
                        RETURNING user_id
                    """, (normalized_linkedin_url,))
                    
                    user_id = cur.fetchone()['user_id']
                    
                    # Add to Ashby source
                    cur.execute("""
                        INSERT INTO PeopleSources (user_id, source_id, linkedin_url, source_metadata)
                        VALUES (%s, %s, %s, %s)
                    """, (user_id, source_id, linkedin_url or "", Json(source_metadata)))
                    
                    conn.commit()
                    return "created", user_id
                    
                except Exception as e:
                    conn.rollback()
                    logger.error(f"Failed to create candidate: {e}")
                    return "skipped", 0
        finally:
            conn.close()
    
    def get_running_jobs(self) -> List[Dict]:
        """Get list of currently running sync jobs"""
        conn = self.get_db_connection()
        try:
            cur = conn.cursor()
            cur.execute("""
                SELECT id, job_type, started_at, candidates_processed
                FROM ashby_sync_jobs 
                WHERE status = 'running'
                ORDER BY started_at DESC
            """)
            return [dict(job) for job in cur.fetchall()]
        finally:
            conn.close()
    
    def mark_stale_jobs_as_failed(self, max_age_minutes: int = 30):
        """Mark stale running jobs as failed"""
        conn = self.get_db_connection()
        try:
            cur = conn.cursor()
            cur.execute("""
                UPDATE ashby_sync_jobs 
                SET status = 'failed', 
                    error_message = 'Job marked as failed due to timeout',
                    completed_at = CURRENT_TIMESTAMP
                WHERE status = 'running' 
                AND started_at < CURRENT_TIMESTAMP - INTERVAL '%s minutes'
            """, (max_age_minutes,))
            
            updated_count = cur.rowcount
            conn.commit()
            
            if updated_count > 0:
                logger.info(f"Marked {updated_count} stale sync jobs as failed")
            
            return updated_count
        finally:
            conn.close()
    
    def sync_candidates(self, api_key: str, is_incremental: bool = False) -> Dict:
        """
        Main method to sync candidates from Ashby
        Returns: Sync results dictionary
        """
        integration = self.get_active_integration()
        if not integration:
            raise ValueError("No active Ashby integration found")
        
        # Clean up any stale running jobs
        self.mark_stale_jobs_as_failed()
        
        # Decrypt API key
        decrypted_key = self.decrypt_api_key(integration['api_key_encrypted'])
        client = AshbyClient(decrypted_key)
        
        # Create sync job
        job_type = "incremental" if is_incremental else "initial"
        job_id = self.create_sync_job(integration['id'], job_type)
        
        # Get or create Ashby source
        source_id = self.create_or_get_ashby_source()
        
        # Initialize counters
        stats = {
            "candidates_processed": 0,
            "candidates_created": 0,
            "candidates_updated": 0,
            "candidates_skipped": 0
        }
        
        try:
            # Choose sync method
            if is_incremental and integration['sync_token']:
                sync_generator = client.sync_candidates_incremental(integration['sync_token'])
            else:
                sync_generator = client.sync_candidates_full()
            
            # Process candidates in batches
            final_sync_token = None
            for batch_or_token in sync_generator:
                if isinstance(batch_or_token, str):
                    # This is the final sync token
                    final_sync_token = batch_or_token
                    break
                
                # Process batch of candidates
                batch = batch_or_token
                for candidate in batch['candidates']:
                    action, user_id = self.process_candidate(candidate, source_id)
                    
                    stats["candidates_processed"] += 1
                    if action == "created":
                        stats["candidates_created"] += 1
                    elif action == "updated":
                        stats["candidates_updated"] += 1
                    else:
                        stats["candidates_skipped"] += 1
                
                # Update job progress
                self.update_sync_job(job_id, **stats)
            
            # Update integration with new sync token
            if final_sync_token:
                token_conn = self.get_db_connection()
                try:
                    cur = token_conn.cursor()
                    cur.execute("""
                        UPDATE ashby_integrations 
                        SET sync_token = %s, last_sync_at = CURRENT_TIMESTAMP
                        WHERE id = %s
                    """, (final_sync_token, integration['id']))
                    token_conn.commit()
                finally:
                    token_conn.close()
            
            # Mark job as completed
            self.update_sync_job(job_id, status="completed", **stats)
            
            logger.info(f"Sync completed successfully: {stats}")
            return {**stats, "job_id": job_id}
            
        except Exception as e:
            # Mark job as failed
            error_message = str(e)
            self.update_sync_job(job_id, status="failed", error_message=error_message, **stats)
            logger.error(f"Sync failed: {error_message}")
            raise 