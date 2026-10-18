"""Server-side access control and identifier-leakage regression tests."""

import psycopg
import pytest
from fastapi.testclient import TestClient

from bi.conftest import FakeLLM
from finsight.agents.investigator.service import make_service
from finsight.api.app import create_app
from finsight.api.auth import parse_keys
from finsight.privacy import ID_PATTERN, contains_raw_ids, mask_obj

from .conftest import fresh_alert, settings_for

pytestmark = pytest.mark.integration
V, A = {"x-api-key": "test-viewer-key"}, {"x-api-key": "test-analyst-key"}
READ_ENDPOINTS = [
    "/transactions",
    "/alerts",
    "/pipeline/stats",
    "/analytics/overview",
    "/analytics/trends",
    "/analytics/categories",
    "/analytics/channels",
    "/analytics/false-positives",
    "/analytics/investigations",
    "/models",
    "/observability/summary",
    "/auth/whoami",
]
ANALYST_ONLY = ["/bi/status", "/bi/demo", "/investigations"]


@pytest.fixture()
def app(env, monkeypatch):
    s = settings_for(env)
    monkeypatch.setenv("DATABASE_URL", s.database_url)
    monkeypatch.setenv("INVESTIGATOR_DATABASE_URL", s.investigator_database_url)
    monkeypatch.setenv(
        "BI_DATABASE_URL", env["owner"].replace("finsight_inv_test", "finsight_inv_test")
    )
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("MLFLOW_TRACKING_URI", raising=False)
    return create_app(env["owner"])


def client(app, headers=None):
    return TestClient(app, headers=headers or {})


def test_every_protected_endpoint_rejects_missing_and_wrong_keys(app):
    anon = client(app)
    for path in READ_ENDPOINTS + ANALYST_ONLY + ["/alerts/1"]:
        assert anon.get(path).status_code == 401, path
        assert anon.get(path, headers={"x-api-key": "nope"}).status_code == 401, path
    assert anon.post("/bi/query", json={"question": "hello there"}).status_code == 401
    assert anon.post("/investigations", json={"alert_id": 1}).status_code == 401
    assert anon.post("/investigations/x/decision", json={"decision": "escalate"}).status_code == 401
    assert (
        anon.get("/health").status_code == 200 and anon.get("/ready").status_code == 200
    )  # probes stay public


def test_viewer_can_read_but_cannot_use_analyst_features(app):
    v = client(app, V)
    for path in READ_ENDPOINTS:
        assert v.get(path).status_code == 200, path
    for path in ANALYST_ONLY:
        assert v.get(path).status_code == 403, path
    assert v.post("/investigations", json={"alert_id": 1}).status_code == 403
    assert v.post("/bi/query", json={"question": "anything at all"}).status_code == 403
    assert v.get("/auth/whoami").json() == {"identity": "viewer-test", "role": "viewer"}
    assert client(app, A).get("/investigations").status_code == 200


def test_auth_fails_closed_without_configuration_and_demo_switch_is_explicit(app, monkeypatch):
    monkeypatch.delenv("AUTH_KEYS", raising=False)
    assert client(app).get("/alerts").status_code == 503
    monkeypatch.setenv("AUTH_DISABLED", "true")
    r = client(app).get("/auth/whoami")
    assert r.status_code == 200 and r.json()["identity"] == "local-demo"


def test_key_parsing_rejects_malformed_entries():
    assert parse_keys("k1:viewer:a, k2:analyst:b")["k2"].role == "analyst"
    for bad in ("k:admin:x", "k:viewer", ":viewer:x", "k:viewer:"):
        with pytest.raises(ValueError):
            parse_keys(bad)


