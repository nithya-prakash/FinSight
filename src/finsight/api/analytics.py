"""Read-only analytics for the dashboard, computed from live tables. Labels (eval.fraud_labels) are SYNTHETIC ground truth."""
import json
import os
import urllib.request
from pathlib import Path

import psycopg
from fastapi import APIRouter, Query
from psycopg.rows import dict_row

from ..config import get_settings
from ..observability import estimate_cost
from .auth import viewer

EVAL_DIR = Path(__file__).resolve().parents[3] / "evals" / "reports"
LABEL_NOTE = "Fraud labels are synthetic ground truth available for offline evaluation; they are not confirmed real-world outcomes."


def build_router(dsn: str) -> APIRouter:
    r = APIRouter(tags=["analytics"], dependencies=[viewer])

    def q(sql, params=None, one=False):
        with psycopg.connect(dsn, row_factory=dict_row) as c:
            cur = c.execute(sql, params or {})
            return cur.fetchone() if one else cur.fetchall()

    @r.get("/analytics/overview")
    def overview():
        t = q("SELECT count(*) AS transactions, coalesce(sum(amount),0)::float AS value, max(event_time) AS last_event FROM core.transactions", one=True)
        a = q("SELECT count(*) AS alerts, count(*) FILTER (WHERE severity='high') AS high, count(*) FILTER (WHERE severity='medium') AS medium, "
              "count(*) FILTER (WHERE severity='low') AS low FROM core.fraud_alerts", one=True)
        l = q("SELECT count(*) AS labeled, count(*) FILTER (WHERE l.is_fraud) AS fraud, coalesce(sum(t.amount) FILTER (WHERE l.is_fraud),0)::float AS exposure, "
              "count(*) FILTER (WHERE l.is_fraud AND fa.alert_id IS NOT NULL) AS caught FROM core.transactions t JOIN eval.fraud_labels l USING (transaction_id) "
              "LEFT JOIN core.fraud_alerts fa USING (transaction_id)", one=True)
        recent = q("SELECT a.alert_id, a.severity, a.created_at, t.account_id, t.amount::float AS amount FROM core.fraud_alerts a JOIN core.transactions t USING (transaction_id) "
                   "ORDER BY a.alert_id DESC LIMIT 6")
        return {**t, **a, "labeled": l["labeled"], "labeled_fraud": l["fraud"],
                "confirmed_fraud_rate": (l["fraud"] / l["labeled"]) if l["labeled"] else None,
                "estimated_exposure": l["exposure"], "fraud_caught": l["caught"],
                "recall_on_labels": (l["caught"] / l["fraud"]) if l["fraud"] else None,
                "flagged_rate": (a["alerts"] / t["transactions"]) if t["transactions"] else None,
                "recent_alerts": recent, "label_note": LABEL_NOTE}

    @r.get("/analytics/trends")
    def trends(days: int = Query(30, ge=1, le=365)):
        rows = q("WITH m AS (SELECT max(event_time)::date AS d FROM core.transactions) "
                 "SELECT t.event_time::date AS day, count(*) AS transactions, count(fa.alert_id) AS alerts, "
                 "count(*) FILTER (WHERE l.is_fraud) AS labeled_fraud, sum(t.amount)::float AS value "
                 "FROM core.transactions t CROSS JOIN m LEFT JOIN core.fraud_alerts fa USING (transaction_id) LEFT JOIN eval.fraud_labels l USING (transaction_id) "
                 "WHERE t.event_time::date > m.d - %(days)s GROUP BY 1 ORDER BY 1", {"days": days})
        return {"days": days, "items": rows, "label_note": LABEL_NOTE}

    @r.get("/analytics/categories")
    def categories():
        return {"items": q("SELECT m.category_code, k.risk_tier, count(*) AS transactions, count(*) FILTER (WHERE l.is_fraud) AS labeled_fraud, "
                           "coalesce(sum(t.amount) FILTER (WHERE l.is_fraud),0)::float AS fraud_exposure, count(fa.alert_id) AS alerts "
                           "FROM core.transactions t JOIN core.merchants m USING (merchant_id) JOIN core.merchant_categories k USING (category_code) "
                           "LEFT JOIN eval.fraud_labels l USING (transaction_id) LEFT JOIN core.fraud_alerts fa USING (transaction_id) "
                           "GROUP BY 1,2 ORDER BY fraud_exposure DESC"), "label_note": LABEL_NOTE}

    @r.get("/analytics/channels")
    def channels():
        return {"items": q("SELECT t.channel, count(*) AS transactions, sum(t.amount)::float AS value, count(*) FILTER (WHERE l.is_fraud) AS labeled_fraud, "
                           "count(fa.alert_id) AS alerts FROM core.transactions t LEFT JOIN eval.fraud_labels l USING (transaction_id) "
                           "LEFT JOIN core.fraud_alerts fa USING (transaction_id) GROUP BY 1 ORDER BY 2 DESC"), "label_note": LABEL_NOTE}

    @r.get("/analytics/false-positives")
    def false_positives():
        rows = q("SELECT s->>'rule' AS rule, count(*) AS alerts, count(*) FILTER (WHERE NOT l.is_fraud) AS false_positives FROM core.fraud_alerts a "
                 "CROSS JOIN LATERAL jsonb_array_elements(a.signals) s JOIN eval.fraud_labels l USING (transaction_id) GROUP BY 1 ORDER BY 3 DESC")
        for x in rows:
            x["false_positive_share"] = x["false_positives"] / x["alerts"] if x["alerts"] else None
        return {"items": rows, "note": "A transaction is counted once per rule that fired. " + LABEL_NOTE}

    @r.get("/analytics/investigations")
    def investigation_outcomes():
        return {"items": q("SELECT status, count(*) AS cases FROM core.investigation_cases GROUP BY 1 ORDER BY 2 DESC"),
                "avg_resolution_hours": (q("SELECT avg(extract(epoch FROM closed_at - opened_at)/3600)::float AS h FROM core.investigation_cases WHERE closed_at IS NOT NULL", one=True) or {}).get("h")}

    @r.get("/models")
    def models():
        s = get_settings()
        events = q("SELECT event_type, model_version, actor, detail, created_at FROM core.model_events ORDER BY event_id DESC LIMIT 25")
        runs = q("SELECT run_id, model_version, n_current, feature_drift, drifted_features, prediction_drift, performance_degraded, created_at FROM core.monitoring_runs ORDER BY run_id DESC LIMIT 10")
        out = {"scoring_mode": s.scoring_mode, "alert_threshold_rules": s.alert_threshold, "ml_threshold_override": s.ml_threshold,
               "events": events, "monitoring_runs": runs, "registry": {"available": False, "versions": [], "approved": None}}
        if s.mlflow_tracking_uri:
            os.environ.setdefault("MLFLOW_HTTP_REQUEST_TIMEOUT", "3")      # fail fast when the registry is down
            os.environ.setdefault("MLFLOW_HTTP_REQUEST_MAX_RETRIES", "0")
            try:
                from ..ml.registry import MODEL_NAME, approved_version, configure
                cl = configure(s.mlflow_tracking_uri)
                vs = []
                for v in cl.search_model_versions(f"name='{MODEL_NAME}'"):
                    t = v.tags or {}
                    vs.append({"version": str(v.version), "aliases": list(v.aliases), "acceptance": t.get("acceptance"), "threshold": t.get("threshold"),
                               "val_pr_auc": t.get("val_pr_auc"), "dataset_id": t.get("dataset_id"), "reasons": t.get("acceptance_reasons")})
                out["registry"] = {"available": True, "approved": approved_version(cl), "versions": sorted(vs, key=lambda x: -int(x["version"]))}
            except Exception as e:  # registry down is a normal state to display
                out["registry"]["error"] = type(e).__name__
        return out

    @r.get("/observability/summary")
    def observability():
        s = get_settings()
        pipe = q("SELECT count(*) FILTER (WHERE outcome='processed') AS processed, coalesce(sum(duplicate_deliveries),0)::int AS redeliveries, "
                 "round(avg(scoring_latency_ms)::numeric,2)::float AS avg_scoring_ms, max(processed_at) AS last_processed_at, "
                 "extract(epoch FROM now()-max(processed_at))::float AS seconds_since_last_event, "
                 "count(*) FILTER (WHERE processed_at > now() - interval '5 minutes') AS last_5m FROM core.processed_events", one=True)
        lat = q("SELECT percentile_cont(0.95) WITHIN GROUP (ORDER BY scoring_latency_ms)::float AS p95, percentile_cont(0.5) WITHIN GROUP (ORDER BY scoring_latency_ms)::float AS p50 "
                "FROM core.processed_events WHERE scoring_latency_ms IS NOT NULL", one=True)
        pipe["scoring_ms_p50"], pipe["scoring_ms_p95"] = lat["p50"], lat["p95"]
        pipe["data_quality_failures_last_hour"] = q("SELECT count(*) AS n FROM core.dead_letter_events WHERE received_at > now() - interval '1 hour'", one=True)["n"]
        try:
            from ..pipeline.lag import consumer_lag
            pipe["consumer_lag"] = consumer_lag(s.kafka_bootstrap, s.consumer_group, s.kafka_topic, timeout=3)
        except Exception as e:  # broker unreachable is a state to display, not a crash
            pipe["consumer_lag"] = {"error": type(e).__name__}
        pipe["dead_letters"] = q("SELECT count(*) AS n FROM core.dead_letter_events", one=True)["n"]
        pipe["throughput_per_min_last_5m"] = round(pipe["last_5m"] / 5, 2)
        bi = q("SELECT count(*) AS requests, count(*) FILTER (WHERE status='answered') AS answered, count(*) FILTER (WHERE status IN ('error','rejected')) AS failed, "
               "count(*) FILTER (WHERE status='llm_unavailable') AS llm_unavailable, coalesce(avg(llm_ms) FILTER (WHERE model IS NOT NULL),0)::float AS avg_llm_ms, "
               "coalesce(avg(db_ms),0)::float AS avg_db_ms, coalesce(sum(input_tokens),0)::int AS input_tokens, coalesce(sum(output_tokens),0)::int AS output_tokens, "
               "coalesce(sum(attempts - 1) FILTER (WHERE attempts > 1),0)::int AS retries, mode FROM core.bi_query_log GROUP BY mode")
        tot_in = sum(x["input_tokens"] for x in bi)
        tot_out = sum(x["output_tokens"] for x in bi)
        agents = q("SELECT event_type, count(*) AS n FROM core.audit_events GROUP BY 1 ORDER BY 2 DESC")
        reports = q("SELECT status, count(*) AS n FROM core.case_reports GROUP BY 1")
        evals = []
        if EVAL_DIR.exists():
            for f in sorted(EVAL_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:10]:
                try:
                    d = json.loads(f.read_text())
                    evals.append({"file": f.name, "mode": d.get("mode"), "llm": d.get("llm"), "evaluated": d.get("evaluated"), "passed": d.get("passed"),
                                  "accuracy": d.get("accuracy"), "not_evaluated": d.get("not_evaluated"), "ran_at": f.stat().st_mtime})
                except (OSError, ValueError):
                    pass
        services = {"database": "ok"}
        if s.mlflow_tracking_uri:
            try:
                urllib.request.urlopen(s.mlflow_tracking_uri.rstrip("/") + "/health", timeout=2)
                services["mlflow"] = "ok"
            except Exception:
                services["mlflow"] = "unreachable"
        services["llm"] = "configured" if s.anthropic_api_key else "not configured"
        services["tracing_exporter"] = "otlp" if os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT") else "none"
        return {"services": services, "pipeline": pipe, "bi": {"by_mode": bi, "estimated_cost": estimate_cost(s.bi_model, tot_in, tot_out)},
                "agent_events": agents, "case_reports": reports, "evaluations": evals,
                "freshness_note": "seconds_since_last_event is wall-clock age of the last processed event; synthetic event times are historical."}

    return r
