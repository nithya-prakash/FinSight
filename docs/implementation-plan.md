# Implementation plan
| Phase | Scope | Status |
|---|---|---|
| 1 | Foundation, Postgres, migrations, synthetic data generator, tests | implemented, verified in Docker |
| 2 | Redpanda, rules engine, idempotent consumer, API | implemented, verified in Docker |
| 3 | dbt + Dagster warehouse | implemented, verified in Docker |
| 4 | Fraud ML, MLflow, drift, gated retraining | implemented, verified in Docker |
| 5 | Text-to-SQL agent + evals | implemented; live Claude path unverified (no API key) |
| 6 | LangGraph investigator | implemented; live Claude path unverified (no API key) |
| 7 | Next.js UI, OpenTelemetry | implemented, verified in browser; live OTLP backend unverified |
| 8 | CI, hardening, docs | implemented; clean-environment run verified; CI not yet run on GitHub |

Decision: Phase 1 uses a simple SQL migration runner instead of Alembic (no ORM yet); revisit when the API needs models.
