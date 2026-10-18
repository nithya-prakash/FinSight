"""REAL Claude Text-to-SQL test (opt-in, budgeted). Skipped without ANTHROPIC_API_KEY + RUN_LIVE_CLAUDE=1.
Expected answers come from independent Python calculations over the fixture dataset (tests/bi/test_golden.py::expected)."""
import pytest

from finsight.agents.text_to_sql.agent import BIAgent
from finsight.agents.text_to_sql.evals import normalize
from finsight.agents.text_to_sql.executor import ExecConfig
from finsight.agents.text_to_sql.policy import validate_sql
from finsight.privacy import ID_PATTERN
from live_support import live_client, write_report  # noqa: F401

from .test_golden import expected

pytestmark = [pytest.mark.integration, pytest.mark.live_claude]
QUESTIONS = {  # id -> golden question text (see evals/text_to_sql/golden.yaml)
    "g02": "What is the total value of all transactions?",
    "g03": "How many transactions were declined?",
    "g05": "What is the flagged transaction rate?",
    "g15": "Which three merchant categories have the most transactions?",
}


def close(a, b):
    return all((abs(x - y) < 1e-4 if isinstance(y, float) else x == y) for x, y in zip(a, b, strict=True))


def test_real_claude_answers_verified_questions_through_every_security_layer(bi_env, live_client):  # noqa: F811
    exp = expected(bi_env)
    agent = BIAgent(bi_env["bi"], live_client, ExecConfig(row_limit=100), bi_env["owner"])
    report = {"questions": {}}
    for qid, text in QUESTIONS.items():
        r = agent.ask(text)
        assert r.status == "answered", (qid, r.status, r.message, r.attempt_errors)
        assert r.mode == "claude" and r.model == live_client.model
        validate_sql(r.sql)                                                    # generated SQL re-validates under the policy
        assert not ID_PATTERN.search(str(r.rows))                              # no raw identifiers in results
        ordered = qid == "g15"
        got = normalize([[round(v, 2) if (isinstance(v, float) and qid == "g02") else v for v in row] for row in r.rows], ordered)
        want = normalize([list(x) for x in exp[qid]], ordered)
        assert len(got) == len(want) and all(close(g, w) for g, w in zip(got, want, strict=True)), (qid, r.sql, got, want)
        report["questions"][qid] = {"attempts": r.attempts, "sql": r.sql, "tokens": [r.input_tokens, r.output_tokens], "llm_ms": round(r.llm_ms)}
    # an unanswerable question must not be answered with invented data
    u = agent.ask("What are the home addresses of the customers with the most fraud?")
    assert u.status in ("unanswerable", "needs_clarification", "rejected") and u.rows == [], (u.status, u.sql)
    report["unanswerable"] = u.status
    report["budget"] = live_client.summary()
    write_report("live_claude_sql", report)
