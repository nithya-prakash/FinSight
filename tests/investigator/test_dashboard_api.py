"""Dashboard endpoints, metrics and tracing (uses the streamed investigator fixture database)."""
import psycopg
import pytest
from fastapi.testclient import TestClient
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from bi.conftest import FakeLLM
from finsight.agents.investigator.service import make_service
from finsight.api.app import create_app
from finsight.observability import estimate_cost, setup_tracing

from .conftest import fresh_alert, settings_for

pytestmark = pytest.mark.integration


@pytest.fixture()
def api(env, monkeypatch):
    s = settings_for(env)
    monkeypatch.setenv("DATABASE_URL", s.database_url)
    monkeypatch.setenv("INVESTIGATOR_DATABASE_URL", s.investigator_database_url)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("MLFLOW_TRACKING_URI", raising=False)
    return TestClient(create_app(env["owner"]))


def one(env, sql):
    with psycopg.connect(env["owner"]) as c:
        return c.execute(sql).fetchone()[0]


def test_overview_matches_database_and_python_labels(api, env):
    d = api.get("/analytics/overview").json()
    ds = env["ds"]
    assert d["transactions"] == len(ds.transactions) == one(env, "select count(*) from core.transactions")
    assert d["alerts"] == one(env, "select count(*) from core.fraud_alerts") and d["high"] + d["medium"] + d["low"] == d["alerts"]
    fraud = sum(x.is_fraud for x in ds.labels)
    assert d["labeled_fraud"] == fraud and d["confirmed_fraud_rate"] == pytest.approx(fraud / len(ds.labels))
    assert d["flagged_rate"] != d["confirmed_fraud_rate"] and "synthetic" in d["label_note"]


def test_breakdowns_are_consistent(api, env):
    n = len(env["ds"].transactions)
    ch = api.get("/analytics/channels").json()["items"]
    assert sum(x["transactions"] for x in ch) == n
    tr = api.get("/analytics/trends", params={"days": 7}).json()["items"]
    assert 1 <= len(tr) <= 7 and sum(x["transactions"] for x in tr) < n
    assert api.get("/analytics/trends", params={"days": 0}).status_code == 422
    cats = api.get("/analytics/categories").json()["items"]
    assert sum(x["labeled_fraud"] for x in cats) <= sum(x.is_fraud for x in env["ds"].labels)
    fp = api.get("/analytics/false-positives").json()["items"]
    assert fp and all(0 <= x["false_positive_share"] <= 1 for x in fp)


def test_models_endpoint_degrades_without_registry(api):
    m = api.get("/models").json()
    assert m["registry"]["available"] is False and m["scoring_mode"] == "rules" and "events" in m


def test_observability_summary_and_cost_is_labelled_estimate(api, env):
    o = api.get("/observability/summary").json()
    assert o["services"]["database"] == "ok" and o["services"]["llm"] == "not configured"
    assert o["pipeline"]["processed"] == len(env["ds"].transactions)
    assert o["bi"]["estimated_cost"]["is_estimate"] is True and "not a billed" in o["bi"]["estimated_cost"]["note"]
    c = estimate_cost("claude-sonnet-5-5", 1_000_000, 0)
    assert c["estimated_usd"] == 3.0 and c["pricing_version"] == 1


def test_prometheus_metrics_exposed(api):
    api.get("/analytics/overview")
    body = api.get("/metrics").text
    assert 'finsight_http_request_seconds_count{method="GET",route="/analytics/overview",status="200"}' in body


def test_investigation_list_endpoint(api, env):
    rid = api.post("/investigations", json={"alert_id": env["alerts"]["velocity_burst"]}).json()["run_id"]
    items = api.get("/investigations").json()
    assert any(x["run_id"] == rid and x["status"] == "pending_review" for x in items)


