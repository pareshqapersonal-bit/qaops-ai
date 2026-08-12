import { useCallback, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  ApiError,
  NetworkError,
  createDesignRun,
  createTicketRun,
} from "../api/client";

type InputMode = "document" | "ticket";

// Mirrors the backend's accepted suffixes (verified against the API).
const ACCEPTED = [
  ".pdf",
  ".docx",
  ".txt",
  ".md",
  ".markdown",
  ".csv",
  ".json",
  ".xlsx",
  ".xlsm",
];

function extensionOf(name: string): string {
  const dot = name.lastIndexOf(".");
  return dot >= 0 ? name.slice(dot).toLowerCase() : "";
}

function humanSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function UploadPage() {
  const navigate = useNavigate();
  const inputRef = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [dragging, setDragging] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  // Phase 32: input mode + Jira-style ticket fields. Document mode is unchanged.
  const [mode, setMode] = useState<InputMode>("document");
  const [ticketId, setTicketId] = useState("");
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [priority, setPriority] = useState("");
  const [labels, setLabels] = useState("");
  // Phase 35: optional design / reference attachment.
  const [attachment, setAttachment] = useState<File | null>(null);

  const onSubmitTicket = useCallback(async () => {
    if (submitting) return;
    if (!title.trim() || !description.trim()) {
      setError("Title and description are required.");
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      const labelList = labels
        .split(",")
        .map((l) => l.trim())
        .filter((l) => l.length > 0);
      const created = await createTicketRun(
        {
          title: title.trim(),
          description: description.trim(),
          ticket_id: ticketId.trim() || undefined,
          priority: priority.trim() || undefined,
          labels: labelList.length > 0 ? labelList : undefined,
        },
        attachment,
      );
      navigate(`/runs/${created.run_id}`);
    } catch (err) {
      if (err instanceof ApiError) {
        setError(err.detail);
      } else if (err instanceof NetworkError) {
        setError(
          "Could not reach the QAOps backend. Is it running on the configured address?",
        );
      } else {
        setError("Something went wrong submitting the ticket.");
      }
      setSubmitting(false);
    }
  }, [
    submitting,
    title,
    description,
    labels,
    ticketId,
    priority,
    attachment,
    navigate,
  ]);

  const chooseFile = useCallback((f: File) => {
    const ext = extensionOf(f.name);
    if (!ACCEPTED.includes(ext)) {
      setError(
        `Unsupported file type "${ext || "(none)"}". Accepted: ${ACCEPTED.join(", ")}.`,
      );
      setFile(null);
      return;
    }
    if (f.size === 0) {
      setError("That file is empty.");
      setFile(null);
      return;
    }
    setError(null);
    setFile(f);
  }, []);

  const onDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      setDragging(false);
      const f = e.dataTransfer.files?.[0];
      if (f) chooseFile(f);
    },
    [chooseFile],
  );

  const onSubmit = useCallback(async () => {
    if (!file || submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      const created = await createDesignRun(file);
      navigate(`/runs/${created.run_id}`);
    } catch (err) {
      if (err instanceof ApiError) {
        setError(err.detail);
      } else if (err instanceof NetworkError) {
        setError(
          "Could not reach the QAOps backend. Is it running on the configured address?",
        );
      } else {
        setError("Something went wrong submitting the run.");
      }
      setSubmitting(false);
    }
  }, [file, submitting, navigate]);

  return (
    <div>
      <h1>New design run</h1>
      <p className="subtitle">
        Upload a requirement document or a requirements/scenarios file. QAOps
        detects the workflow and generates test design artifacts.
      </p>

      {error && (
        <div className="alert error" role="alert">
          <div className="title">Upload problem</div>
          <div>{error}</div>
        </div>
      )}

      <div className="row" role="tablist" aria-label="Input type" style={{ marginBottom: 16 }}>
        <button
          className={`btn ${mode === "document" ? "" : "secondary"}`}
          role="tab"
          aria-selected={mode === "document"}
          onClick={() => {
            setMode("document");
            setError(null);
          }}
          disabled={submitting}
        >
          Document
        </button>
        <button
          className={`btn ${mode === "ticket" ? "" : "secondary"}`}
          role="tab"
          aria-selected={mode === "ticket"}
          onClick={() => {
            setMode("ticket");
            setError(null);
          }}
          disabled={submitting}
        >
          Ticket
        </button>
      </div>

      {mode === "document" && (
      <div className="panel">
        <div
          className={`dropzone ${dragging ? "dragging" : ""}`}
          onDragOver={(e) => {
            e.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={onDrop}
          onClick={() => inputRef.current?.click()}
          role="button"
          tabIndex={0}
          aria-label="Upload a file"
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === " ") {
              e.preventDefault();
              inputRef.current?.click();
            }
          }}
        >
          <div>
            <strong>Drop a file here</strong> or click to browse
          </div>
          <div className="hint">{ACCEPTED.join("  ·  ")}</div>
          <input
            ref={inputRef}
            type="file"
            accept={ACCEPTED.join(",")}
            style={{ display: "none" }}
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f) chooseFile(f);
              // Allow re-selecting the same file later.
              e.target.value = "";
            }}
          />
        </div>

        {file && (
          <div className="file-card" style={{ marginTop: 16 }}>
            <div className="meta">
              <div className="name">{file.name}</div>
              <div className="sub">
                {humanSize(file.size)} · {extensionOf(file.name)}
              </div>
            </div>
            <button
              className="btn secondary"
              onClick={() => setFile(null)}
              disabled={submitting}
            >
              Remove
            </button>
          </div>
        )}

        <div className="row" style={{ marginTop: 20 }}>
          <button className="btn" onClick={onSubmit} disabled={!file || submitting}>
            {submitting ? (
              <>
                <span className="spin" aria-hidden="true" /> Submitting…
              </>
            ) : (
              "Start run"
            )}
          </button>
          {file && !submitting && (
            <span className="muted">Ready to analyze {file.name}</span>
          )}
        </div>
      </div>
      )}

      {mode === "ticket" && (
      <div className="panel ticket-form">
        <div className="field">
          <label htmlFor="ticket-id">Ticket ID</label>
          <input
            id="ticket-id"
            type="text"
            value={ticketId}
            placeholder="OTP-123 (optional)"
            onChange={(e) => setTicketId(e.target.value)}
            disabled={submitting}
          />
        </div>
        <div className="field">
          <label htmlFor="ticket-title">Title</label>
          <input
            id="ticket-title"
            type="text"
            value={title}
            placeholder="Add OTP login"
            onChange={(e) => setTitle(e.target.value)}
            disabled={submitting}
          />
        </div>
        <div className="field">
          <label htmlFor="ticket-description">Description</label>
          <textarea
            id="ticket-description"
            rows={3}
            value={description}
            placeholder="Users should be able to log in using their mobile number and OTP."
            onChange={(e) => setDescription(e.target.value)}
            disabled={submitting}
          />
        </div>
        <div className="field">
          <label htmlFor="ticket-priority">Priority</label>
          <input
            id="ticket-priority"
            type="text"
            value={priority}
            placeholder="High (optional)"
            onChange={(e) => setPriority(e.target.value)}
            disabled={submitting}
          />
        </div>
        <div className="field">
          <label htmlFor="ticket-labels">Labels (comma-separated)</label>
          <input
            id="ticket-labels"
            type="text"
            value={labels}
            placeholder="auth, login (optional)"
            onChange={(e) => setLabels(e.target.value)}
            disabled={submitting}
          />
        </div>
        <div className="field">
          <label htmlFor="ticket-attachment">
            Design / Reference Material (optional)
          </label>
          <input
            id="ticket-attachment"
            type="file"
            accept=".pdf,.docx,.md,.markdown,.txt"
            onChange={(e) => setAttachment(e.target.files?.[0] ?? null)}
            disabled={submitting}
          />
          {attachment && (
            <span className="muted">Attached: {attachment.name}</span>
          )}
        </div>
        <div className="row" style={{ marginTop: 20 }}>
          <button
            className="btn"
            onClick={onSubmitTicket}
            disabled={!title.trim() || !description.trim() || submitting}
          >
            {submitting ? (
              <>
                <span className="spin" aria-hidden="true" /> Submitting…
              </>
            ) : (
              "Start run"
            )}
          </button>
        </div>
      </div>
      )}
    </div>
  );
}
