"""Grounding checks: drop claims that lack valid evidence citations or state numbers absent from the cited evidence."""
import json
import re

from .models import Analysis, Claim

_NUM = re.compile(r"\d[\d,]*\.?\d*")


def _numbers(text: str) -> set[float]:
    out = set()
    for m in _NUM.findall(text):
        try:
            out.add(float(m.replace(",", "").rstrip(".")))
        except ValueError:
            pass
    return out


def ungrounded_numbers(claim_text: str, cited: list[dict], threshold: float = 10) -> list[str]:
    ev_text = json.dumps(cited, default=str)
    ev_nums = _numbers(ev_text)
    bad = []
    for m in _NUM.findall(claim_text):
        raw = m.replace(",", "").rstrip(".")
        try:
            v = float(raw)
        except ValueError:
            continue
        if v < threshold and "." not in raw:
            continue
        if raw in ev_text or any(abs(v - e) <= max(0.011, abs(e) * 1e-6) or round(e) == v for e in ev_nums):
            continue
        bad.append(m)
    return bad


def ground(analysis: Analysis, items: list[dict], prior_count: int) -> tuple[Analysis, list[dict]]:
    by_id = {i["id"]: i for i in items}
    issues: list[dict] = []

    def keep(claims: list[Claim], kind: str) -> list[Claim]:
        ok = []
        for c in claims:
            if not c.evidence_ids:
                issues.append({"kind": "no_citation", "section": kind, "text": c.text[:120]})
            elif any(e not in by_id for e in c.evidence_ids):
                issues.append({"kind": "unknown_evidence_id", "section": kind, "text": c.text[:120], "ids": c.evidence_ids})
            else:
                bad = ungrounded_numbers(c.text, [by_id[e] for e in c.evidence_ids])
                if bad:
                    issues.append({"kind": "ungrounded_number", "section": kind, "text": c.text[:120], "numbers": bad})
                else:
                    ok.append(c)
        return ok

    out = Analysis(flag_reasons=keep(analysis.flag_reasons, "flag_reasons"), supporting_evidence=keep(analysis.supporting_evidence, "supporting"),
                   contradicting_evidence=keep(analysis.contradicting_evidence, "contradicting"), hypotheses=keep(analysis.hypotheses, "hypotheses"),
                   confidence=analysis.confidence, missing_information=analysis.missing_information[:10], next_steps=analysis.next_steps[:10])
    if prior_count < 5 and out.confidence != "low":  # thin history caps confidence regardless of what the model said
        issues.append({"kind": "confidence_capped", "from": out.confidence, "to": "low", "reason": "fewer than 5 prior transactions"})
        out.confidence = "low"
    return out, issues
