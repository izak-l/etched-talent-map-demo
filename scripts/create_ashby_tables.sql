-- Ashby Integration Database Schema
-- Run this script to set up tables for Ashby integration

-- Store Ashby integration settings (API keys, sync tokens, etc.)
CREATE TABLE IF NOT EXISTS ashby_integrations (
    id SERIAL PRIMARY KEY,
    api_key_encrypted TEXT NOT NULL,
    sync_token TEXT,
    last_sync_at TIMESTAMP,
    is_active BOOLEAN DEFAULT true,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Track sync jobs for monitoring and debugging
CREATE TABLE IF NOT EXISTS ashby_sync_jobs (
    id SERIAL PRIMARY KEY,
    integration_id INTEGER REFERENCES ashby_integrations(id),
    job_type VARCHAR(50) NOT NULL, -- 'initial' or 'incremental'
    status VARCHAR(50) NOT NULL DEFAULT 'running', -- 'running', 'completed', 'failed'
    candidates_processed INTEGER DEFAULT 0,
    candidates_created INTEGER DEFAULT 0,
    candidates_updated INTEGER DEFAULT 0,
    candidates_skipped INTEGER DEFAULT 0,
    error_message TEXT,
    started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP,
    
    CONSTRAINT valid_job_type CHECK (job_type IN ('initial', 'incremental')),
    CONSTRAINT valid_status CHECK (status IN ('running', 'completed', 'failed'))
);

-- Create indexes for performance
CREATE INDEX IF NOT EXISTS idx_ashby_integrations_active ON ashby_integrations(is_active);
CREATE INDEX IF NOT EXISTS idx_ashby_sync_jobs_status ON ashby_sync_jobs(status);
CREATE INDEX IF NOT EXISTS idx_ashby_sync_jobs_type ON ashby_sync_jobs(job_type);
CREATE INDEX IF NOT EXISTS idx_ashby_sync_jobs_started ON ashby_sync_jobs(started_at);

-- Add comments for documentation
COMMENT ON TABLE ashby_integrations IS 'Stores Ashby API integration configuration and sync state';
COMMENT ON TABLE ashby_sync_jobs IS 'Tracks Ashby candidate sync jobs for monitoring and debugging';

COMMENT ON COLUMN ashby_integrations.api_key_encrypted IS 'Encrypted Ashby API key';
COMMENT ON COLUMN ashby_integrations.sync_token IS 'Latest sync token from Ashby API for incremental syncs';
COMMENT ON COLUMN ashby_integrations.last_sync_at IS 'Timestamp of last successful sync';

COMMENT ON COLUMN ashby_sync_jobs.job_type IS 'Type of sync: initial (full) or incremental (changes only)';
COMMENT ON COLUMN ashby_sync_jobs.status IS 'Current status of the sync job';
COMMENT ON COLUMN ashby_sync_jobs.candidates_processed IS 'Total number of candidates processed in this job';
COMMENT ON COLUMN ashby_sync_jobs.candidates_created IS 'Number of new candidates created';
COMMENT ON COLUMN ashby_sync_jobs.candidates_updated IS 'Number of existing candidates updated';
COMMENT ON COLUMN ashby_sync_jobs.candidates_skipped IS 'Number of candidates skipped (duplicates, etc.)'; 