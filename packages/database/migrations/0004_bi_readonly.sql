-- Least-privilege role for the Text-to-SQL agent. DEV DEFAULT PASSWORD: change it (and BI_DATABASE_URL) outside local use.
DO $$ BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'finsight_bi') THEN
    CREATE ROLE finsight_bi LOGIN PASSWORD 'finsight_bi_dev_password'
      NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION CONNECTION LIMIT 20;
  END IF;
END $$;
ALTER ROLE finsight_bi SET statement_timeout = '5s';
ALTER ROLE finsight_bi SET idle_in_transaction_session_timeout = '10s';
ALTER ROLE finsight_bi SET default_transaction_read_only = 'on';
ALTER ROLE finsight_bi SET search_path = 'marts';

CREATE SCHEMA IF NOT EXISTS marts;
REVOKE ALL ON SCHEMA core, eval FROM finsight_bi;
REVOKE ALL ON ALL TABLES IN SCHEMA core, eval FROM finsight_bi;
GRANT USAGE ON SCHEMA marts TO finsight_bi;
GRANT SELECT ON ALL TABLES IN SCHEMA marts TO finsight_bi;
-- dbt drops/recreates mart tables on every build; new tables inherit SELECT automatically.
ALTER DEFAULT PRIVILEGES IN SCHEMA marts GRANT SELECT ON TABLES TO finsight_bi;

CREATE TABLE core.bi_query_log (
  query_id BIGSERIAL PRIMARY KEY,
  asked_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  question_sha256 TEXT NOT NULL,
  question_preview TEXT NOT NULL,
  mode TEXT NOT NULL,
  status TEXT NOT NULL,
  sql TEXT,
  attempts INTEGER NOT NULL DEFAULT 0,
  row_count INTEGER,
  error_category TEXT,
  model TEXT,
  input_tokens INTEGER,
  output_tokens INTEGER,
  llm_ms DOUBLE PRECISION,
  db_ms DOUBLE PRECISION
);
