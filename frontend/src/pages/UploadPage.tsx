import { useCallback, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ApiError, NetworkError, createDesignRun } from "../api/client";

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
    </div>
  );
}
