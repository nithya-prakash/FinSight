"""LangGraph workflow: retrieve -> network -> analyze (validated, bounded retry) -> draft -> persist -> HUMAN REVIEW (interrupt) -> record.
The workflow can only write a report, a case status and audit rows; it has no tool that touches accounts or payments."""
import json
import logging
from dataclasses import dataclass
from typing import Any, TypedDict

import psycopg
from langgraph.graph import END, StateGraph
from langgraph.types import interrupt
from pydantic import ValidationError

from ...config import Settings
from ...logs import log
from ...observability import traced
from ...privacy import mask_obj
from ..text_to_sql.llm import LLMClient, LLMUnavailable
from .evidence import gather, jsonable
from .models import (
    ANALYSIS_TOOL,
    DECISIONS,
    DISCLAIMER,
    NARRATIVE_TOOL,
    SYSTEM_ANALYST,
    SYSTEM_DRAFTER,
    Analysis,
)
from .validation import ground

logger = logging.getLogger("finsight.investigator")
MAX_ANALYSIS_ATTEMPTS = 2


class State(TypedDict, total=False):
    run_id: str
    alert_id: int
    case_id: int
    bundle: dict
    analysis: dict | None
    issues: list
    attempts: int
    llm_status: str
    tokens: dict
    report: dict
    decision: dict


@dataclass
class Deps:
    settings: Settings
    llm: LLMClient | None
    inv_dsn: str
    owner_dsn: str


def audit(dsn: str, run_id: str, case_id: int | None, event: str, actor: str, detail: dict) -> None:
    with psycopg.connect(dsn, autocommit=True) as c:
        c.execute("INSERT INTO core.audit_events (run_id, case_id, event_type, actor, detail) VALUES (%s,%s,%s,%s,%s::jsonb)",
                  (run_id, case_id, event, actor, json.dumps(jsonable(detail))))