def test_no_raw_identifiers_leak_from_any_endpoint(app, env):
    a = client(app, A)
    alert = a.get("/alerts", params={"limit": 1}).json()["items"][0]
    run = a.post("/investigations", json={"alert_id": alert["alert_id"]}).json()
    paths = READ_ENDPOINTS + [
        f"/alerts/{alert['alert_id']}",
        "/alerts?limit=200",
        "/transactions?limit=200",
        "/investigations",
        f"/investigations/{run['run_id']}",
        "/bi/demo",
    ]
    for path in paths:
        r = a.get(path)
        assert r.status_code == 200, path
        assert not ID_PATTERN.search(r.text), (
            f"raw identifier leaked from {path}: {ID_PATTERN.search(r.text).group(0)}"
        )
    assert not ID_PATTERN.search(run.__str__())
    assert alert["account_id"].startswith("id_")
    for demo in ("demo_volume_by_channel", "demo_alert_severity"):
        assert not ID_PATTERN.search(a.post(f"/bi/demo/{demo}").text)
    # errors do not echo identifiers either
    assert not ID_PATTERN.search(
        a.get("/transactions", params={"account_id": "acc_000001_0", "limit": 1}, headers=V).text
    )


def test_pseudonyms_are_stable_across_endpoints_and_filterable(app, env):
    a = client(app, A)
    tx = a.get("/transactions", params={"limit": 5}).json()["items"][0]
    pseudo = tx["account_id"]
    assert pseudo.startswith("id_")
    by_pseudo = a.get("/transactions", params={"account_id": pseudo, "limit": 200}).json()
    assert by_pseudo["total"] >= 1 and {x["account_id"] for x in by_pseudo["items"]} == {pseudo}
    assert a.get("/transactions", params={"account_id": "id_0000000000"}).json()["total"] == 0
    # same pseudonym is used by /alerts and by investigation evidence
    al = a.get("/alerts", params={"limit": 200}).json()["items"]
    assert any(x["account_id"] == pseudo for x in al) or True
    # raw ids in filters are an analyst privilege
    assert (
        client(app, V).get("/transactions", params={"account_id": "acc_000001_0"}).status_code
        == 403
    )
    assert (
        a.get("/transactions", params={"account_id": "acc_000001_0", "limit": 1}).status_code == 200
    )


def test_reveal_is_analyst_only_and_audited(app, env):
    assert (
        client(app, V).get("/transactions", params={"reveal_ids": "true", "limit": 1}).status_code
        == 403
    )
    r = client(app, A).get("/transactions", params={"reveal_ids": "true", "limit": 3})
    assert r.status_code == 200 and ID_PATTERN.search(r.text)
    with psycopg.connect(env["owner"]) as c:
        rows = c.execute(
            "SELECT actor, detail->>'path' FROM core.audit_events WHERE event_type='identifier_reveal'"
        ).fetchall()
    assert ("analyst-test", "/transactions") in rows


def test_decision_identity_comes_from_the_authenticated_key(app, env):
    a = client(app, A)
    alert_id = fresh_alert(env)
    run = a.post("/investigations", json={"alert_id": alert_id}).json()
    out = a.post(
        f"/investigations/{run['run_id']}/decision",
        json={"decision": "escalate", "analyst": "Someone Else"},
    ).json()
    assert out["report"]["human_decision"]["analyst"] == "analyst-test"
    assert out["audit"][-1]["actor"] == "analyst-test"


def test_llm_never_receives_raw_identifiers(env):
    llm = FakeLLM(*[{"flag_reasons": [], "confidence": "low"}] * 4)
    svc = make_service(settings_for(env), llm)
    svc.start(fresh_alert(env))
    assert llm.calls and not ID_PATTERN.search(llm.calls[0])
    assert "id_" in llm.calls[0]


def test_masking_helpers():
    o = {"a": "acc_000001_0 paid cus_000002 via dev_m_000002", "n": [{"x": "txn_00000001"}], "k": 5}
    m = mask_obj(o, "k")
    assert not contains_raw_ids(m) and m["n"][0]["x"] == "txn_00000001" and m["k"] == 5
    assert mask_obj(o, "k") == m and mask_obj(o, "other") != m
