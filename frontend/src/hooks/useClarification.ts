import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "../api/client";
import {
  getClarifications,
  submitClarificationAnswers,
} from "../api/client";
import type {
  AnswerInput,
  ClarificationResponse,
} from "../api/types";

type LoadState = "loading" | "ready" | "error";

export interface UseClarificationResult {
  data: ClarificationResponse | null;
  loadState: LoadState;
  errorMessage: string | null;
  // Local, in-progress answer selections keyed by question_id.
  answers: Record<string, string>;
  setAnswer: (questionId: string, value: string) => void;
  submitting: boolean;
  submitError: string | null;
  // True when the backend reported this run has moved past clarification (409),
  // so the caller (RunPage) should refresh run status and re-branch.
  movedOn: boolean;
  submit: (proceedWithAssumptions?: boolean) => Promise<void>;
}

/**
 * Owns the clarification question batch, readiness, and the user's in-progress
 * answer selections for a run. Loads the current batch once (and after each
 * submit the POST response IS the new batch, so no extra GET). Does NOT poll -
 * useRun owns run-status polling; this hook owns clarification state only.
 */
export function useClarification(
  runId: string | null,
  enabled: boolean,
): UseClarificationResult {
  const [data, setData] = useState<ClarificationResponse | null>(null);
  const [loadState, setLoadState] = useState<LoadState>("loading");
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [movedOn, setMovedOn] = useState(false);

  const cancelledRef = useRef(false);

  useEffect(() => {
    if (!runId || !enabled) {
      return;
    }
    cancelledRef.current = false;
    const controller = new AbortController();

    const load = async () => {
      try {
        const next = await getClarifications(runId, { signal: controller.signal });
        if (cancelledRef.current) return;
        setData(next);
        setLoadState("ready");
        setErrorMessage(null);
      } catch (err) {
        if (cancelledRef.current) return;
        if (err instanceof DOMException && err.name === "AbortError") return;
        // 409 = the run moved past clarification; signal the caller to re-branch.
        if (err instanceof ApiError && err.status === 409) {
          setMovedOn(true);
          return;
        }
        setErrorMessage(
          err instanceof Error ? err.message : "Failed to load clarification.",
        );
        setLoadState((prev) => (prev === "ready" ? "ready" : "error"));
      }
    };

    void load();

    return () => {
      cancelledRef.current = true;
      controller.abort();
    };
  }, [runId, enabled]);

  const setAnswer = useCallback((questionId: string, value: string) => {
    setAnswers((prev) => ({ ...prev, [questionId]: value }));
  }, []);

  const submit = useCallback(
    async (proceedWithAssumptions = false) => {
      if (!runId || !data) return;
      setSubmitting(true);
      setSubmitError(null);
      // Build the batch from the current question set + local selections. Only
      // questions the user actually answered are sent; skipped ones are handled
      // by proceed_with_assumptions on the backend.
      const batch: AnswerInput[] = data.questions
        .filter((q) => answers[q.question_id] !== undefined && answers[q.question_id] !== "")
        .map((q) => ({
          question_id: q.question_id,
          answer_type: q.answer_type,
          answer: answers[q.question_id],
        }));
      try {
        const next = await submitClarificationAnswers(
          runId,
          batch,
          proceedWithAssumptions,
        );
        setData(next);
        // Keep local selections so answered questions stay editable until start.
      } catch (err) {
        if (err instanceof ApiError && err.status === 409) {
          // Round cap or lifecycle move; surface the backend detail so the UI can
          // show the forced proceed-with-assumptions prompt.
          setSubmitError(err.detail);
        } else if (err instanceof ApiError) {
          // 400 contradictory/malformed - show the backend detail verbatim.
          setSubmitError(err.detail);
        } else {
          setSubmitError(
            err instanceof Error ? err.message : "Failed to submit answers.",
          );
        }
      } finally {
        setSubmitting(false);
      }
    },
    [runId, data, answers],
  );

  return {
    data,
    loadState,
    errorMessage,
    answers,
    setAnswer,
    submitting,
    submitError,
    movedOn,
    submit,
  };
}
