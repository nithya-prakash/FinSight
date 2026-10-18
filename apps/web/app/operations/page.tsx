"use client";
import { DataState } from "@/components/DataState";
import { Badge, Card, Kpi, Table } from "@/components/ui";
import { int, pct } from "@/lib/format";
import { useApi } from "@/lib/useApi";

export default function Operations() {
  const { data: o, error, loading, reload } = useApi("observability/summary");
  return (
    <>
      <h1>Pipeline & observability</h1>
      <p className="sub">Costs are <Badge kind="estimate" /> from versioned pricing metadata, not billed amounts.</p>
      <DataState loading={loading} error={error} onRetry={reload}>
        {o && <>
          <Card title="Services"><div className="row">{Object.entries(o.services).map(([k, v]) => <span key={k}><strong>{k}</strong>: <Badge kind={v === "ok" || v === "configured" || v === "otlp" ? "ok" : "medium"}>{String(v)}</Badge></span>)}</div></Card>
          <div className="grid g3" style={{ marginTop: 12 }}>
            <Kpi label="Events processed" value={int(o.pipeline.processed)} />
            <Kpi label="Throughput (last 5 min)" value={`${o.pipeline.throughput_per_min_last_5m}/min`} />
            <Kpi label="Seconds since last event" value={o.pipeline.seconds_since_last_event == null ? "–" : int(Math.round(o.pipeline.seconds_since_last_event))} hint="Data freshness (wall clock)" />
            <Kpi label="Dead letters" value={int(o.pipeline.dead_letters)} />
            <Kpi label="Avg scoring time" value={`${o.pipeline.avg_scoring_ms ?? "–"} ms`} />
            <Kpi label="Redeliveries absorbed" value={int(o.pipeline.redeliveries)} hint="Duplicates ignored idempotently" />
          </div>
          <div className="grid g2" style={{ marginTop: 12 }}>
            <Card title="AI analyst usage">
              <Table rows={o.bi.by_mode} empty="No analyst requests yet" cols={[{ key: "mode", label: "Mode" }, { key: "requests", label: "Requests", num: true }, { key: "answered", label: "Answered", num: true }, { key: "failed", label: "Failed", num: true },
                { key: "llm_unavailable", label: "LLM unavailable", num: true }, { key: "avg_llm_ms", label: "Avg LLM ms", num: true, render: (r) => Math.round(r.avg_llm_ms) }, { key: "retries", label: "Retries", num: true }]} />
              <p className="sub">Estimated cost: ${o.bi.estimated_cost.estimated_usd} ({o.bi.estimated_cost.note})</p>
            </Card>
            <Card title="Investigator activity">
              <Table rows={o.agent_events} empty="No agent events yet" cols={[{ key: "event_type", label: "Event" }, { key: "n", label: "Count", num: true }]} />
            </Card>
          </div>
          <Card title="Evaluation history">
            <Table rows={o.evaluations} empty="No evaluation reports yet. Run: finsight eval text2sql" cols={[{ key: "file", label: "Report" }, { key: "mode", label: "Mode" }, { key: "llm", label: "LLM", render: (r) => r.llm ?? "none" },
              { key: "passed", label: "Passed", num: true, render: (r) => `${r.passed}/${r.evaluated}` }, { key: "accuracy", label: "Accuracy", num: true, render: (r) => pct(r.accuracy, 0) }, { key: "not_evaluated", label: "Not evaluated", num: true }]} />
          </Card>
        </>}
      </DataState>
    </>
  );
}
