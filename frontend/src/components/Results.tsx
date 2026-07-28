import { useMemo, useState } from "react";
import type {
  DesignArtifact,
  SummarySchema,
} from "../api/types";
import { EmptyState, SeverityTag } from "./common";

type Tab =
  | "requirements"
  | "business_rules"
  | "scenarios"
  | "test_cases"
  | "gaps"
  | "coverage";

const TAB_LABELS: Record<Tab, string> = {
  requirements: "Requirements",
  business_rules: "Business Rules",
  scenarios: "Scenarios",
  test_cases: "Test Cases",
  gaps: "Gap Analysis",
  coverage: "Coverage",
};

const TAB_ORDER: Tab[] = [
  "requirements",
  "business_rules",
  "scenarios",
  "test_cases",
  "gaps",
  "coverage",
];

export function Results({
  artifact,
  summary,
}: {
  artifact: DesignArtifact;
  summary: SummarySchema | null;
}) {
  const [tab, setTab] = useState<Tab>("requirements");

  return (
    <div>
      <div className="tabs" role="tablist" aria-label="Result categories">
        {TAB_ORDER.map((t) => (
          <button
            key={t}
            role="tab"
            aria-selected={tab === t}
            className={`tab ${tab === t ? "active" : ""}`}
            onClick={() => setTab(t)}
          >
            {TAB_LABELS[t]}
          </button>
        ))}
      </div>

      <div role="tabpanel">
        {tab === "requirements" && <RequirementsTable artifact={artifact} />}
        {tab === "business_rules" && <BusinessRulesTable artifact={artifact} />}
        {tab === "scenarios" && <ScenariosTable artifact={artifact} />}
        {tab === "test_cases" && <TestCasesTable artifact={artifact} />}
        {tab === "gaps" && <GapsTable artifact={artifact} />}
        {tab === "coverage" && (
          <CoverageView artifact={artifact} summary={summary} />
        )}
      </div>
    </div>
  );
}

function useSearch<T>(rows: T[], keys: (row: T) => string): [string, (v: string) => void, T[]] {
  const [query, setQuery] = useState("");
  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return rows;
    return rows.filter((r) => keys(r).toLowerCase().includes(q));
  }, [rows, query, keys]);
  return [query, setQuery, filtered];
}

function SearchBox({
  value,
  onChange,
  label,
}: {
  value: string;
  onChange: (v: string) => void;
  label: string;
}) {
  return (
    <input
      className="search-input"
      type="search"
      placeholder={label}
      aria-label={label}
      value={value}
      onChange={(e) => onChange(e.target.value)}
    />
  );
}

