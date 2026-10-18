-- Concurrency control for investigations: one active run per alert, and an atomic claim for decisions.
CREATE TABLE core.investigation_runs (
  run_id TEXT PRIMARY KEY,
  alert_id BIGINT NOT NULL REFERENCES core.fraud_alerts,
  state TEXT NOT NULL CHECK (state IN ('running','pending_review','decided','failed')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX one_active_run_per_alert ON core.investigation_runs (alert_id) WHERE state IN ('running','pending_review');
ALTER TABLE core.case_reports ADD COLUMN decision_claim TEXT, ADD COLUMN claimed_at TIMESTAMPTZ;
-- existing reports (created before this migration) keep working: register them as runs
INSERT INTO core.investigation_runs (run_id, alert_id, state)
SELECT run_id, alert_id, CASE WHEN status = 'pending_review' THEN 'pending_review' ELSE 'decided' END FROM core.case_reports
ON CONFLICT DO NOTHING;