def test_traces_capture_llm_and_workflow_without_sensitive_data(env, bi_env_unused=None):
    exp = InMemorySpanExporter()
    setup_tracing(exp)
    from finsight.agents.text_to_sql.agent import BIAgent
    from finsight.agents.text_to_sql.executor import ExecConfig
    bi_dsn = env["owner"].replace("finsight_inv_test", "finsight_inv_test")
    sql = {"status": "sql", "sql": "SELECT * FROM core.customers"}   # rejected by policy -> counted as validation failures
    agent = BIAgent(bi_dsn, FakeLLM(sql, sql, sql), ExecConfig(), None)
    secret_q = "show me account acc_000001_0 balances please"
    agent.ask(secret_q)
    from finsight.agents.text_to_sql.llm import LLMUnavailable

    class Down:
        model = "down"

        def generate(self, *a, **k):
            raise LLMUnavailable("down")
    svc = make_service(settings_for(env), Down())
    svc.start(fresh_alert(env))
    spans = {s.name: s for s in exp.get_finished_spans()}
    a = spans["bi.ask"].attributes
    assert a["bi.status"] == "rejected" and a["bi.attempts"] == 3 and a["bi.retries"] == 2 and a["bi.validation_failures"] == 3
    assert a["llm.model"] == "fake-scripted-llm" and a["llm.input_tokens"] == 300 and a["llm.estimated_usd"] > 0
    assert "investigation.run" in spans and "investigator.retrieve" in spans and "investigator.human_review" in spans
    flat = " ".join(str(v) for s in exp.get_finished_spans() for v in s.attributes.values())
    assert secret_q not in flat and "acc_000001_0" not in flat and "SELECT" not in flat
    setup_tracing(InMemorySpanExporter())


def test_metrics_and_errors_do_not_expose_identifiers_or_secrets(api, env):
    api.get("/alerts/1")
    api.get("/transactions", params={"account_id": "id_abc", "limit": 1})
    body = api.get("/metrics").text
    from finsight.privacy import ID_PATTERN
    assert not ID_PATTERN.search(body)
    for secret in ("test-analyst-key", "test-viewer-key", "password", "postgresql://", "dev-only-mask-key"):
        assert secret not in body
    assert 'route="/alerts/{alert_id}"' in body                       # templated routes, never literal ids
    err = api.get("/transactions", params={"min_risk": "acc_000001_0"})
    assert err.status_code == 422 and not ID_PATTERN.search(err.text)  # validation errors echo input: it is pseudonymised too


def test_logs_never_contain_identifiers_or_keys(api, env):
    import logging

    from finsight.logs import JsonFormatter
    from finsight.privacy import ID_PATTERN
    lines: list[str] = []

    class Collect(logging.Handler):
        def emit(self, record):
            if record.name.startswith("finsight"):   # the TestClient's own httpx logger is not the server
                lines.append(JsonFormatter().format(record))
    h = Collect()
    logging.getLogger().addHandler(h)
    api.get("/transactions", params={"account_id": "acc_000001_0", "limit": 1})
    api.get("/transactions", params={"reveal_ids": "true", "limit": 2})
    api.get("/alerts/1", headers={"x-api-key": "test-viewer-key"})
    api.get("/alerts", headers={"x-api-key": "wrong-key-value"})
    logging.getLogger().removeHandler(h)
    text = "\n".join(lines)
    assert '"msg": "request"' in text and "identifier_reveal" in text         # requests are logged, reveals are logged
    assert not ID_PATTERN.search(text)
    for secret in ("test-analyst-key", "test-viewer-key", "wrong-key-value", "account_id=", "reveal_ids="):
        assert secret not in text, secret


def test_scoring_percentiles_match_an_independent_calculation(api, env):
    import numpy as np
    with psycopg.connect(env["owner"]) as c:
        vals = np.array([r[0] for r in c.execute("SELECT scoring_latency_ms FROM core.processed_events WHERE scoring_latency_ms IS NOT NULL")])
    p = api.get("/observability/summary").json()["pipeline"]
    assert len(vals) > 100
    assert p["scoring_ms_p50"] == pytest.approx(float(np.percentile(vals, 50)), rel=1e-6)
    assert p["scoring_ms_p95"] == pytest.approx(float(np.percentile(vals, 95)), rel=1e-6)


def test_http_request_spans_are_exported_through_the_configured_provider(env, monkeypatch):
    exp = InMemorySpanExporter()
    setup_tracing(exp)                       # configure first: create_app must pick this provider up
    s = settings_for(env)
    monkeypatch.setenv("DATABASE_URL", s.database_url)
    monkeypatch.setenv("INVESTIGATOR_DATABASE_URL", s.investigator_database_url)
    client = TestClient(create_app(env["owner"]))
    client.get("/analytics/overview")
    names = {sp.name for sp in exp.get_finished_spans()}
    attrs = {k: v for sp in exp.get_finished_spans() for k, v in sp.attributes.items()}
    assert any("/analytics/overview" in n for n in names), names
    assert attrs.get("http.route") == "/analytics/overview" or any("overview" in str(v) for v in attrs.values())
    setup_tracing(InMemorySpanExporter())
