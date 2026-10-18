CREATE SCHEMA core;
CREATE SCHEMA eval;  -- ground truth, NEVER used as model/inference input

CREATE TABLE core.customers (
  customer_id TEXT PRIMARY KEY,
  full_name TEXT NOT NULL,
  home_region TEXT NOT NULL,
  segment TEXT NOT NULL CHECK (segment IN ('retail','premium','small_business')),
  created_at TIMESTAMPTZ NOT NULL
);
CREATE TABLE core.accounts (
  account_id TEXT PRIMARY KEY,
  customer_id TEXT NOT NULL REFERENCES core.customers,
  account_type TEXT NOT NULL CHECK (account_type IN ('checking','savings','credit_card')),
  currency CHAR(3) NOT NULL,
  opened_at TIMESTAMPTZ NOT NULL
);
CREATE TABLE core.merchant_categories (
  category_code TEXT PRIMARY KEY,
  description TEXT NOT NULL,
  risk_tier TEXT NOT NULL CHECK (risk_tier IN ('low','medium','high'))
);
CREATE TABLE core.merchants (
  merchant_id TEXT PRIMARY KEY,
  merchant_name TEXT NOT NULL,
  category_code TEXT NOT NULL REFERENCES core.merchant_categories,
  region TEXT NOT NULL,
  lat DOUBLE PRECISION NOT NULL,
  lon DOUBLE PRECISION NOT NULL
);
CREATE TABLE core.devices (
  device_id TEXT PRIMARY KEY,
  customer_id TEXT REFERENCES core.customers,
  device_type TEXT NOT NULL CHECK (device_type IN ('mobile','desktop','pos_terminal','atm'))
);
CREATE TABLE core.transactions (
  transaction_id TEXT PRIMARY KEY,
  event_time TIMESTAMPTZ NOT NULL,
  customer_id TEXT NOT NULL REFERENCES core.customers,
  account_id TEXT NOT NULL REFERENCES core.accounts,
  merchant_id TEXT REFERENCES core.merchants,
  counterparty_account_id TEXT REFERENCES core.accounts,
  amount NUMERIC(14,2) NOT NULL CHECK (amount > 0),
  currency CHAR(3) NOT NULL,
  transaction_type TEXT NOT NULL CHECK (transaction_type IN ('purchase','transfer','cash_withdrawal')),
  channel TEXT NOT NULL CHECK (channel IN ('online','pos','transfer','atm')),
  device_id TEXT REFERENCES core.devices,
  ip_region TEXT,
  lat DOUBLE PRECISION,
  lon DOUBLE PRECISION,
  status TEXT NOT NULL CHECK (status IN ('approved','declined')),
  CHECK ((transaction_type = 'transfer') = (counterparty_account_id IS NOT NULL)),
  CHECK ((transaction_type = 'purchase') = (merchant_id IS NOT NULL))
);
CREATE INDEX transactions_event_time_idx ON core.transactions (event_time);
CREATE INDEX transactions_account_time_idx ON core.transactions (account_id, event_time);
CREATE INDEX transactions_merchant_idx ON core.transactions (merchant_id);

-- Ground truth, kept apart from core.* so features cannot accidentally read it.
CREATE TABLE eval.fraud_labels (
  transaction_id TEXT PRIMARY KEY REFERENCES core.transactions,
  is_fraud BOOLEAN NOT NULL,
  scenario TEXT NOT NULL,
  scenario_instance_id TEXT NOT NULL,
  generator_version TEXT NOT NULL,
  dataset_id TEXT NOT NULL
);
CREATE INDEX fraud_labels_scenario_idx ON eval.fraud_labels (scenario);

CREATE TABLE eval.datasets (
  dataset_id TEXT PRIMARY KEY,
  generator_version TEXT NOT NULL,
  seed INTEGER NOT NULL,
  config JSONB NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Tables populated by later phases (contracts fixed now).
CREATE TABLE core.model_predictions (
  prediction_id BIGSERIAL PRIMARY KEY,
  transaction_id TEXT NOT NULL REFERENCES core.transactions,
  model_version TEXT NOT NULL,
  risk_score DOUBLE PRECISION NOT NULL CHECK (risk_score BETWEEN 0 AND 1),
  evidence JSONB NOT NULL DEFAULT '{}',
  scored_at TIMESTAMPTZ NOT NULL,
  UNIQUE (transaction_id, model_version)
);
CREATE TABLE core.fraud_alerts (
  alert_id BIGSERIAL PRIMARY KEY,
  transaction_id TEXT NOT NULL UNIQUE REFERENCES core.transactions,
  severity TEXT NOT NULL CHECK (severity IN ('low','medium','high')),
  signals JSONB NOT NULL DEFAULT '[]',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE core.investigation_cases (
  case_id BIGSERIAL PRIMARY KEY,
  alert_id BIGINT NOT NULL REFERENCES core.fraud_alerts,
  status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','escalated','closed_fraud','closed_legit')),
  opened_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  closed_at TIMESTAMPTZ
);
