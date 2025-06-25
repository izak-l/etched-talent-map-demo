"""
Ashby API Client for syncing candidates
Handles pagination, incremental sync, and error handling
"""

import requests
import json
import logging
from typing import Dict, List, Optional, Tuple, Generator
from datetime import datetime
import time

logger = logging.getLogger(__name__)

class AshbyAPIError(Exception):
    """Custom exception for Ashby API errors"""
    pass

class AshbyClient:
    """Client for interacting with Ashby's candidate API"""
    
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.base_url = "https://api.ashbyhq.com"
        self.session = requests.Session()
        self.session.auth = (api_key, "")  # Ashby uses API key as username, empty password
        self.session.headers.update({
            "Accept": "application/json",
            "Content-Type": "application/json"
        })
    
    def validate_api_key(self) -> bool:
        """Test if the API key is valid by making a simple request"""
        try:
            response = self._make_request("candidate.list", {"limit": 1})
            return response.get("success", False)
        except Exception as e:
            logger.error(f"API key validation failed: {e}")
            return False
    
    def _make_request(self, endpoint: str, data: Dict) -> Dict:
        """Make a request to the Ashby API with error handling"""
        url = f"{self.base_url}/{endpoint}"
        
        try:
            response = self.session.post(url, json=data)
            response.raise_for_status()
            
            result = response.json()
            
            if not result.get("success", False):
                errors = result.get("errors", ["Unknown error"])
                raise AshbyAPIError(f"Ashby API error: {', '.join(errors)}")
            
            return result
            
        except requests.RequestException as e:
            raise AshbyAPIError(f"Request failed: {e}")
        except json.JSONDecodeError as e:
            raise AshbyAPIError(f"Invalid JSON response: {e}")
    
    def sync_candidates_full(self, limit: int = 100) -> Generator[Dict, None, str]:
        """
        Perform a full sync of all candidates with pagination
        Returns: Generator yielding candidate batches, final return is sync_token
        """
        logger.info("Starting full candidate sync")
        
        cursor = None
        total_processed = 0
        
        while True:
            # Build request data
            request_data = {"limit": limit}
            if cursor:
                request_data["cursor"] = cursor
            
            # Make request
            response = self._make_request("candidate.list", request_data)
            
            # Yield the current batch
            candidates = response.get("results", [])
            if candidates:
                yield {
                    "candidates": candidates,
                    "batch_size": len(candidates),
                    "total_processed": total_processed + len(candidates)
                }
                total_processed += len(candidates)
            
            # Check if we're done
            if not response.get("moreDataAvailable", False):
                sync_token = response.get("syncToken")
                logger.info(f"Full sync completed. Total candidates: {total_processed}")
                return sync_token
            
            # Get cursor for next page
            cursor = response.get("nextCursor")
            if not cursor:
                raise AshbyAPIError("No nextCursor provided but moreDataAvailable is true")
            
            # Small delay to be respectful to the API
            time.sleep(0.1)
    
    def sync_candidates_incremental(self, sync_token: str, limit: int = 100) -> Generator[Dict, None, str]:
        """
        Perform incremental sync using a sync token
        Returns: Generator yielding candidate batches, final return is new sync_token
        """
        logger.info(f"Starting incremental candidate sync with token: {sync_token[:10]}...")
        
        cursor = None
        total_processed = 0
        
        while True:
            # Build request data
            request_data = {"limit": limit, "syncToken": sync_token}
            if cursor:
                request_data["cursor"] = cursor
            
            try:
                # Make request
                response = self._make_request("candidate.list", request_data)
            except AshbyAPIError as e:
                if "sync_token_expired" in str(e):
                    raise AshbyAPIError("Sync token expired. Need to perform full sync.")
                raise
            
            # Yield the current batch
            candidates = response.get("results", [])
            if candidates:
                yield {
                    "candidates": candidates,
                    "batch_size": len(candidates),
                    "total_processed": total_processed + len(candidates)
                }
                total_processed += len(candidates)
            
            # Check if we're done
            if not response.get("moreDataAvailable", False):
                new_sync_token = response.get("syncToken")
                logger.info(f"Incremental sync completed. Total candidates: {total_processed}")
                return new_sync_token
            
            # Get cursor for next page
            cursor = response.get("nextCursor")
            if not cursor:
                raise AshbyAPIError("No nextCursor provided but moreDataAvailable is true")
            
            # Small delay to be respectful to the API
            time.sleep(0.1)
    
    def extract_linkedin_url(self, candidate: Dict) -> Optional[str]:
        """Extract LinkedIn URL from candidate's social links"""
        social_links = candidate.get("socialLinks", [])
        for link in social_links:
            if link.get("type") == "LinkedIn":
                url = link.get("url", "").strip()
                if url:
                    return url
        return None
    
    def get_candidate_name(self, candidate: Dict) -> str:
        """Get candidate's full name, handling missing fields gracefully"""
        name = candidate.get("name", "").strip()
        if name:
            return name
        
        # Fallback: try to construct from email or ID
        email = candidate.get("primaryEmailAddress", {})
        if email:
            return email.get("value", "").split("@")[0].replace(".", " ").title()
        
        # Last resort: use candidate ID
        return f"Candidate {candidate.get('id', 'Unknown')[:8]}"
    
    def get_candidate_email(self, candidate: Dict) -> Optional[str]:
        """Get candidate's primary email address"""
        primary_email = candidate.get("primaryEmailAddress", {})
        return primary_email.get("value") if primary_email else None
    
    def format_candidate_for_storage(self, candidate: Dict) -> Dict:
        """
        Format a candidate from Ashby API for storage in our system
        Returns: Dictionary with standardized fields for PeopleSources.source_metadata
        """
        return {
            "ashby_candidate_id": candidate.get("id"),
            "ashby_data": candidate,  # Store complete Ashby response
            "integration_sync": {
                "last_synced": datetime.utcnow().isoformat(),
                "sync_status": "synced"
            },
            "extracted_fields": {
                "name": self.get_candidate_name(candidate),
                "email": self.get_candidate_email(candidate),
                "linkedin_url": self.extract_linkedin_url(candidate),
                "position": candidate.get("position"),
                "company": candidate.get("company"),
                "school": candidate.get("school"),
                "tags": [tag.get("title") for tag in candidate.get("tags", [])],
                "location": candidate.get("primaryLocation", {}).get("locationSummary")
            }
        }

def test_ashby_connection(api_key: str) -> Tuple[bool, str]:
    """
    Test function to validate Ashby API connection
    Returns: (success: bool, message: str)
    """
    try:
        client = AshbyClient(api_key)
        if client.validate_api_key():
            return True, "Connection successful"
        else:
            return False, "Invalid API key or insufficient permissions"
    except Exception as e:
        return False, f"Connection failed: {str(e)}" 