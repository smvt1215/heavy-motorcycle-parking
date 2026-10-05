-- Enable PostGIS extension
-- This runs on first database initialization via Docker entrypoint
CREATE EXTENSION IF NOT EXISTS postgis;
