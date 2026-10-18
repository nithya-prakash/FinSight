"use client";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { DataState } from "@/components/DataState";
import { Badge, Card, Pager, Table } from "@/components/ui";
import { api } from "@/lib/api";
import { eur, when } from "@/lib/format";
import { useApi } from "@/lib/useApi";

const LIMIT = 20;
export default function Alerts() {
  const [sev, setSev] = useState("");
  const [offset, setOffset] = useState(0);
  const [sel, setSel] = useState<number | null>(null);
  const router = useRouter();
  const list = useApi(`alerts?limit=${LIMIT}&offset=${offset}${sev ? `&severity=${sev}` : ""}`);
  const detail = useApi(sel ? `alerts/${sel}` : null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  async function investigate(id: number) {
    setBusy(true); setErr(null);
    try { const r = await api("investigations", { method: "POST", json: { alert_id: id } }); router.push(`/investigations?run=${r.run_id}`); }
    catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  }
  return (
    <>
      <h1>Fraud alerts</h1>
      <p className="sub">Alerts are <Badge kind="model" /> outputs, not confirmed fraud.</p>
      <div className="row"><label>Severity<select value={sev} onChange={(e) => { setSev(e.target.value); setOffset(0); }}><option value="">All</option><option>high</option><option>medium</option><option>low</option></select></label></div>
      <div className="split">
        <Card>
          <DataState loading={list.loading} error={list.error} empty={!!list.data && list.data.items.length === 0} emptyText="No alerts for this filter." onRetry={list.reload}>
            {list.data && <>
              <Table onRow={(r) => setSel(r.alert_id)} rows={list.data.items} cols={[
                { key: "alert_id", label: "#" }, { key: "severity", label: "Severity", render: (r) => <Badge kind={r.severity}>{r.severity}</Badge> },
                { key: "amount", label: "Amount", num: true, render: (r) => eur(r.amount) }, { key: "risk_score", label: "Score", num: true, render: (r) => Number(r.risk_score).toFixed(2) }]} />
              <Pager total={list.data.total} limit={LIMIT} offset={offset} onChange={setOffset} />
            </>}
          </DataState>
        </Card>
        <Card title={sel ? `Alert ${sel}` : "Evidence"}>
          {!sel ? <div className="state">Select an alert to inspect its evidence.</div> : (
            <DataState loading={detail.loading} error={detail.error} onRetry={detail.reload}>
              {detail.data && <>
                <p><Badge kind={detail.data.severity}>{detail.data.severity}</Badge> <Badge kind="model">{detail.data.model_version} · score {Number(detail.data.risk_score).toFixed(3)}</Badge></p>
                <h2>Triggered signals</h2>
                <ul className="plain">{(detail.data.signals ?? []).map((s: { rule: string; score: number; explanation: string }, i: number) => <li key={i}><strong>{s.rule}</strong> ({Number(s.score).toFixed(2)}): {s.explanation}</li>)}</ul>
                <h2 style={{ marginTop: 10 }}>Transaction <Badge kind="fact" /></h2>
                <pre>{JSON.stringify({ id: detail.data.transaction.transaction_id, time: when(detail.data.transaction.event_time), account: detail.data.transaction.account_id, amount: detail.data.transaction.amount, channel: detail.data.transaction.channel, type: detail.data.transaction.transaction_type, ip_region: detail.data.transaction.ip_region }, null, 2)}</pre>
                {err && <div role="alert" className="state error">{err}</div>}
                <button disabled={busy} onClick={() => investigate(sel)}>{busy ? "Starting…" : "Open investigation"}</button>
              </>}
            </DataState>
          )}
        </Card>
      </div>
    </>
  );
}
