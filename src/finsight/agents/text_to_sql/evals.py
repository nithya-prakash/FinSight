"""Evaluation harness: machine-readable JSON + human summary, with denominators and failure categories."""
import json
from collections import Counter, defaultdict
from pathlib import Path

import yaml

from .agent import BIAgent

GOLDEN = Path(__file__).resolve().parents[4] / "evals" / "text_to_sql" / "golden.yaml"


def normalize(rows: list[list], ordered: bool):
    def cell(v):
        return round(v, 6) if isinstance(v, float) else v
    out = [tuple(cell(v) for v in r) for r in rows]
    return out if ordered else sorted(out, key=repr)


def run_eval(agent: BIAgent, mode: str = "reference", golden_path: Path = GOLDEN) -> dict:
    cases = yaml.safe_load(golden_path.read_text())["cases"]
    per_cat: dict = defaultdict(lambda: {"total": 0, "evaluated": 0, "passed": 0, "not_evaluated": 0})
    failures: list[dict] = []
    fail_kinds: Counter[str] = Counter()
    for c in cases:
        cat = per_cat[c["category"]]
        cat["total"] += 1
        if "reference_sql" in c:
            ref = agent.run_reference(c["reference_sql"], c["question"])
            if ref.status != "answered":
                failures.append({"id": c["id"], "kind": "reference_failed", "detail": ref.message})
                fail_kinds["reference_failed"] += 1
                cat["evaluated"] += 1
                continue
            if mode == "reference":
                cat["evaluated"] += 1
                cat["passed"] += 1
                continue
            if agent.llm is None:
                cat["not_evaluated"] += 1
                continue
            got = agent.ask(c["question"])
            cat["evaluated"] += 1
            if got.status != "answered":
                kind = f"status_{got.status}"
            elif normalize(got.rows, c.get("ordered", False)) != normalize(ref.rows, c.get("ordered", False)):
                kind = "wrong_result"
            else:
                cat["passed"] += 1
                continue
            failures.append({"id": c["id"], "kind": kind, "sql": got.sql, "detail": got.message})
            fail_kinds[kind] += 1
        else:  # needs a live LLM
            if agent.llm is None or mode == "reference":
                cat["not_evaluated"] += 1
                continue
            got = agent.ask(c["question"])
            cat["evaluated"] += 1
            exp = c["expect"]
            ok = ((exp == "clarify" and got.status == "needs_clarification")
                  or (exp == "unanswerable" and got.status in ("unanswerable", "needs_clarification"))
                  or (exp == "no_restricted_data" and (got.status != "answered" or not any(
                      isinstance(v, str) and v.startswith(("acc_", "cus_")) for r in got.rows for v in r))))
            if ok:
                cat["passed"] += 1
            else:
                failures.append({"id": c["id"], "kind": f"expected_{exp}_got_{got.status}", "detail": got.message})
                fail_kinds[f"expected_{exp}"] += 1
    ev = sum(v["evaluated"] for v in per_cat.values())
    ps = sum(v["passed"] for v in per_cat.values())
    return {"mode": mode, "llm": agent.llm.model if agent.llm else None, "categories": per_cat,
            "evaluated": ev, "passed": ps, "accuracy": (ps / ev) if ev else None,
            "not_evaluated": sum(v["not_evaluated"] for v in per_cat.values()),
            "failure_kinds": dict(fail_kinds), "failures": failures,
            "note": "mode=reference validates the golden set and pipeline; it is NOT a measure of LLM accuracy."
            if mode == "reference" else "accuracy = execution accuracy of LLM-generated SQL vs reference result sets"}


def summarize(r: dict) -> str:
    lines = [f"Text-to-SQL eval (mode={r['mode']}, llm={r['llm']})", r["note"], ""]
    for k, v in r["categories"].items():
        lines.append(f"  {k:20s} {v['passed']}/{v['evaluated']} passed, {v['not_evaluated']} not evaluated (of {v['total']})")
    acc = "n/a" if r["accuracy"] is None else f"{r['accuracy']:.1%}"
    lines.append(f"\nTotal: {r['passed']}/{r['evaluated']} = {acc}; not evaluated: {r['not_evaluated']}")
    if r["failures"]:
        lines.append("Failures: " + json.dumps(r["failure_kinds"]))
    return "\n".join(lines)
