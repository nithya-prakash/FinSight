"use client";
import { useState } from "react";
import { DataState } from "@/components/DataState";
import { Badge, Card, Pager, Table } from "@/components/ui";
import { eur, when } from "@/lib/format";
import { useApi } from "@/lib/useApi";

const LIMIT = 25;
export default function Transactions() {
  const [f, setF] = useState({ account_id: "", customer_id: "", min_risk: "", since: "" });
  const [applied, setApplied] = useState(f);
  const [offset, setOffset] = useState(0);
  const qs = new URLSearchParams({ limit: String(LIMIT), offset: String(offset) });
  Object.entries(applied).forEach(([k, v]) => v && qs.set(k, v));
  const { data, error, loading, reload } = useApi(`transactions?${qs}`);
  return (
    <>
      <h1>Live transactions</h1>
      <p className="sub">Streamed through Redpanda and scored on arrival. <Badge kind="synthetic" /> <Badge kind="model">Risk score = model prediction</Badge></p>
      <form className="row" onSubmit={(e) => { e.preventDefault(); setOffset(0); setApplied(f); }} aria-label="Transaction filters">
        <label>Account<input value={f.account_id} onChange={(e) => setF({ ...f, account_id: e.target.value })} placeholder="id_… (pseudonym)" /></label>
        <label>Customer<input value={f.customer_id} onChange={(e) => setF({ ...f, customer_id: e.target.value })} placeholder="id_… (pseudonym)" /></label>
        <label>Min risk (0–1)<input type="number" step="0.05" min="0" max="1" value={f.min_risk} onChange={(e) => setF({ ...f, min_risk: e.target.value })} /></label>
        <label>Since<input type="date" value={f.since} onChange={(e) => setF({ ...f, since: e.target.value })} /></label>
        <button type="submit">Apply</button>
        <button type="button" className="ghost" onClick={() => { const z = { account_id: "", customer_id: "", min_risk: "", since: "" }; setF(z); setApplied(z); setOffset(0); }}>Clear</button>
      </form>
      <Card>
        <DataState loading={loading} error={error} empty={!!data && data.items.length === 0} emptyText="No transactions match these filters." onRetry={reload}>
          {data && (
            <>
              <Table rows={data.items} cols={[
                { key: "event_time", label: "Event time", render: (r) => when(r.event_time) }, { key: "transaction_id", label: "Transaction" }, { key: "account_id", label: "Account" },
                { key: "amount", label: "Amount", num: true, render: (r) => eur(r.amount) }, { key: "channel", label: "Channel" },
                { key: "risk_score", label: "Risk", render: (r) => r.risk_score == null ? "–" : <><span className="bar" style={{ width: 60 * r.risk_score }} /> {Number(r.risk_score).toFixed(2)}</> },
                { key: "signals", label: "Rules triggered", render: (r) => (r.signals ?? []).map((s: { rule: string }) => s.rule).join(", ") || "–" },
                { key: "model_version", label: "Model" }, { key: "status", label: "Status" }]} />
              <Pager total={data.total} limit={LIMIT} offset={offset} onChange={setOffset} />
            </>
          )}
        </DataState>
      </Card>
    </>
  );
}
