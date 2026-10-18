-- Audit trail of model lifecycle events (registration, evaluation, promotion, rollback).
CREATE TABLE core.model_events (
  event_id BIGSERIAL PRIMARY KEY,
  event_type TEXT NOT NULL CHECK (event_type IN ('registered','evaluated','promoted','rolled_back','promotion_refused')),
  model_name TEXT NOT NULL,
  model_version TEXT NOT NULL,
  actor TEXT NOT NULL,
  detail JSONB NOT NULL DEFAULT '{}',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- Output of each monitoring run. Feature drift, prediction drift and performance are separate signals.
CREATE TABLE core.monitoring_runs (
  run_id BIGSERIAL PRIMARY KEY,
  model_version TEXT NOT NULL,
  window_start TIMESTAMPTZ,
  window_end TIMESTAMPTZ,
  n_current INTEGER NOT NULL,
  feature_drift BOOLEAN NOT NULL,
  drifted_features JSONB NOT NULL DEFAULT '[]',
  prediction_drift BOOLEAN NOT NULL,
  data_quality_issues JSONB NOT NULL DEFAULT '[]',
  performance JSONB,            -- NULL until labels are available for the window
  performance_degraded BOOLEAN,
  retrain_candidate_version TEXT,
  config JSONB NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
