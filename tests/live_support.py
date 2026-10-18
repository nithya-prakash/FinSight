"""Support for OPT-IN tests that call the real Anthropic API.

Double opt-in: ANTHROPIC_API_KEY must be set AND RUN_LIVE_CLAUDE=1. They are never part of the default/CI run.
A hard budget (calls, tokens, estimated USD) is enforced BEFORE each request. Synthetic data only; no secrets are logged.
"""
import json
import os
import time
from pathlib import Path

import pytest

from finsight.agents.text_to_sql.llm import AnthropicClient, LLMResult
from finsight.observability import estimate_cost

REPORT_DIR = Path(__file__).resolve().parents[1] / "evals" / "reports"


class BudgetExceeded(RuntimeError):
    pass


class BudgetedClient:
    """Wraps a real LLM client: enforces the budget, records latency / tokens / model / prompts (prompts stay in memory only)."""

    def __init__(self, inner, max_calls: int = 14, max_tokens: int = 80_000, max_usd: float | None = None):
        self.inner, self.model = inner, inner.model
        self.max_calls, self.max_tokens = max_calls, max_tokens
        self.max_usd = max_usd if max_usd is not None else float(os.environ.get("LIVE_CLAUDE_MAX_USD", "0.50"))
        self.calls: list[dict] = []
        self.prompts: list[str] = []

    @property
    def tokens(self) -> int:
        return sum(c["input_tokens"] + c["output_tokens"] for c in self.calls)

    @property
    def usd(self) -> float:
        return sum(c["estimated_usd"] for c in self.calls)

    def generate(self, system: str, user: str, tool: dict | None = None) -> LLMResult:
        if len(self.calls) >= self.max_calls:
            raise BudgetExceeded(f"call budget {self.max_calls} reached")
        if self.tokens >= self.max_tokens:
            raise BudgetExceeded(f"token budget {self.max_tokens} reached")
        if self.usd >= self.max_usd:
            raise BudgetExceeded(f"cost budget ${self.max_usd} reached (estimate)")
        self.prompts.append(user)
        t0 = time.perf_counter()
        out = self.inner.generate(system, user, tool)
        self.calls.append({"latency_ms": round((time.perf_counter() - t0) * 1000), "input_tokens": out.input_tokens, "output_tokens": out.output_tokens,
                           "estimated_usd": estimate_cost(self.model, out.input_tokens, out.output_tokens)["estimated_usd"]})
        return out

    def summary(self) -> dict:
        return {"model": self.model, "calls": len(self.calls), "tokens": self.tokens, "estimated_usd": round(self.usd, 6), "is_estimate": True,
                "budget": {"max_calls": self.max_calls, "max_tokens": self.max_tokens, "max_usd": self.max_usd}, "per_call": self.calls}


def live_enabled() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY")) and os.environ.get("RUN_LIVE_CLAUDE") == "1"


@pytest.fixture()
def live_client():
    """A budgeted REAL client, or a skip with the exact setup instructions."""
    if not live_enabled():
        pytest.skip("real-Claude test skipped: set ANTHROPIC_API_KEY and RUN_LIVE_CLAUDE=1 (see docs/evaluation.md; `make live-claude`)")
    model = os.environ.get("LIVE_CLAUDE_MODEL", "claude-sonnet-5-5")
    return BudgetedClient(AnthropicClient(os.environ["ANTHROPIC_API_KEY"], model, max_tokens=1500, timeout_s=60.0))


def write_report(name: str, payload: dict) -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    p = REPORT_DIR / f"{name}.json"
    p.write_text(json.dumps(payload, indent=2, default=str))
    return p
