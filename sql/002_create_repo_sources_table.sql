CREATE TABLE IF NOT EXISTS repo_sources (
    id SERIAL PRIMARY KEY,
    repo_url TEXT UNIQUE NOT NULL,
    file_sha TEXT,              
    last_scanned_at TIMESTAMP,
    document_count INT DEFAULT 0,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_repo_sources_url ON repo_sources(repo_url);