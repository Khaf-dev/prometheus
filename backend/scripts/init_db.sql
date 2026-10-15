-- Prometheus DB Initialization Script
-- Dijalankan otomatis saat container pertama kali dibuat

-- Set password untuk user prometheus (dari env var POSTGRES_PASSWORD)
ALTER USER prometheus WITH PASSWORD 'prometheus';

-- Enable pgvector extension
CREATE EXTENSION IF NOT EXISTS vector;

-- Enable pg_trgm untuk BM25-style text search (hybrid RAG)
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- Performance tuning untuk pgvector
-- Naikkan ef_search untuk HSNW query (default 40, 100 lebih akurat)
ALTER SYSTEM SET hnsw.ef_search = 100;

-- Work mem untuk sorting & hashing operations
ALTER SYSTEM SET work_mem = '64MB';

SELECT pg_reload_conf();
