"use client";
import { useState } from "react";
import { DataState } from "@/components/DataState";
import { ResultView } from "@/components/ResultView";
import { Badge, Card } from "@/components/ui";
import { api, Row } from "@/lib/api";
import { useApi } from "@/lib/useApi";

export default function Analyst() {
  const status = useApi("bi/status");
  const demos = useApi("bi/demo");
  const [q, setQ] = useState("");
  const [res, setRes] = useState<Row | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  async function run(call: () => Promise<Row>) {
    setBusy(true); setErr(null);
    try { setRes(await call()); } catch (e) { setErr((e as Error).message); setRes(null); } finally { setBusy(false); }
  }
  return (
    <>
      <h1>AI analyst</h1>
      <p className="sub">Ask in plain language. SQL is validated against an allow-list and runs on a read-only role; identifiers are pseudonymised.</p>
      <DataState loading={status.loading} error={status.error} onRetry={status.reload}>
        {status.data && !status.data.llm_available && <div className="banner" role="alert"><strong>Live LLM unavailable.</strong> {status.data.message} Questions you type cannot be answered; the reference queries below still run.</div>}
      </DataState>
      <Card>
        <form onSubmit={(e) => { e.preventDefault(); if (q.trim()) run(() => api("bi/query", { method: "POST", json: { question: q } })); }}>
          <label>Question<textarea value={q} maxLength={500} onChange={(e) => setQ(e.target.value)} placeholder="Which merchant categories have the highest fraud exposure?" /></label>
          <button type="submit" disabled={busy || !q.trim()} style={{ marginTop: 8 }}>{busy ? "Working…" : "Ask"}</button>
        </form>
        <h2 style={{ marginTop: 14 }}>Reference queries <Badge kind="demo" /></h2>
        <DataState loading={demos.loading} error={demos.error} empty={!!demos.data && demos.data.queries.length === 0} onRetry={demos.reload}>
          <div className="row" style={{ marginTop: 6 }}>{demos.data?.queries.map((d: Row) => <button key={d.id} className="ghost" disabled={busy} onClick={() => run(() => api(`bi/demo/${d.id}`, { method: "POST" }))}>{d.question}</button>)}</div>
        </DataState>
      </Card>
      <div style={{ marginTop: 12 }}>
        {err && <div className="state error" role="alert">{err}</div>}
        {res && <Card title="Result"><ResultView r={res} /></Card>}
      </div>
    </>
  );
}
