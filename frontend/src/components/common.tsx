import type { ReactNode } from "react";
import type { RunStatus } from "../api/types";

export function StatusBadge({ status }: { status: RunStatus }) {
  return (
    <span className={`badge ${status}`} role="status">
      {status === "running" && <span className="spin" aria-hidden="true" />}
      {status}
    </span>
  );
}

export function SeverityTag({ severity }: { severity: string }) {
  const known = severity === "blocker" || severity === "major" || severity === "minor";
  return <span className={`sev ${known ? severity : "minor"}`}>{severity}</span>;
}

export function EmptyState({ children }: { children: ReactNode }) {
  return <div className="empty-state">{children}</div>;
}

export function Panel({
  title,
  children,
  actions,
}: {
  title?: string;
  children: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <section className="panel">
      {(title || actions) && (
        <div className="row" style={{ justifyContent: "space-between", marginBottom: 12 }}>
          {title && <h2 style={{ margin: 0 }}>{title}</h2>}
          {actions}
        </div>
      )}
      {children}
    </section>
  );
}

export function KeyValue({ k, v }: { k: string; v: ReactNode }) {
  return (
    <div className="kv">
      <div className="k">{k}</div>
      <div className="v">{v ?? "—"}</div>
    </div>
  );
}
