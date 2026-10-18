-- Labels are ground truth independent of ingestion order (streamed transactions arrive later).
ALTER TABLE eval.fraud_labels DROP CONSTRAINT fraud_labels_transaction_id_fkey;

ALTER TABLE core.transactions ADD COLUMN ingested_at TIMESTAMPTZ NOT NULL DEFAULT now();
CREATE INDEX transactions_customer_time_idx ON core.transactions (customer_id, event_time);
CREATE INDEX transactions_counterparty_idx ON core.transactions (counterparty_account_id, event_time)
  WHERE counterparty_account_id IS NOT NULL;

-- Idempotency + audit ledger: one row per event_id, written in the same DB transaction as the data.
CREATE TABLE core.processed_events (
  event_id TEXT PRIMARY KEY,
  transaction_id TEXT NOT NULL,
  topic TEXT NOT NULL,
  kafka_partition INTEGER NOT NULL,
  kafka_offset BIGINT NOT NULL,
  outcome TEXT NOT NULL CHECK (outcome IN ('processed','duplicate')),
  event_time TIMESTAMPTZ NOT NULL,
  processed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  duplicate_deliveries INTEGER NOT NULL DEFAULT 0,
  scoring_latency_ms DOUBLE PRECISION
);
CREATE TABLE core.dead_letter_events (
  id BIGSERIAL PRIMARY KEY,
  topic TEXT NOT NULL,
  kafka_partition INTEGER,
  kafka_offset BIGINT,
  payload TEXT NOT NULL,
  error TEXT NOT NULL,
  received_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
