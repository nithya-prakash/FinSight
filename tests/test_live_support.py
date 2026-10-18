"""Always-on tests for the live-test support code itself (no network, no API key)."""
import pytest

import live_support
from finsight.agents.text_to_sql.llm import LLMResult
from live_support import BudgetedClient, BudgetExceeded


class Fake:
    model = "claude-sonnet-5-5"

    def __init__(self, i=1000, o=500):
        self.i, self.o, self.n = i, o, 0

    def generate(self, system, user, tool=None):
        self.n += 1
        return LLMResult({"status": "sql"}, self.i, self.o, self.model)


def test_call_budget_is_enforced_before_the_request():
    inner = Fake()
    c = BudgetedClient(inner, max_calls=2, max_tokens=10**9, max_usd=100)
    c.generate("s", "u"), c.generate("s", "u")
    with pytest.raises(BudgetExceeded, match="call budget"):
        c.generate("s", "u")
    assert inner.n == 2                                   # the third request never reached the API


def test_token_and_cost_budgets_stop_further_calls():
    c = BudgetedClient(Fake(40_000, 1_000), max_calls=50, max_tokens=50_000, max_usd=100)
    c.generate("s", "u"), c.generate("s", "u")
    with pytest.raises(BudgetExceeded, match="token budget"):
        c.generate("s", "u")
    d = BudgetedClient(Fake(1_000_000, 0), max_calls=50, max_tokens=10**9, max_usd=1.0)   # ~$3 per call at placeholder prices
    d.generate("s", "u")
    with pytest.raises(BudgetExceeded, match="cost budget"):
        d.generate("s", "u")


def test_summary_records_model_latency_tokens_and_marks_cost_as_estimate():
    c = BudgetedClient(Fake(), max_usd=5)
    c.generate("s", "u")
    s = c.summary()
    assert s["model"] == "claude-sonnet-5-5" and s["calls"] == 1 and s["tokens"] == 1500 and s["is_estimate"] is True
    assert s["per_call"][0]["latency_ms"] >= 0


def test_live_tests_are_double_opt_in(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("RUN_LIVE_CLAUDE", "1")
    assert live_support.live_enabled() is False
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    monkeypatch.delenv("RUN_LIVE_CLAUDE")
    assert live_support.live_enabled() is False
    monkeypatch.setenv("RUN_LIVE_CLAUDE", "1")
    assert live_support.live_enabled() is True
