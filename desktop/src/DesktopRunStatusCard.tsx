import { useQuery } from "@tanstack/react-query";
import { open as openShell } from "@tauri-apps/plugin-shell";
import { RunsAPI } from "@shared/api/client";
import { absoluteUrl } from "./api-adapter";

// Stage 2 of bubble parity: structured run-completion card. Mirrors the
// web RunStatusCard in frontend/src/pages/DialogCenter.tsx — same status
// pill / workflow name / poll-while-active behaviour. Click opens the
// run page in the user's default browser via the tauri shell plugin
// (the desktop overlay has no router; the web SPA at the sidecar host
// handles /runs/:id). Stage 4 will swap this for an in-overlay panel.

const STATUS_LABEL: Record<string, string> = {
  queued: "Queued",
  running: "Running",
  paused: "Paused",
  cancelling: "Cancelling",
  cancelled: "Cancelled",
  done: "Done",
  error: "Failed",
};

export default function DesktopRunStatusCard({
  runId,
  workflowName,
  createdAtLabel,
}: {
  runId: number;
  workflowName?: string;
  createdAtLabel?: string;
}) {
  const { data: run } = useQuery({
    queryKey: ["run", runId],
    queryFn: () => RunsAPI.get(runId),
    refetchInterval: (q) => {
      const r = q.state.data as { status?: string } | undefined;
      if (!r) return 3_000;
      return ["running", "queued", "cancelling", "paused"].includes(r.status || "")
        ? 3_000
        : false;
    },
  });

  const status = run?.status || "queued";
  const isActive = ["running", "queued", "cancelling", "paused"].includes(status);
  const isDone = status === "done";
  const isError = status === "error" || status === "cancelled";
  const cls = isActive ? "active" : isDone ? "done" : isError ? "error" : "neutral";
  const displayName =
    workflowName || (run as any)?.workflow_name || `Workflow #${run?.workflow_id ?? ""}`;

  const openRunPage = () => {
    openShell(absoluteUrl(`/runs/${runId}`)).catch(() => {
      // shell plugin unavailable in vite dev — silently no-op so the
      // bubble doesn't crash when running outside the tauri shell.
    });
  };

  return (
    <div
      className={`run-status-card ${cls} clickable`}
      role="button"
      tabIndex={0}
      onClick={openRunPage}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          openRunPage();
        }
      }}
    >
      <div className="run-status-row">
        {isActive && <span className="spinner" />}
        <svg
          className="run-status-icon"
          width="14"
          height="14"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <polyline points="9 11 12 14 22 4" />
          <path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11" />
        </svg>
        <span className="run-status-name" title={displayName}>
          {displayName}
        </span>
        <span className="run-status-runid">#{runId}</span>
        <span className={`run-status-pill ${cls}`}>{STATUS_LABEL[status] || status}</span>
        <span className="run-status-spacer" />
        {createdAtLabel && <span className="run-status-time">{createdAtLabel}</span>}
        <button
          type="button"
          className="run-status-open"
          aria-label="Open run page in browser"
          title="Open run page"
          onClick={(e) => {
            e.stopPropagation();
            openRunPage();
          }}
        >
          <svg
            width="14"
            height="14"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2.2"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <polyline points="9 18 15 12 9 6" />
          </svg>
        </button>
      </div>
    </div>
  );
}
