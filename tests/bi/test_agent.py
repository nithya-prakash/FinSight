import psycopg
import pytest
import yaml
from fastapi.testclient import TestClient

from finsight.agents.text_to_sql.agent import BIAgent
from finsight.agents.text_to_sql.executor import ExecConfig
from finsight.agents.text_to_sql.semantic import DIR, load_catalog, retrieve
from finsight.api.app import create_app

from .conftest import FakeLLM

pytestmark = pytest.mark.integration
OK_SQL = "SELECT channel, COUNT(*) AS n FROM marts.fact_transactions GROUP BY channel"


def mk(bi_env, *outs, **kw):
    llm = FakeLLM(*outs)
    return BIAgent(bi_env["bi"], llm, ExecConfig(row_limit=100, **kw), bi_env["owner"]), llm


def test_catalog_matches_live_database(bi_env):
    with psycopg.connect(bi_env["owner"]) as c:
        live: dict[str, set] = {}
        for t, col in c.execute("SELECT table_name, column_name FROM information_schema.columns WHERE table_schema='marts'"):
            live.setdefault(t, set()).add(col)
    cat = load_catalog()
    assert set(cat.tables) == set(live)
    for t in cat.tables:
        assert cat.columns(t) == live[t], t


def test_retrieval_picks_tables_and_glossary_terms():
    r = retrieve("What is the false positive rate by merchant category?")
    assert "fact_transactions" in r["tables"] and "false_positive_rate" in r["terms"]
    r2 = retrieve("how long do investigations take to resolve")
    assert "fact_investigation_outcomes" in r2["tables"] and "investigation_resolution_time" in r2["terms"]
    gl = yaml.safe_load((DIR / "glossary.yaml").read_text())["terms"]
    for must in ("transaction_volume", "transaction_value", "confirmed_fraud_rate", "flagged_transaction_rate", "fraud_loss",
                 "estimated_financial_exposure", "false_positive_rate", "precision_recall", "alert_conversion_rate",
                 "investigation_resolution_time"):
        assert must in gl


def test_answered_flow_with_chart_log_and_tokens(bi_env):
    a, llm = mk(bi_env, {"status": "sql", "sql": OK_SQL, "explanation": "counts", "chart": {"type": "bar", "x": "channel", "y": "n"}})
    r = a.ask("How many transactions per channel?")
    assert r.status == "answered" and r.mode == "claude" and r.row_count == 4 and r.chart["type"] == "bar"
    assert r.input_tokens == 100 and "LIMIT" in r.sql and "<question>" in llm.calls[0]
    with psycopg.connect(bi_env["owner"]) as c:
        row = c.execute("SELECT status, attempts, model FROM core.bi_query_log ORDER BY query_id DESC LIMIT 1").fetchone()
    assert row == ("answered", 1, "fake-scripted-llm")


def test_chart_with_unknown_columns_is_replaced_by_valid_spec(bi_env):
    a, _ = mk(bi_env, {"status": "sql", "sql": OK_SQL, "chart": {"type": "line", "x": "nope", "y": "n"}})
    r = a.ask("transactions per channel")
    assert r.chart["x"] in {c["name"] for c in r.columns} and r.chart["y"] == "n"


def test_bounded_repair_with_sanitized_errors(bi_env):
    a, llm = mk(bi_env, {"status": "sql", "sql": "SELECT full_name FROM marts.dim_customer"},
                {"status": "sql", "sql": OK_SQL})
    r = a.ask("transactions per channel")
    assert r.status == "answered" and r.attempts == 2 and r.attempt_errors[0]["code"] == "column_not_allowed"
    assert "previous_attempt" in llm.calls[1] and "column_not_allowed" in llm.calls[1]


def test_repair_is_bounded_and_unsafe_sql_never_executes(bi_env):
    a, llm = mk(bi_env, *[{"status": "sql", "sql": "SELECT * FROM core.customers"}] * 3)
    r = a.ask("show me customers")
    assert r.status == "rejected" and r.attempts == 3 and r.rows == [] and r.sql is None and len(llm.calls) == 3


def test_database_errors_are_sanitized_and_repaired(bi_env):
    a, _ = mk(bi_env, {"status": "sql", "sql": "SELECT SUM(channel) FROM marts.fact_transactions"}, {"status": "sql", "sql": OK_SQL})
    r = a.ask("transactions per channel")
    assert r.status == "answered" and r.attempt_errors[0]["stage"] == "database"
    assert "psycopg" not in r.attempt_errors[0]["message"] and "Traceback" not in r.attempt_errors[0]["message"]


def test_clarify_and_unanswerable_are_passed_through(bi_env):
    a, _ = mk(bi_env, {"status": "clarify", "clarification_question": "Which month?"})
    assert a.ask("last month please").status == "needs_clarification"
    a, _ = mk(bi_env, {"status": "unanswerable", "reason": "no address data"})
    r = a.ask("customer addresses")
    assert r.status == "unanswerable" and r.rows == []


def test_invalid_questions_do_not_reach_the_llm(bi_env):
    a, llm = mk(bi_env)
    assert a.ask("   ").status == "invalid_question" and a.ask("x" * 600).status == "invalid_question"
    assert llm.calls == []