def build_graph(d: Deps, checkpointer):
    def retrieve(s: State):
        bundle = mask_obj(gather(d.inv_dsn, s["alert_id"], d.settings.alert_threshold), d.settings.pii_mask_key)  # identifiers never leave the DB boundary in clear
        audit(d.owner_dsn, s["run_id"], s["case_id"], "evidence_retrieved", "system", {"items": len(bundle["items"]), "missing": bundle["missing"]})
        return {"bundle": jsonable(bundle), "attempts": 0, "issues": [], "llm_status": "pending", "tokens": {"in": 0, "out": 0}}

    def analyze(s: State):
        if d.llm is None:
            return {"llm_status": "unavailable", "analysis": None}
        evidence = json.dumps(s["bundle"]["items"], default=str)
        user = f"<evidence_data>\n{evidence}\n</evidence_data>"
        if s.get("issues") and s["attempts"] > 0:
            user += "\nYour previous answer had these problems; fix them (cite valid evidence ids, no unsupported numbers):\n" + json.dumps(s["issues"][-6:])
        try:
            out = d.llm.generate(SYSTEM_ANALYST, user, ANALYSIS_TOOL)
            raw = Analysis.model_validate(out.tool_input)
        except LLMUnavailable as e:
            return {"llm_status": f"unavailable: {e}", "analysis": None}
        except ValidationError as e:
            return {"attempts": s["attempts"] + 1, "analysis": None, "llm_status": "invalid_output",
                    "issues": s["issues"] + [{"kind": "schema_invalid", "detail": str(e)[:200]}]}
        grounded, issues = ground(raw, s["bundle"]["items"], s["bundle"]["prior_count"])
        t = s["tokens"]
        return {"attempts": s["attempts"] + 1, "analysis": grounded.model_dump(), "issues": s["issues"] + issues, "llm_status": "ok",
                "tokens": {"in": t["in"] + out.input_tokens, "out": t["out"] + out.output_tokens}}

    def after_analysis(s: State) -> str:
        a = s.get("analysis")
        usable = a and (a["flag_reasons"] or a["supporting_evidence"])
        dropped = [i for i in s["issues"] if i["kind"] != "confidence_capped"]
        if s["llm_status"].startswith("unavailable"):
            return "draft"
        if usable and not dropped:
            return "draft"
        return "analyze" if s["attempts"] < MAX_ANALYSIS_ATTEMPTS else "draft"

    def draft(s: State):
        b = s["bundle"]
        a: dict[str, Any] | None = s.get("analysis")
        degraded = a is None or not (a["flag_reasons"] or a["supporting_evidence"])
        narrative, questions = None, []
        if not degraded and d.llm is not None:
            try:
                o = d.llm.generate(SYSTEM_DRAFTER, "<analysis>\n" + json.dumps(a) + "\n</analysis>", NARRATIVE_TOOL)
                narrative, questions = o.tool_input.get("summary"), o.tool_input.get("unresolved_questions", [])
            except LLMUnavailable:
                pass
        limitations = ["Synthetic data: not a real customer, account or transaction.", "Risk scores are model/rule outputs, not proof of fraud.",
                       *(["LLM analysis unavailable or rejected by grounding checks; this draft contains database facts and model outputs only."] if degraded else [])]
        report = {
            "case_id": s["case_id"], "alert_id": s["alert_id"], "run_id": s["run_id"], "disclaimer": DISCLAIMER,
            "database_facts": b["facts"],
            "model_outputs": [i for i in b["items"] if i["basis"] == "model_output"],
            "observed_relationships": [i for i in b["items"] if i["basis"] == "observed_relationship"],
            "inferred_patterns": [i for i in b["items"] if i["basis"] == "inferred_suspicious"],
            "timeline": b["timeline"], "relevant_transactions": b["relevant_transactions"],
            "potential_exposure": {"amount_eur": b["exposure"], "basis": "gross sum of alerted transactions on the account within 48h; not a confirmed loss"},
            "agent_analysis": None if (degraded or a is None) else {
                "summary": narrative, "supporting_evidence": a["supporting_evidence"], "contradicting_evidence": a["contradicting_evidence"],
                "flag_reasons": a["flag_reasons"], "hypotheses": [{**h, "label": "agent_hypothesis"} for h in a["hypotheses"]], "confidence": a["confidence"]},
            "suggested_next_steps": (a or {}).get("next_steps") or ["Review the account's recent activity and contact the customer through the bank's normal process (not done by this system)."],
            "missing_information": list(dict.fromkeys(b["missing"] + ((a or {}).get("missing_information") or []))),
            "unresolved_questions": questions, "limitations": limitations,
            "validation_issues": s["issues"], "llm_status": "degraded" if degraded and d.llm else s["llm_status"],
            "evidence_index": [{"id": i["id"], "type": i["type"], "source": i["source"], "basis": i["basis"]} for i in b["items"]],
            "human_decision": None}
        return {"report": jsonable(report)}

    def persist(s: State):
        r = s["report"]
        with psycopg.connect(d.owner_dsn, autocommit=True) as c:
            c.execute("INSERT INTO core.case_reports (run_id, case_id, alert_id, llm_status, report) VALUES (%s,%s,%s,%s,%s::jsonb) "
                      "ON CONFLICT (run_id) DO UPDATE SET report=EXCLUDED.report, llm_status=EXCLUDED.llm_status",
                      (s["run_id"], s["case_id"], s["alert_id"], r["llm_status"], json.dumps(r)))
        audit(d.owner_dsn, s["run_id"], s["case_id"], "report_drafted", "agent" if r["llm_status"] == "ok" else "system",
              {"llm_status": r["llm_status"], "issues": len(s["issues"]), "tokens": s.get("tokens")})
        audit(d.owner_dsn, s["run_id"], s["case_id"], "review_requested", "system", {})
        return {}

    def human_review(s: State):
        answer = interrupt({"awaiting": "analyst decision", "options": list(DECISIONS), "case_id": s["case_id"]})  # pauses; state is checkpointed
        return {"decision": answer}

    def record(s: State):
        dec = s["decision"]
        case_status, report_status = DECISIONS[dec["decision"]]
        with psycopg.connect(d.owner_dsn) as c, c.transaction():  # one atomic unit: case, report and run state change together or not at all
            done = c.execute("UPDATE core.case_reports SET status=%s, decided_at=now(), report = jsonb_set(report, '{human_decision}', %s::jsonb) "
                             "WHERE run_id=%s AND status='pending_review'", (report_status, json.dumps({"decision": dec["decision"], "analyst": dec["analyst"], "note": dec.get("note")}), s["run_id"]))
            if done.rowcount != 1:
                raise RuntimeError("decision rejected: investigation is no longer pending review")
            c.execute("UPDATE core.investigation_cases SET status=%s, closed_at = CASE WHEN %s IN ('closed_fraud','closed_legit') THEN now() END WHERE case_id=%s",
                      (case_status, case_status, s["case_id"]))
            c.execute("UPDATE core.investigation_runs SET state='decided', updated_at=now() WHERE run_id=%s", (s["run_id"],))
        audit(d.owner_dsn, s["run_id"], s["case_id"], "decision_recorded", dec["analyst"], dec)
        return {}

    g = StateGraph(State)
    for name, fn in (("retrieve", retrieve), ("analyze", analyze), ("draft", draft), ("persist", persist), ("human_review", human_review), ("record", record)):
        g.add_node(name, traced(f"investigator.{name}")(fn))
    g.set_entry_point("retrieve")
    g.add_edge("retrieve", "analyze")
    g.add_conditional_edges("analyze", after_analysis, {"analyze": "analyze", "draft": "draft"})
    g.add_edge("draft", "persist")
    g.add_edge("persist", "human_review")
    g.add_edge("human_review", "record")
    g.add_edge("record", END)
    return g.compile(checkpointer=checkpointer)


_ = (Any, log)
