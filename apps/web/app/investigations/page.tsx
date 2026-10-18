"use client";
import { useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { Report } from "@/components/Report";
import { DataState } from "@/components/DataState";
import { Card, Table } from "@/components/ui";
import { eur } from "@/lib/format";
import { useApi } from "@/lib/useApi";

function Inner() {
  const params = useSearchParams();
  const [sel, setSel] = useState<string | null>(params.get("run"));
  const list = useApi("investigations");
  const run = useApi(sel ? `investigations/${sel}` : null);
  return (
    <>
      <h1>Investigation center</h1>
      <p className="sub">Evidence-grounded drafts. Facts, model outputs, agent hypotheses and human decisions are kept separate.</p>
      <div className="split">
        <Card title="Investigations">
          <DataState loading={list.loading} error={list.error} empty={!!list.data && list.data.length === 0} emptyText="None yet. Open one from the Alerts page." onRetry={list.reload}>
            {list.data && <Table onRow={(r) => setSel(r.run_id)} rows={list.data} cols={[{ key: "case_id", label: "Case" }, { key: "alert_id", label: "Alert" }, { key: "status", label: "Status" }, { key: "exposure_eur", label: "Exposure", num: true, render: (r) => eur(r.exposure_eur) }]} />}
          </DataState>
        </Card>
        <Card title="Report">
          {!sel ? <div className="state">Select an investigation.</div> : (
            <DataState loading={run.loading} error={run.error} onRetry={run.reload}>{run.data && <Report run={run.data} reload={() => { run.reload(); list.reload(); }} />}</DataState>
          )}
        </Card>
      </div>
    </>
  );
}
export default function Investigations() { return <Suspense fallback={<div className="state">Loading…</div>}><Inner /></Suspense>; }