def test_identifiers_are_pseudonymized_even_when_aliased(bi_env):
    a, _ = mk(bi_env, {"status": "sql", "sql": "SELECT account_id AS acct, COUNT(*) AS alerts FROM marts.fact_fraud_alerts GROUP BY 1 ORDER BY 2 DESC"})
    r = a.ask("which accounts have the most alerts")
    assert r.status == "answered" and r.masked_columns == ["acct"]
    assert all(row[0].startswith("id_") and "acc_" not in row[0] for row in r.rows)
    a2, _ = mk(bi_env, {"status": "sql", "sql": "SELECT * FROM marts.dim_account"})
    r2 = a2.ask("list accounts")
    assert {"account_id", "customer_id"} <= set(r2.masked_columns) and not any("acc_" in str(v) or "cus_" in str(v) for row in r2.rows for v in row)


def test_pseudonyms_are_stable_and_keyed(bi_env):
    q = {"status": "sql", "sql": "SELECT account_id FROM marts.dim_account ORDER BY 1 LIMIT 2"}
    r1 = mk(bi_env, q, mask_key="k1")[0].ask("accounts")
    r1b = mk(bi_env, q, mask_key="k1")[0].ask("accounts")
    r2 = mk(bi_env, q, mask_key="k2")[0].ask("accounts")
    assert r1.rows == r1b.rows and r1.rows != r2.rows


def test_small_groups_over_quasi_identifiers_are_suppressed(bi_env):
    ids = ",".join(f"'{t.transaction_id}'" for t in bi_env["ds"].transactions[:2])
    sql = ("SELECT c.segment, COUNT(*) AS n FROM marts.fact_transactions f JOIN marts.dim_customer c USING (customer_id) "
           f"WHERE f.transaction_id IN ({ids}) GROUP BY c.segment")
    r = mk(bi_env, {"status": "sql", "sql": sql})[0].ask("segment counts for two transactions")
    assert r.status == "answered" and r.rows == [] and r.suppressed_rows >= 1


def test_row_limit_is_enforced_in_sql(bi_env):
    r = mk(bi_env, {"status": "sql", "sql": "SELECT transaction_id FROM marts.fact_transactions LIMIT 100000"})[0].ask("all transactions")
    assert r.row_count == 100 and " LIMIT 100" in r.sql


def test_db_role_blocks_even_if_policy_were_bypassed(bi_env):
    with psycopg.connect(bi_env["bi"]) as c:
        for bad in ("SELECT * FROM core.transactions", "SELECT * FROM eval.fraud_labels", "SELECT * FROM stg.stg_transactions",
                    "SELECT * FROM public.schema_migrations"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                c.execute(bad)
            c.rollback()
        for bad in ("INSERT INTO marts.dim_channel VALUES ('x','y')", "DROP TABLE marts.dim_channel",
                    "CREATE TABLE marts.x (a int)", "CREATE TABLE public.x (a int)"):
            with pytest.raises((psycopg.errors.InsufficientPrivilege, psycopg.errors.ReadOnlySqlTransaction)):
                c.execute(bad)
            c.rollback()
        with pytest.raises(psycopg.errors.QueryCanceled):
            c.execute("SELECT pg_sleep(30)")  # role-level statement_timeout = 5s


def test_timeout_is_reported_without_internals(bi_env):
    from finsight.agents.text_to_sql.executor import ExecutionError, execute
    from finsight.agents.text_to_sql.policy import Validated
    with pytest.raises(ExecutionError) as e:
        execute(bi_env["bi"], Validated("SELECT pg_sleep(5) FROM marts.dim_channel", [], [], False), ExecConfig(timeout_ms=200))
    assert e.value.code == "timeout" and "pg_sleep" not in e.value.message


def test_missing_credentials_are_honest_and_demo_is_labelled(bi_env, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("BI_DATABASE_URL", bi_env["bi"])
    api = TestClient(create_app(bi_env["owner"]))
    st = api.get("/bi/status").json()
    assert st["llm_available"] is False and "ANTHROPIC_API_KEY" in st["message"]
    r = api.post("/bi/query", json={"question": "How many transactions per channel?"}).json()
    assert r["status"] == "llm_unavailable" and r["mode"] == "none" and r["rows"] == [] and r["sql"] is None
    demos = api.get("/bi/demo").json()
    assert "not generated by an LLM" in demos["label"]
    d = api.post(f"/bi/demo/{demos['queries'][0]['id']}").json()
    assert d["status"] == "answered" and d["mode"] == "demo_reference_sql" and d["rows"]
    assert api.post("/bi/demo/nope").status_code == 404


def test_bi_requires_analyst_role(bi_env, monkeypatch):
    monkeypatch.setenv("BI_DATABASE_URL", bi_env["bi"])
    api = TestClient(create_app(bi_env["owner"]), headers={})
    assert api.get("/bi/status").status_code == 401
    assert api.get("/bi/status", headers={"X-API-Key": "wrong"}).status_code == 401
    assert api.get("/bi/status", headers={"X-API-Key": "test-viewer-key"}).status_code == 403
    assert api.get("/bi/status", headers={"X-API-Key": "test-analyst-key"}).status_code == 200
