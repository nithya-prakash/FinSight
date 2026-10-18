"use client";
import { useState } from "react";
import { MultiLine, SimpleBar } from "@/components/Charts";
import { DataState } from "@/components/DataState";
import { Badge, Card, Table } from "@/components/ui";
import { eur, int, pct } from "@/lib/format";
import { useApi } from "@/lib/useApi";

export default function Analytics() {
  const [days, setDays] = useState(30);
  const tr = useApi(`analytics/trends?days=${days}`);
  const cat = useApi("analytics/categories");
  const ch = useApi("analytics/channels");
  const fp = useApi("analytics/false-positives");
  const inv = useApi("analytics/investigations");
  return (
    <>
      <h1>Analytics</h1>
      <p className="sub">Fraud figures use <Badge kind="label" /> and alerts are <Badge kind="model" />.</p>
      <div className="row"><label>Date range<select value={days} onChange={(e) => setDays(Number(e.target.value))}>{[7, 14, 30, 60, 90].map((d) => <option key={d} value={d}>Last {d} days of data</option>)}</select></label></div>
      <div className="grid g2">
        <Card title="Daily trend"><DataState loading={tr.loading} error={tr.error} empty={!!tr.data && tr.data.items.length === 0} onRetry={tr.reload}>
          {tr.data && <MultiLine data={tr.data.items} x="day" ys={["transactions", "alerts", "labeled_fraud"]} label="Daily transactions, alerts and labelled fraud" />}</DataState></Card>
        <Card title="Fraud exposure by merchant category"><DataState loading={cat.loading} error={cat.error} empty={!!cat.data && cat.data.items.length === 0} onRetry={cat.reload}>
          {cat.data && <SimpleBar data={cat.data.items} x="category_code" y="fraud_exposure" label="Fraud exposure by category" />}</DataState></Card>
        <Card title="Channels"><DataState loading={ch.loading} error={ch.error} empty={!!ch.data && ch.data.items.length === 0} onRetry={ch.reload}>
          {ch.data && <Table rows={ch.data.items} cols={[{ key: "channel", label: "Channel" }, { key: "transactions", label: "Txns", num: true, render: (r) => int(r.transactions) }, { key: "value", label: "Value", num: true, render: (r) => eur(r.value) },
            { key: "labeled_fraud", label: "Labelled fraud", num: true }, { key: "alerts", label: "Alerts", num: true }]} />}</DataState></Card>
        <Card title="False positives by rule"><DataState loading={fp.loading} error={fp.error} empty={!!fp.data && fp.data.items.length === 0} onRetry={fp.reload}>
          {fp.data && <><Table rows={fp.data.items} cols={[{ key: "rule", label: "Rule" }, { key: "alerts", label: "Alerts", num: true }, { key: "false_positives", label: "False positives", num: true }, { key: "false_positive_share", label: "Share", num: true, render: (r) => pct(r.false_positive_share, 0) }]} /><p className="sub">{fp.data.note}</p></>}</DataState></Card>
        <Card title="Investigation outcomes"><DataState loading={inv.loading} error={inv.error} empty={!!inv.data && inv.data.items.length === 0} emptyText="No investigations yet." onRetry={inv.reload}>
          {inv.data && <><Table rows={inv.data.items} cols={[{ key: "status", label: "Case status" }, { key: "cases", label: "Cases", num: true }]} /><p className="sub">Avg resolution: {inv.data.avg_resolution_hours == null ? "–" : `${inv.data.avg_resolution_hours.toFixed(1)} h`}</p></>}</DataState></Card>
      </div>
    </>
  );
}
