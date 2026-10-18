# Data model and warehouse

## Layers and ownership
| Layer | Schema | Writer | Built as |
|---|---|---|---|
| Raw | `core`, `eval` | streaming consumer / seed / generator **only** | tables (migrations) |
| Staging | `stg` | dbt | views: typed, deduplicated, names dropped |
| Intermediate | `int` | dbt | tables: history, velocity, labeled joins |
| Marts | `marts` | dbt | tables: star schema + metrics |

dbt never writes to `core`/`eval`; the consumer never writes to `stg/int/marts`. Dagster orchestrates dbt;
the `raw_ingested_events` gate asset fails the run if nothing has been ingested. Rebuilds are full and idempotent.

## Star schema (marts)
| Table | Grain |
|---|---|
| fact_transactions | one row per transaction (`transaction_id`) |
| fact_fraud_predictions | one row per (`transaction_id`, `model_version`) |
| fact_fraud_alerts | one row per alert (`alert_id`; at most one per transaction) |
| fact_investigation_outcomes | one row per investigation case |
| dim_customer / dim_account / dim_merchant | one row per entity |
| dim_date | one row per calendar day in the observed range (`date_key` = YYYYMMDD) |
| dim_channel / dim_transaction_type | one row per value |

## Metrics (`metrics_daily`, `metrics_by_category`, `metrics_alerts`)
| Metric | Definition | Denominator | Notes |
|---|---|---|---|
| txn_count / txn_value | all transactions / sum(amount) in EUR | n/a | declined transactions included |
| fraud_rate | labeled fraud txns / labeled txns | labeled transactions that day | uses ground truth, synthetic |
| flagged_rate | alerted txns / all txns | all transactions | **not** the fraud rate |
| fraud_value / fraud_exposure | sum(amount) of labeled-fraud txns | n/a | gross, before any recovery |
| precision | TP / (TP+FP) | alerted labeled txns | NULL if none |
| recall | TP / (TP+FN) | labeled fraud txns | NULL if none |
| false_positive_rate | FP / actual negatives | labeled legit txns | |
| investigation_conversion_rate | alerts with a case / alerts | all alerts | needs analyst cases (Phase 6) |
| avg_resolution_hours | mean(closed_at - opened_at) | closed cases | |

Labels exist for every synthetic transaction immediately; in a real system they mature with delay, so these
metrics would be computed only on matured cohorts. Metrics are daily by event time, so weekly/monthly trends
come from joining `dim_date` (`week_start`, `month_start`).

## Quality
~105 dbt nodes: unique/not_null/relationships/accepted_values on keys, plus singular tests (alert needs a prediction,
rates in [0,1], fact count equals staging, confusion-matrix consistency, no future-dated events).
`dbt source freshness` warns when `core.transactions` is more than 1 day stale. Run `dbt docs generate && dbt docs serve`
inside the tools container for model lineage.

## Run
```bash
make up && make migrate
docker compose --profile warehouse up -d dagster-webserver dagster-daemon   # UI: http://localhost:3000
docker compose run --rm tools dagster job execute -f orchestration/dagster/definitions.py -j warehouse_build
```
The daily 02:00 UTC schedule starts enabled; failed steps retry 3x (10 s delay). Dagster state is SQLite in the
`dagster_home` volume (local development only).
