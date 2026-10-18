"use client";
import { useEffect, useState } from "react";
import { Badge, Table } from "@/components/ui";
import { api, Row } from "@/lib/api";
import { eur, when } from "@/lib/format";

const DECISIONS = [["confirm_fraud", "Confirm fraud"], ["close_legit", "Close as legitimate"], ["escalate", "Escalate"], ["need_more_info", "Need more information"]];

export function DecisionForm({ run, onDone }: { run: Row; onDone: () => void }) {
  const [who, setWho] = useState<string | null>(null);
  useEffect(() => { api("auth/whoami").then((w) => setWho(`${w.identity} (${w.role})`)).catch(() => setWho(null)); }, []);
  const [decision, setDecision] = useState("");
  const [note, setNote] = useState("");
  const [confirm, setConfirm] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  if (run.status !== "pending_review") return <p><Badge kind="decision">Decided: {run.report.human_decision?.decision} by {run.report.human_decision?.analyst}</Badge></p>;
  const ready = decision !== "";
  async function submit() {
    setBusy(true); setErr(null);
    try { await api(`investigations/${run.run_id}/decision`, { method: "POST", json: { decision, note } }); onDone(); }
    catch (e) { setErr((e as Error).message); } finally { setBusy(false); setConfirm(false); }
  }
  return (
    <form onSubmit={(e) => { e.preventDefault(); if (ready) { if (!confirm) setConfirm(true); else submit(); } }} aria-label="Analyst decision">
      <p className="sub">No account action is taken by FinSight. The decision is recorded under the identity of the authenticated server key: <strong>{who ?? "unknown"}</strong> (the dashboard has no per-user login).</p>
      <div className="row">
        <label>Decision<select value={decision} onChange={(e) => { setDecision(e.target.value); setConfirm(false); }}><option value="">Select…</option>{DECISIONS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></label>
      </div>
      <label>Note<textarea value={note} maxLength={500} onChange={(e) => setNote(e.target.value)} /></label>
      {err && <div role="alert" className="state error">{err}</div>}
      <button type="submit" disabled={!ready || busy} className={confirm ? "danger" : ""} style={{ marginTop: 8 }}>{confirm ? "Click again to confirm decision" : "Record decision"}</button>
    </form>
  );
}

function Claims({ items }: { items?: Row[] }) {
  if (!items?.length) return <p className="sub">None reported.</p>;
  return <ul className="plain">{items.map((c, i) => <li key={i}>{c.text} <small>[{c.evidence_ids.join(", ")}]</small></li>)}</ul>;
}

export function Report({ run, reload }: { run: Row; reload: () => void }) {
  const r = run.report;
  const a = r.agent_analysis;
  return (
    <>
      <p><Badge kind={run.status === "pending_review" ? "medium" : "decision"}>{run.status}</Badge> case #{run.case_id} · alert #{run.alert_id} · LLM: {r.llm_status}</p>
      <div className="banner">{r.disclaimer}</div>
      <h2>Database facts <Badge kind="fact" /></h2>
      <ul className="plain">{r.database_facts.map((f: Row, i: number) => <li key={i}>{f.statement} <small>[{f.evidence_ids.join(", ")}]</small></li>)}</ul>
      <h2 style={{ marginTop: 10 }}>Model outputs <Badge kind="model" /></h2>
      <ul className="plain">{r.model_outputs.map((m: Row) => <li key={m.id}>{m.type}: <code>{JSON.stringify(m.data).slice(0, 220)}</code> <small>[{m.id}]</small></li>)}</ul>
      <h2 style={{ marginTop: 10 }}>Relationships</h2>
      <ul className="plain">{r.observed_relationships.map((m: Row) => <li key={m.id}>Observed: {m.type} <small>[{m.id}]</small></li>)}{r.inferred_patterns.map((m: Row) => <li key={m.id}><Badge kind="agent">Inferred</Badge> {m.data.pattern}: {m.data.caveat}</li>)}</ul>
      <h2 style={{ marginTop: 10 }}>Potential exposure</h2>
      <p>{eur(r.potential_exposure.amount_eur)} <span className="sub">({r.potential_exposure.basis})</span></p>
      <h2>Evidence timeline</h2>
      <Table rows={r.timeline} cols={[{ key: "time", label: "Time", render: (x) => when(x.time) }, { key: "event", label: "Event" }, { key: "evidence_ids", label: "Evidence", render: (x) => x.evidence_ids.join(", ") }]} />
      {a ? <>
        <h2 style={{ marginTop: 10 }}>Agent analysis <Badge kind="agent" /> (confidence: {a.confidence})</h2>
        {a.summary && <p>{a.summary}</p>}
        <h2>Supporting evidence</h2><Claims items={a.supporting_evidence} />
        <h2>Contradicting / benign explanations</h2><Claims items={a.contradicting_evidence} />
        <h2>Hypotheses</h2><Claims items={a.hypotheses} />
      </> : <div className="state">No agent analysis: {r.llm_status === "degraded" ? "the model output failed grounding checks" : "LLM not available"}. The sections above contain only database facts and model outputs.</div>}
      <h2>Suggested next steps</h2><ul className="plain">{r.suggested_next_steps.map((s: string, i: number) => <li key={i}>{s}</li>)}</ul>
      <h2 style={{ marginTop: 10 }}>Missing information & limitations</h2>
      <ul className="plain">{[...r.missing_information, ...r.limitations].map((s: string, i: number) => <li key={i}>{s}</li>)}</ul>
      {r.validation_issues.length > 0 && <details><summary>{r.validation_issues.length} grounding issue(s) detected</summary><pre>{JSON.stringify(r.validation_issues, null, 2)}</pre></details>}
      <h2 style={{ marginTop: 14 }}>Analyst review</h2>
      <DecisionForm run={run} onDone={reload} />
    </>
  );
}

