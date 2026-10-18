"use client";
import { DataState } from "@/components/DataState";
import { Badge, Card, Kpi, Table } from "@/components/ui";
import { eur, int, pct, when } from "@/lib/format";
import { useApi } from "@/lib/useApi";

export default function Overview() {
  const o = useApi("analytics/overview");
  const p = useApi("pipeline/stats");
  const m = useApi("models");
  const d = o.data;
  return (
    <>
      <h1>Overview</h1>
      <p className="sub">All figures come from the live API. Data is <Badge kind="synthetic" />; it does not reflect real banking fraud.</p>
      <DataState loading={o.loading} error={o.error} empty={!!d && d.transactions === 0} emptyText="No transactions ingested yet. Run the producer to stream events." onRetry={o.reload}>
        {d && (
          <>
            <div className="grid g3">
              <Kpi label="Transactions" value={int(d.transactions)} hint={`Last event ${when(d.last_event)}`} />
              <Kpi label="Value" value={eur(d.value)} />
              <Kpi label="Fraud alerts" value={int(d.alerts)} hint={`${d.high} high · ${d.medium} medium · ${d.low} low`} badge="model" />
              <Kpi label="Fraud rate (labelled)" value={pct(d.confirmed_fraud_rate, 2)} hint={`${int(d.labeled_fraud)} of ${int(d.labeled)} labelled`} badge="label" />
              <Kpi label="Estimated exposure" value={eur(d.estimated_exposure)} hint="Gross value of labelled-fraud transactions" badge="label" />
              <Kpi label="Flagged rate" value={pct(d.flagged_rate, 2)} hint="Alerts ÷ all transactions. Not the fraud rate." badge="model" />
            </div>
            <div className="grid g2" style={{ marginTop: 12 }}>
              <Card title="Recent alerts">
                <Table rows={d.recent_alerts} empty="No alerts yet" cols={[
                  { key: "alert_id", label: "Alert" }, { key: "severity", label: "Severity", render: (r) => <Badge kind={r.severity}>{r.severity}</Badge> },
                  { key: "account_id", label: "Account" }, { key: "amount", label: "Amount", num: true, render: (r) => eur(r.amount) }, { key: "created_at", label: "Created", render: (r) => when(r.created_at) }]} />
              </Card>
              <Card title="Pipeline & model health">
                <DataState loading={p.loading || m.loading} error={p.error || m.error} onRetry={() => { p.reload(); m.reload(); }}>
                  {p.data && m.data && (
                    <ul className="plain">
                      <li>Events processed: <strong>{int(p.data.processed)}</strong> · dead letters: <strong>{int(p.data.dead_letters)}</strong></li>
                      <li>Average scoring time: <strong>{p.data.avg_scoring_ms ?? "–"} ms</strong></li>
                      <li>Scoring mode: <strong>{m.data.scoring_mode}</strong> {m.data.registry.available ? `· approved model v${m.data.registry.approved ?? "none"}` : "· model registry not connected"}</li>
                      <li>Last drift check: {m.data.monitoring_runs[0] ? `${m.data.monitoring_runs[0].feature_drift ? "feature drift flagged" : "no feature drift"} (${when(m.data.monitoring_runs[0].created_at)})` : "none yet"}</li>
                    </ul>
                  )}
                </DataState>
              </Card>
            </div>
          </>
        )}
      </DataState>
    </>
  );
}
