-- Initial DB setup for agent_company v2
-- Run automatically when the postgres container starts for the first time.

-- Enable pgvector for future RAG / semantic skill search
CREATE EXTENSION IF NOT EXISTS vector;

-- Full-text search helpers
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- UUID helper (optional; for future id schemes)
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Placeholder schema_version table so migrations can track state
CREATE TABLE IF NOT EXISTS schema_migrations (
  version TEXT PRIMARY KEY,
  applied_at TIMESTAMPTZ DEFAULT NOW()
);

INSERT INTO schema_migrations (version) VALUES ('0000_bootstrap')
  ON CONFLICT (version) DO NOTHING;

-- v2 tables will be created by the app on startup (once db.py migrates to psycopg).
-- For now this file only prepares the extensions.