function RequirementsTable({ artifact }: { artifact: DesignArtifact }) {
  const [q, setQ, rows] = useSearch(
    artifact.requirements,
    (r) => `${r.id} ${r.title} ${r.description} ${r.actors.join(" ")}`,
  );
  if (artifact.requirements.length === 0)
    return <EmptyState>No requirements were produced.</EmptyState>;
  return (
    <>
      <SearchBox value={q} onChange={setQ} label="Search requirements" />
      <div className="table-wrap">
        <table className="data">
          <thead>
            <tr>
              <th>ID</th>
              <th>Title</th>
              <th>Description</th>
              <th>Actors</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.id}>
                <td className="cell-mono">{r.id}</td>
                <td>{r.title}</td>
                <td className="cell-wide">{r.description}</td>
                <td>{r.actors.join(", ")}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

function BusinessRulesTable({ artifact }: { artifact: DesignArtifact }) {
  const rows = artifact.business_rules;
  if (rows.length === 0) return <EmptyState>No business rules were produced.</EmptyState>;
  return (
    <div className="table-wrap">
      <table className="data">
        <thead>
          <tr>
            <th>ID</th>
            <th>Requirement</th>
            <th>Rule</th>
            <th>Source excerpt</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.id}>
              <td className="cell-mono">{r.id}</td>
              <td className="cell-mono">{r.requirement_id}</td>
              <td className="cell-wide">{r.rule}</td>
              <td className="muted">{r.source_excerpt}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ScenariosTable({ artifact }: { artifact: DesignArtifact }) {
  const [q, setQ, rows] = useSearch(
    artifact.scenarios,
    (s) => `${s.id} ${s.title} ${s.description} ${s.category}`,
  );
  if (artifact.scenarios.length === 0)
    return <EmptyState>No scenarios were produced.</EmptyState>;
  return (
    <>
      <SearchBox value={q} onChange={setQ} label="Search scenarios" />
      <div className="table-wrap">
        <table className="data">
          <thead>
            <tr>
              <th>ID</th>
              <th>Title</th>
              <th>Category</th>
              <th>Description</th>
              <th>Requirements</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((s) => (
              <tr key={s.id}>
                <td className="cell-mono">{s.id}</td>
                <td>{s.title}</td>
                <td className="cell-mono">{s.category}</td>
                <td className="cell-wide">{s.description}</td>
                <td className="cell-mono">{s.requirement_ids.join(", ")}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

function TestCasesTable({ artifact }: { artifact: DesignArtifact }) {
  const [q, setQ, rows] = useSearch(
    artifact.test_cases,
    (t) => `${t.id} ${t.title} ${t.objective} ${t.priority} ${t.test_type}`,
  );
  if (artifact.test_cases.length === 0)
    return <EmptyState>No test cases were produced.</EmptyState>;
  return (
    <>
      <SearchBox value={q} onChange={setQ} label="Search test cases" />
      <div className="table-wrap">
        <table className="data">
          <thead>
            <tr>
              <th>ID</th>
              <th>Title</th>
              <th>Scenario</th>
              <th>Priority</th>
              <th>Type</th>
              <th>Preconditions</th>
              <th>Steps</th>
              <th>Expected result</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((t) => (
              <tr key={t.id}>
                <td className="cell-mono">{t.id}</td>
                <td style={{ minWidth: 180 }}>{t.title}</td>
                <td className="cell-mono">{t.scenario_id}</td>
                <td className="cell-mono">{t.priority}</td>
                <td className="cell-mono">{t.test_type}</td>
                <td className="cell-wide">
                  {t.preconditions.length > 0 ? (
                    <ul className="steps-list">
                      {t.preconditions.map((p, i) => (
                        <li key={i}>{p}</li>
                      ))}
                    </ul>
                  ) : (
                    <span className="muted">—</span>
                  )}
                </td>
                <td className="cell-wide">
                  {t.steps.length > 0 ? (
                    <ol className="steps-list">
                      {t.steps.map((s) => (
                        <li key={s.number}>
                          {s.action}
                          {s.expected ? (
                            <span className="muted"> → {s.expected}</span>
                          ) : null}
                        </li>
                      ))}
                    </ol>
                  ) : (
                    <span className="muted">—</span>
                  )}
                </td>
                <td className="cell-wide">{t.expected_result}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

const SEVERITY_RANK: Record<string, number> = { blocker: 0, major: 1, minor: 2 };

function GapsTable({ artifact }: { artifact: DesignArtifact }) {
  // Sort blocker/high-severity first so they are discoverable (spec section 15).
  const gaps = artifact.gap_report?.gaps;
  const sorted = useMemo(
    () =>
      [...(gaps ?? [])].sort(
        (a, b) =>
          (SEVERITY_RANK[a.severity] ?? 9) - (SEVERITY_RANK[b.severity] ?? 9),
      ),
    [gaps],
  );
  if (sorted.length === 0) return <EmptyState>No gaps were identified.</EmptyState>;
  return (
    <div className="table-wrap">
      <table className="data">
        <thead>
          <tr>
            <th>Severity</th>
            <th>Requirement</th>
            <th>Description</th>
            <th>Suggested question</th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((g, i) => (
            <tr key={i}>
              <td>
                <SeverityTag severity={g.severity} />
              </td>
              <td className="cell-mono">{g.requirement_id}</td>
              <td className="cell-wide">{g.description}</td>
              <td className="cell-wide muted">{g.suggested_question}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function CoverageView({
  artifact,
  summary,
}: {
  artifact: DesignArtifact;
  summary: SummarySchema | null;
}) {
  const m = artifact.coverage?.metrics;
  // The authoritative overall coverage % comes from the backend run summary,
  // not recomputed here (spec sections 11, 16).
  const pct = summary?.coverage_percent ?? null;
  return (
    <div>
      {pct !== null && (
        <div className="panel">
          <h2>Overall coverage</h2>
          <div className="row" style={{ marginBottom: 8 }}>
            <span style={{ fontSize: 24, fontFamily: "var(--mono)", fontWeight: 700 }}>
              {pct.toFixed(1)}%
            </span>
          </div>
          <div
            className="cov-bar"
            role="progressbar"
            aria-valuenow={Math.round(pct)}
            aria-valuemin={0}
            aria-valuemax={100}
            aria-label="Overall requirement coverage"
          >
            <div className="fill" style={{ width: `${Math.min(100, Math.max(0, pct))}%` }} />
          </div>
        </div>
      )}
      {m ? (
        <div className="table-wrap">
          <table className="data">
            <thead>
              <tr>
                <th>Category</th>
                <th>Covered</th>
                <th>Total</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td>Requirements</td>
                <td className="cell-mono">{m.covered_requirements}</td>
                <td className="cell-mono">{m.total_requirements}</td>
              </tr>
              <tr>
                <td>Business rules</td>
                <td className="cell-mono">{m.covered_business_rules}</td>
                <td className="cell-mono">{m.total_business_rules}</td>
              </tr>
              <tr>
                <td>Scenarios</td>
                <td className="cell-mono">{m.covered_scenarios}</td>
                <td className="cell-mono">{m.total_scenarios}</td>
              </tr>
              <tr>
                <td>Test cases</td>
                <td className="cell-mono">{m.total_test_cases}</td>
                <td className="cell-mono">{m.total_test_cases}</td>
              </tr>
            </tbody>
          </table>
        </div>
      ) : (
        <EmptyState>No coverage metrics available.</EmptyState>
      )}
    </div>
  );
}
