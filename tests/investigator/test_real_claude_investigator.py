"""REAL Claude investigator-analysis test (opt-in, budgeted). Every factual claim is re-verified independently against retrieved evidence."""
import pytest

from finsight.agents.investigator.evidence import gather
from finsight.agents.investigator.service import make_service
from finsight.agents.investigator.validation import ungrounded_numbers
from finsight.privacy import ID_PATTERN, mask_obj
from live_support import live_client, write_report  # noqa: F401

from .conftest import fresh_alert, settings_for

pytestmark = [pytest.mark.integration, pytest.mark.live_claude]


def test_real_claude_analysis_is_grounded_in_retrieved_evidence(env, live_client):  # noqa: F811
    alert = fresh_alert(env)
    settings = settings_for(env)
    result = make_service(settings, live_client).start(alert)
    rep = result["report"]
    assert rep["llm_status"] == "ok", (rep["llm_status"], rep["validation_issues"])
    a = rep["agent_analysis"]
    assert a and (a["flag_reasons"] or a["supporting_evidence"]) and a["confidence"] in ("low", "medium", "high")
    # independent re-check: rebuild the evidence the model saw (same pseudonymisation) and verify EVERY claim
    items = {i["id"]: i for i in mask_obj(gather(env["inv"], alert), settings.pii_mask_key)["items"]}
    checked = 0
    for section in ("flag_reasons", "supporting_evidence", "contradicting_evidence", "hypotheses"):
        for claim in a[section]:
            assert claim["evidence_ids"] and set(claim["evidence_ids"]) <= set(items), (section, claim)
            assert ungrounded_numbers(claim["text"], [items[e] for e in claim["evidence_ids"]]) == [], (section, claim)
            checked += 1
    assert checked >= 1 and all(h["label"] == "agent_hypothesis" for h in a["hypotheses"])
    assert not any(ID_PATTERN.search(p) for p in live_client.prompts)          # identifiers never reached the API provider in clear
    assert not ID_PATTERN.search(str(rep))
    assert result["case_status"] in ("open", "escalated") and rep["human_decision"] is None   # no consequential action taken
    write_report("live_claude_investigator", {"alert": "redacted", "claims_checked": checked, "confidence": a["confidence"], "grounding_issues_dropped": rep["validation_issues"],
                                              "budget": live_client.summary()})
