# Observability

* **Tracing (OpenTelemetry)**: FastAPI is auto-instrumented; custom spans `bi.ask` (model, tokens, estimated USD, attempts, retries, validation failures, DB/LLM ms, status),
  `investigation.run` and one span per LangGraph node (`investigator.retrieve|analyze|draft|persist|human_review|record` = workflow transitions).
  Spans are exported with OTLP/HTTP when `OTEL_EXPORTER_OTLP_ENDPOINT` is set; `docker compose --profile observability up -d jaeger` starts an optional Jaeger (UI :16686).
  Questions, SQL and results are never recorded in spans (only a 12-char question fingerprint). Tests assert this with an in-memory exporter.
* **Metrics**: Prometheus text at `GET /metrics` (`finsight_http_request_seconds`, `finsight_bi_queries_total`, `finsight_llm_tokens_total`, `finsight_bi_llm_seconds`, `finsight_investigations_total`).
  Consumer metrics (`:9108` inside the consumer container): `finsight_scoring_seconds` (per-event scoring latency), `finsight_consumer_events_total{outcome}`, `finsight_data_quality_failures_total{kind}`. Real consumer lag (`finsight_consumer_lag{partition}`, log-end minus committed offset from the broker) is refreshed on `GET /metrics` and shown in `/observability/summary` with p50/p95 scoring latency and dead-letter counts. The consumer container healthcheck requires a fresh poll-loop heartbeat.
  Pipeline throughput, freshness, dead letters and redeliveries come from `GET /observability/summary` (derived from `core.processed_events`); consumer lag is proxied by data freshness (no broker-offset metric yet).
* **Cost**: `observability/pricing.yaml` is versioned metadata with ILLUSTRATIVE placeholder prices; every figure is labelled an estimate, not a billed amount.
* **Logs**: JSON logs with request ids (`x-request-id`), no secrets.
* **UI**: the Operations page shows service health, freshness, pipeline stats, AI usage and cost estimate, agent events and evaluation history.
* **Not verified**: export to a live Jaeger/OTLP backend (only the in-memory exporter path is tested).
