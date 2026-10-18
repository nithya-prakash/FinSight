"use client";
import { DataState } from "@/components/DataState";
import { Badge, Card, Kpi, Table } from "@/components/ui";
import { when } from "@/lib/format";
import { useApi } from "@/lib/useApi";

export default function Models() {
  const { data: m, error, loading, reload } = useApi("models");
  return (
    <>
      <h1>Model operations</h1>
      <p className="sub">Candidates never go live automatically: promotion needs a passing evaluation and a named human approver (CLI). <Badge kind="synthetic" /></p>
      <DataState loading={loading} error={error} onRetry={reload}>
        {m && <>
          <div className="grid g3">
            <Kpi label="Scoring mode" value={m.scoring_mode} hint={m.scoring_mode === "rules" ? "Rules-only baseline" : "Approved registry model, rules-only fallback"} />
            <Kpi label="Approved model" value={m.registry.available ? (m.registry.approved ? `v${m.registry.approved}` : "none") : "registry offline"} />
            <Kpi label="Rules alert threshold" value={m.alert_threshold_rules} hint={m.ml_threshold_override ? `ML override ${m.ml_threshold_override}` : "ML uses its validation-tuned threshold"} />
          </div>
          <div className="grid g2" style={{ marginTop: 12 }}>
            <Card title="Registered versions">
              {!m.registry.available ? <div className="state">Model registry not connected{m.registry.error ? ` (${m.registry.error})` : ""}. Start MLflow with the <code>ml</code> profile.</div> :
                <Table rows={m.registry.versions} empty="No versions registered" cols={[{ key: "version", label: "Ver" }, { key: "aliases", label: "Aliases", render: (r) => r.aliases.join(", ") || "–" },
                  { key: "acceptance", label: "Evaluation", render: (r) => <Badge kind={r.acceptance === "pass" ? "ok" : "rejected"}>{r.acceptance ?? "unevaluated"}</Badge> },
                  { key: "threshold", label: "Threshold", num: true, render: (r) => r.threshold ? Number(r.threshold).toFixed(3) : "–" }, { key: "val_pr_auc", label: "Val PR-AUC", num: true, render: (r) => r.val_pr_auc ? Number(r.val_pr_auc).toFixed(3) : "–" }]} />}
            </Card>
            <Card title="Drift & monitoring">
              <Table rows={m.monitoring_runs} empty="No monitoring runs yet" cols={[{ key: "created_at", label: "When", render: (r) => when(r.created_at) }, { key: "model_version", label: "Model" },
                { key: "feature_drift", label: "Feature drift", render: (r) => (r.feature_drift ? "yes" : "no") }, { key: "prediction_drift", label: "Prediction drift", render: (r) => (r.prediction_drift ? "yes" : "no") },
                { key: "performance_degraded", label: "Performance", render: (r) => (r.performance_degraded == null ? "no labels" : r.performance_degraded ? "degraded" : "ok") }]} />
              <p className="sub">Drift alone does not show the model is inaccurate; only labelled performance does.</p>
            </Card>
          </div>
          <Card title="Promotion & rollback history">
            <Table rows={m.events} empty="No model events" cols={[{ key: "created_at", label: "When", render: (r) => when(r.created_at) }, { key: "event_type", label: "Event" }, { key: "model_version", label: "Model" }, { key: "actor", label: "Actor" }]} />
          </Card>
        </>}
      </DataState>
    </>
  );
}
