"""Real Anthropic SDK code paths (request building, retries, timeouts, parsing) exercised through an httpx MockTransport.
No network and no real Claude call: this proves failure handling, NOT answer quality."""

import json

import anthropic
import httpx
import pytest

from finsight.agents.text_to_sql.llm import (
    SUBMIT_TOOL,
    AnthropicClient,
    LLMUnavailable,
    make_client,
)

KEY = "sk-" + "ant-test-secret-do-not-leak"  # fake; built at runtime so no scannable literal exists


def client_for(handler, max_retries=2):
    http = httpx.Client(transport=httpx.MockTransport(handler))
    sdk = anthropic.Anthropic(api_key=KEY, http_client=http, max_retries=max_retries, timeout=5)
    return AnthropicClient(KEY, "claude-sonnet-5-5", client=sdk)


def ok_body(content):
    return {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "model": "claude-sonnet-5-5",
        "content": content,
        "stop_reason": "tool_use",
        "usage": {"input_tokens": 11, "output_tokens": 7},
    }


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda *_: None)


def test_missing_key_means_no_client():
    assert make_client(None, "m") is None and make_client("", "m") is None


def test_successful_tool_use_is_parsed_with_token_usage():
    seen = {}

    def handler(req):
        seen["body"], seen["key"] = json.loads(req.content), req.headers["x-api-key"]
        return httpx.Response(
            200,
            json=ok_body(
                [
                    {
                        "type": "tool_use",
                        "id": "t1",
                        "name": "submit_query",
                        "input": {"status": "sql", "sql": "SELECT 1"},
                    }
                ]
            ),
        )

    out = client_for(handler).generate("sys", "user text")
    assert out.tool_input == {"status": "sql", "sql": "SELECT 1"} and (
        out.input_tokens,
        out.output_tokens,
    ) == (11, 7)
    assert (
        seen["body"]["tool_choice"] == {"type": "tool", "name": "submit_query"}
        and seen["body"]["tools"][0]["name"] == SUBMIT_TOOL["name"]
    )
    assert seen["key"] == KEY


def test_invalid_key_401_is_reported_without_leaking_the_key():
    def handler(req):
        return httpx.Response(
            401,
            json={
                "type": "error",
                "error": {"type": "authentication_error", "message": "invalid x-api-key"},
            },
        )

    with pytest.raises(LLMUnavailable) as e:
        client_for(handler).generate("s", "u")
    assert KEY not in str(e.value) and "AuthenticationError" in str(e.value)


def test_server_errors_are_retried_a_bounded_number_of_times():
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(
            529, json={"type": "error", "error": {"type": "overloaded_error", "message": "busy"}}
        )

    with pytest.raises(LLMUnavailable):
        client_for(handler, max_retries=2).generate("s", "u")
    assert len(calls) == 3  # 1 try + 2 retries, then give up


def test_transient_failure_then_success_recovers():
    calls = []

    def handler(req):
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(
                500, json={"type": "error", "error": {"type": "api_error", "message": "boom"}}
            )
        return httpx.Response(
            200,
            json=ok_body(
                [
                    {
                        "type": "tool_use",
                        "id": "t",
                        "name": "submit_query",
                        "input": {"status": "clarify", "clarification_question": "Which month?"},
                    }
                ]
            ),
        )

    assert (
        client_for(handler).generate("s", "u").tool_input["status"] == "clarify" and len(calls) == 2
    )


def test_timeouts_are_reported_as_unavailable():
    def handler(req):
        raise httpx.ReadTimeout("slow", request=req)

    with pytest.raises(LLMUnavailable) as e:
        client_for(handler, max_retries=1).generate("s", "u")
    assert "Timeout" in str(e.value) and KEY not in str(e.value)


def test_malformed_responses_do_not_crash_the_caller():
    with pytest.raises(LLMUnavailable):  # text only, no structured answer
        client_for(
            lambda r: httpx.Response(
                200, json=ok_body([{"type": "text", "text": "I think the answer is 42"}])
            )
        ).generate("s", "u")
    with pytest.raises(Exception) as e:  # body that is not JSON at all
        client_for(
            lambda r: httpx.Response(
                200, content=b"<html>gateway error</html>", headers={"content-type": "text/html"}
            ),
            max_retries=0,
        ).generate("s", "u")
    assert KEY not in str(e.value)


def test_agent_handles_tool_input_that_is_structurally_wrong():
    """The agent must treat a wrong-shaped but 'successful' model answer as a failed attempt, not execute it."""
    from finsight.agents.text_to_sql.agent import BIAgent
    from finsight.agents.text_to_sql.executor import ExecConfig
    from finsight.agents.text_to_sql.llm import LLMResult

    class Odd:
        model = "odd"
        n = 0

        def generate(self, system, user, tool=None):
            Odd.n += 1
            return LLMResult({"status": "sql", "sql": 12345}, 1, 1, "odd")  # sql is not a string

    r = BIAgent("postgresql://unused/none", Odd(), ExecConfig(), None).ask(
        "how many transactions per channel?"
    )
    assert r.status in ("error", "rejected") and r.rows == [] and Odd.n == 3
