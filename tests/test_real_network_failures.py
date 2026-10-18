"""Opt-in network tests that need NO valid key: the real SDK against the real endpoint with bad inputs (RUN_LIVE_NETWORK=1)."""
import os

import pytest

from finsight.agents.text_to_sql.llm import AnthropicClient, LLMUnavailable

pytestmark = [pytest.mark.live_network]
KEY = "sk-" + "ant-api03-INVALID-KEY-FOR-TESTING"  # built at runtime: deliberately invalid, and never a scannable literal
skip = pytest.mark.skipif(os.environ.get("RUN_LIVE_NETWORK") != "1", reason="set RUN_LIVE_NETWORK=1 to contact api.anthropic.com with a deliberately invalid key")


@pytest.fixture(autouse=True)
def real_endpoint(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)       # do not inherit a local proxy


@skip
def test_invalid_key_is_rejected_by_the_real_endpoint_without_leaking_the_key():
    with pytest.raises(LLMUnavailable) as e:
        AnthropicClient(KEY, "claude-sonnet-5-5", timeout_s=20).generate("s", "u")
    assert "AuthenticationError" in str(e.value) and KEY not in str(e.value)


@skip
def test_real_timeout_is_reported_as_unavailable():
    import anthropic
    sdk = anthropic.Anthropic(api_key=KEY, timeout=0.001, max_retries=0)
    with pytest.raises(LLMUnavailable) as e:
        AnthropicClient(KEY, "claude-sonnet-5-5", client=sdk).generate("s", "u")
    assert "Timeout" in str(e.value) or "Connection" in str(e.value)
    assert KEY not in str(e.value)
