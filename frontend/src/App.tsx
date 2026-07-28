import { NavLink, Route, Routes } from "react-router-dom";
import { useBackendStatus } from "./hooks/useBackendStatus";
import { UploadPage } from "./pages/UploadPage";
import { RunPage } from "./pages/RunPage";

function BackendPill() {
  const status = useBackendStatus();
  const label =
    status.state === "online"
      ? `backend ${status.version ?? "online"}`
      : status.state === "offline"
        ? "backend offline"
        : "checking…";
  return (
    <span className="backend-pill" title="Backend health">
      <span className={`dot ${status.state}`} aria-hidden="true" />
      {label}
      {status.state === "online" && status.providerCount !== null && (
        <span className="muted"> · {status.providerCount} providers</span>
      )}
    </span>
  );
}

export function App() {
  return (
    <div className="app-shell">
      <header className="topbar">
        <span className="brand">QAOps AI</span>
        <nav>
          <NavLink to="/" className={({ isActive }) => (isActive ? "active" : "")} end>
            New Run
          </NavLink>
        </nav>
        <span className="spacer" />
        <BackendPill />
      </header>
      <main className="content">
        <Routes>
          <Route path="/" element={<UploadPage />} />
          <Route path="/runs/:runId" element={<RunPage />} />
          <Route path="*" element={<UploadPage />} />
        </Routes>
      </main>
    </div>
  );
}
