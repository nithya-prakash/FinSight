"use client";
import { ReactNode } from "react";

export function DataState({ loading, error, empty, emptyText = "No data yet.", children, onRetry }: {
  loading: boolean; error: string | null; empty?: boolean; emptyText?: string; children: ReactNode; onRetry?: () => void;
}) {
  if (loading) return <div className="state" role="status" aria-busy="true">Loading…</div>;
  if (error) return (
    <div className="state error" role="alert">
      <strong>Something went wrong.</strong> {error}
      {onRetry && <button onClick={onRetry} className="ghost">Retry</button>}
    </div>
  );
  if (empty) return <div className="state" role="status">{emptyText}</div>;
  return <>{children}</>;
}
