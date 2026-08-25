import { useMemo } from "react";
import type {
  ClarificationQuestion,
  ClarificationResponse,
} from "../api/types";
import { EmptyState, Panel } from "./common";

const PRIORITY_RANK: Record<string, number> = {
  blocking: 0,
  recommended: 1,
  optional: 2,
};

interface Props {
  data: ClarificationResponse;
  answers: Record<string, string>;
  setAnswer: (questionId: string, value: string) => void;
  submitting: boolean;
  submitError: string | null;
  onSubmit: (proceedWithAssumptions?: boolean) => void;
}

/**
 * Renders the current clarification question batch. Questions are ordered
 * blocking-first; each renders the least-typing widget for its answer_type. The
 * user's selections are collected locally and submitted as one batch.
 */
export function ClarificationPanel({
  data,
  answers,
  setAnswer,
  submitting,
  submitError,
  onSubmit,
}: Props) {
  const ordered = useMemo(
    () =>
      [...data.questions].sort(
        (a, b) => (PRIORITY_RANK[a.priority] ?? 3) - (PRIORITY_RANK[b.priority] ?? 3),
      ),
    [data.questions],
  );

  const blockingRemaining = data.readiness.blocking_unanswered;
  // Proceeding with assumptions is a valid escape hatch whenever the run is not
  // ready - including when blocking questions remain (e.g. the clarification round
  // cap has been reached). The backend's proceed_with_assumptions path marks every
  // remaining question (blocking included) as an accepted assumption and moves the
  // run to ready, so the button must be available in that state, not only when the
  // sole remaining questions are recommended/optional.
  const canProceedWithAssumptions = !data.readiness.ready;
  const proceedBypassesBlocking = blockingRemaining > 0;

  if (ordered.length === 0) {
    return (
      <Panel title="Requirement Clarification">
        <EmptyState>No clarification questions are pending.</EmptyState>
      </Panel>
    );
  }

  return (
    <Panel title="Requirement Clarification">
      <p className="muted" style={{ marginTop: 0 }}>
        I&apos;ve identified {ordered.length} decision
        {ordered.length === 1 ? "" : "s"} that affect test coverage.
      </p>

      <ol className="clar-list">
        {ordered.map((q, i) => (
          <li key={q.question_id} className={`clar-item ${q.priority}`}>
            <div className="clar-q">
              <span className="clar-num">{i + 1}.</span>
              <span className="clar-text">{q.question}</span>
              <span className={`sev ${q.priority}`}>{q.priority}</span>
            </div>
            {q.reason && <div className="muted clar-reason">{q.reason}</div>}
            <QuestionInput
              question={q}
              value={answers[q.question_id] ?? ""}
              onChange={(v) => setAnswer(q.question_id, v)}
            />
          </li>
        ))}
      </ol>

      {submitError && (
        <div className="alert error" role="alert" style={{ marginTop: 12 }}>
          {submitError}
        </div>
      )}

      <div className="row" style={{ gap: 12, marginTop: 16 }}>
        <button
          className="btn"
          onClick={() => onSubmit(false)}
          disabled={submitting}
        >
          {submitting ? "Submitting…" : "Submit Answers"}
        </button>
        {canProceedWithAssumptions && (
          <button
            className="btn secondary"
            onClick={() => onSubmit(true)}
            disabled={submitting}
            title={
              proceedBypassesBlocking
                ? "Proceed without answering the remaining questions (including blocking ones); each will be recorded as an assumption."
                : "Proceed without answering the remaining recommended/optional questions; assumptions will be recorded."
            }
          >
            Proceed with assumptions
          </button>
        )}
      </div>

      {data.readiness.blocking_reasons.length > 0 && (
        <ul className="muted clar-reasons">
          {data.readiness.blocking_reasons.map((r) => (
            <li key={r}>{r}</li>
          ))}
        </ul>
      )}
    </Panel>
  );
}

function QuestionInput({
  question,
  value,
  onChange,
}: {
  question: ClarificationQuestion;
  value: string;
  onChange: (value: string) => void;
}) {
  switch (question.answer_type) {
    case "boolean":
      return (
        <div className="row clar-bool" style={{ gap: 8 }}>
          {["true", "false"].map((v) => (
            <button
              key={v}
              type="button"
              className={`btn toggle ${value === v ? "active" : ""}`}
              aria-pressed={value === v}
              onClick={() => onChange(v)}
            >
              {v === "true" ? "Yes" : "No"}
            </button>
          ))}
        </div>
      );
    case "single_select":
      return (
        <div className="clar-options">
          {question.options.map((opt) => (
            <label key={opt} className="clar-radio">
              <input
                type="radio"
                name={question.question_id}
                checked={value === opt}
                onChange={() => onChange(opt)}
              />
              {opt}
            </label>
          ))}
        </div>
      );
    case "multi_select":
      return (
        <div className="clar-options">
          {question.options.map((opt) => {
            const selected = value ? value.split("\u001f") : [];
            const checked = selected.includes(opt);
            return (
              <label key={opt} className="clar-check">
                <input
                  type="checkbox"
                  checked={checked}
                  onChange={() => {
                    const next = checked
                      ? selected.filter((o) => o !== opt)
                      : [...selected, opt];
                    onChange(next.join("\u001f"));
                  }}
                />
                {opt}
              </label>
            );
          })}
        </div>
      );
    case "numeric":
      return (
        <input
          type="number"
          className="input clar-input"
          value={value}
          onChange={(e) => onChange(e.target.value)}
        />
      );
    case "date":
      return (
        <input
          type="date"
          className="input clar-input"
          value={value}
          onChange={(e) => onChange(e.target.value)}
        />
      );
    default:
      return (
        <textarea
          className="input clar-input"
          rows={2}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder="Type your answer"
        />
      );
  }
}
