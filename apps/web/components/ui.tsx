"use client";
import { ReactNode } from "react";

const KINDS: Record<string, string> = {
  synthetic: "Synthetic data", model: "Model prediction", label: "Ground-truth label (synthetic)",
  decision: "Analyst decision", fact: "Database fact", agent: "Agent hypothesis", estimate: "Estimate", demo: "Reference SQL (no LLM)",
};
export function Badge({ kind, children }: { kind: keyof typeof KINDS | string; children?: ReactNode }) {
  return <span className={`badge ${kind}`} title={KINDS[kind] ?? kind}>{children ?? KINDS[kind] ?? kind}</span>;
}
export function Kpi({ label, value, hint, badge }: { label: string; value: ReactNode; hint?: string; badge?: string }) {
  return (
    <div className="card kpi">
      <div className="kpi-label">{label}{badge && <Badge kind={badge} />}</div>
      <div className="kpi-value">{value}</div>
      {hint && <div className="kpi-hint">{hint}</div>}
    </div>
  );
}
export function Card({ title, children, right }: { title?: string; children: ReactNode; right?: ReactNode }) {
  return <section className="card">{title && <header className="card-h"><h2>{title}</h2>{right}</header>}{children}</section>;
}
export function Table({ cols, rows, onRow, empty = "No rows" }: {
  cols: { key: string; label: string; render?: (r: any) => ReactNode; num?: boolean }[]; rows: any[]; onRow?: (r: any) => void; empty?: string; // eslint-disable-line @typescript-eslint/no-explicit-any
}) {
  if (!rows.length) return <div className="state">{empty}</div>;
  return (
    <div className="table-wrap">
      <table>
        <thead><tr>{cols.map((c) => <th key={c.key} className={c.num ? "num" : ""} scope="col">{c.label}</th>)}</tr></thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i} onClick={onRow ? () => onRow(r) : undefined} className={onRow ? "click" : ""} tabIndex={onRow ? 0 : undefined}
                onKeyDown={onRow ? (e) => { if (e.key === "Enter") onRow(r); } : undefined}>
              {cols.map((c) => <td key={c.key} className={c.num ? "num" : ""}>{c.render ? c.render(r) : String(r[c.key] ?? "–")}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
export function Pager({ total, limit, offset, onChange }: { total: number; limit: number; offset: number; onChange: (o: number) => void }) {
  return (
    <div className="pager">
      <button className="ghost" disabled={offset === 0} onClick={() => onChange(Math.max(0, offset - limit))}>← Prev</button>
      <span>{total === 0 ? 0 : offset + 1}–{Math.min(offset + limit, total)} of {total}</span>
      <button className="ghost" disabled={offset + limit >= total} onClick={() => onChange(offset + limit)}>Next →</button>
    </div>
  );
}
