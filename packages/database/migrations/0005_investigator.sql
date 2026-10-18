-- Investigator: least-privilege read role (no ground-truth labels, no customer names) + report/audit storage.
DO $$ BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'finsight_investigator') THEN
    CREATE ROLE finsight_investigator LOGIN PASSWORD 'finsight_inv_dev_password' NOSUPERUSER NOCREATEDB NOCREATEROLE CONNECTION LIMIT 20;
  END IF;
END $$;
ALTER ROLE finsight_investigator SET default_transaction_read_only = 'on';
ALTER ROLE finsight_investigator SET statement_timeout = '10s';
REVOKE ALL ON SCHEMA eval FROM finsight_investigator;
GRANT USAGE ON SCHEMA core TO finsight_investigator;
GRANT SELECT ON core.transactions, core.accounts, core.merchants, core.merchant_categories, core.devices,
      core.model_predictions, core.fraud_alerts, core.investigation_cases TO finsight_investigator;
GRANT SELECT (customer_id, home_region, segment, created_at) ON core.customers TO finsight_investigator;  -- not full_name

CREATE TABLE core.case_reports (
  report_id BIGSERIAL PRIMARY KEY,
  run_id TEXT NOT NULL UNIQUE,
  case_id BIGINT NOT NULL REFERENCES core.investigation_cases,
  alert_id BIGINT NOT NULL REFERENCES core.fraud_alerts,
  status TEXT NOT NULL DEFAULT 'pending_review' CHECK (status IN ('pending_review','approved_fraud','closed_legit','escalated','needs_more_info')),
  llm_status TEXT NOT NULL,
  report JSONB NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  decided_at TIMESTAMPTZ
);
CREATE TABLE core.audit_events (
  event_id BIGSERIAL PRIMARY KEY,
  run_id TEXT NOT NULL,
  case_id BIGINT,
  event_type TEXT NOT NULL,
  actor TEXT NOT NULL,          -- 'system', 'agent' or the human analyst's name
  detail JSONB NOT NULL DEFAULT '{}',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX audit_run_idx ON core.audit_events (run_id, event_id);
