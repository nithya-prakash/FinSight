"""Verifies the golden set against INDEPENDENT Python calculations from the fixture dataset, then runs the harness."""
from collections import Counter
from datetime import timedelta

import psycopg
import pytest

from finsight.agents.text_to_sql.evals import normalize, run_eval

pytestmark = pytest.mark.integration


def expected(env):
    ds = env["ds"]
    lab = {x.transaction_id: x.is_fraud for x in ds.labels}
    with psycopg.connect(env["owner"]) as c:
        flagged = {r[0] for r in c.execute("SELECT transaction_id FROM core.fraud_alerts")}
        alert_accounts = {r[0] for r in c.execute("SELECT t.account_id FROM core.fraud_alerts a JOIN core.transactions t USING (transaction_id)")}
        n_alerts = len(flagged)
    tier = {c.category_code: c.risk_tier for c in ds.categories}
    mer = {m.merchant_id: m for m in ds.merchants}
    seg = {c.customer_id: c.segment for c in ds.customers}
    tx = ds.transactions
    n = len(tx)
    fraud = [t for t in tx if lab[t.transaction_id]]
    legit = [t for t in tx if not lab[t.transaction_id]]
    tp = sum(1 for t in fraud if t.transaction_id in flagged)
    fp = sum(1 for t in legit if t.transaction_id in flagged)
    last = max(t.event_time.date() for t in tx)
    week = Counter((t.event_time.date() - timedelta(days=t.event_time.weekday())).isoformat() for t in tx)
    cat = Counter(mer[t.merchant_id].category_code for t in tx if t.merchant_id)
    top3 = sorted(cat.items(), key=lambda kv: (-kv[1], kv[0]))[:3]
    by_seg = {s: [t for t in tx if seg[t.customer_id] == s] for s in set(seg.values())}
    return {
        "g01": [(k, v) for k, v in Counter(t.transaction_type for t in tx).items()],
        "g02": [(round(float(sum(t.amount for t in tx)), 2),)],
        "g03": [(sum(t.status == "declined" for t in tx),)],
        "g04": list(Counter(tier[mer[t.merchant_id].category_code] for t in fraud if t.merchant_id).items()),
        "g05": [(len(flagged) / n,)],
        "g06": [(fp / len(legit),)],
        "g07": [(tp / (tp + fp), tp / len(fraud))],
        "g08": [(s, sum(lab[t.transaction_id] for t in ts) / len(ts)) for s, ts in by_seg.items()],
        "g09": [(sum(t.event_time.date() > last - timedelta(days=7) for t in tx),)],
        "g10": sorted(week.items()),
        "g11": [(7.0,)],
        "g12": [(3 / n_alerts,)],
        "g13": [(round(float(sum(t.amount for t in fraud if t.transaction_id not in flagged)), 2),)],
        "g14": [(len(alert_accounts),)],
        "g15": top3,
    }


def test_reference_sql_matches_independent_python_expectations(bi_env, agent):
    import yaml

    from finsight.agents.text_to_sql.evals import GOLDEN
    exp = expected(bi_env)
    cases = [c for c in yaml.safe_load(GOLDEN.read_text())["cases"] if "reference_sql" in c]
    assert {c["id"] for c in cases} == set(exp)
    for c in cases:
        r = agent.run_reference(c["reference_sql"], c["question"])
        assert r.status == "answered", (c["id"], r.message)
        got = normalize([[round(v, 2) if (isinstance(v, float) and c["id"] in ("g02", "g13")) else v for v in row] for row in r.rows],
                        c.get("ordered", False))
        want = normalize([list(row) for row in exp[c["id"]]], c.get("ordered", False))
        assert len(got) == len(want), c["id"]
        for g, w in zip(got, want, strict=True):
            assert all(abs(a - b) < 1e-6 if isinstance(b, float) else a == b for a, b in zip(g, w, strict=True)), (c["id"], g, w)


def test_harness_reports_denominators_and_marks_llm_cases_not_evaluated(agent):
    rep = run_eval(agent, "reference")
    assert rep["failures"] == [] and rep["evaluated"] == 15 and rep["passed"] == 15
    assert rep["not_evaluated"] == 8 and rep["llm"] is None
    assert rep["categories"]["ambiguous"]["not_evaluated"] == 3 and "NOT a measure" in rep["note"]


def test_harness_scores_llm_mode_with_scripted_llm(bi_env):
    """Checks the scoring logic only (scripted outputs, not Claude)."""
    from finsight.agents.text_to_sql.agent import BIAgent
    from finsight.agents.text_to_sql.executor import ExecConfig

    from .conftest import FakeLLM
    wrong = {"status": "sql", "sql": "SELECT COUNT(*) AS n FROM marts.fact_transactions"}
    llm = FakeLLM(*([wrong] * 150))
    a = BIAgent(bi_env["bi"], llm, ExecConfig(row_limit=100), None)
    rep = run_eval(a, "llm")
    assert rep["evaluated"] == 23 and rep["accuracy"] < 0.5 and rep["failure_kinds"]["wrong_result"] >= 10
