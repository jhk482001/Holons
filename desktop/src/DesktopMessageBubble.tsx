import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import DesktopRunStatusCard from "./DesktopRunStatusCard";

// Slim message bubble for the desktop overlay.
//
//   Stage 1 (shipped): markdown prose + timestamp + fenced-card stripping.
//   Stage 2 (this file now): run-event messages render as a structured
//     RunStatusCard instead of prose. Click opens the run page in the
//     user's browser.
//   Stage 3-4 (later): hire / artifact bubbles + side detail panel.
//
// Web counterpart: frontend/src/pages/DialogCenter.tsx :: MessageBubble.

interface DesktopMessage {
  id?: string | number;
  role?: string;
  sender?: string;
  content?: string;
  text?: string;
  message?: string;
  created_at?: string | number | Date;
  metadata?: Record<string, any>;
}

const FENCED_CARD_BLOCKS =
  /```(?:workflow|hire|project|artifact-(?:html|slides|file|markdown)(?:\s+[^\n]+)?)\s*\n[\s\S]*?\n```/g;

function pickContent(m: DesktopMessage): string {
  return m.content || m.text || m.message || "";
}

function pickRole(m: DesktopMessage): "user" | "bot" {
  const r = m.role || m.sender;
  return r === "user" || r === "human" ? "user" : "bot";
}

function formatTime(value: DesktopMessage["created_at"]): string {
  if (!value) return "";
  const d = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
}

export default function DesktopMessageBubble({ msg }: { msg: DesktopMessage }) {
  const role = pickRole(msg);
  const raw = pickContent(msg);
  const meta = msg.metadata || {};
  const timeLabel = formatTime(msg.created_at);

  // Run-completion event — render structured card instead of prose.
  // Mirrors the web detection: run_id present + event ∈ {run_event,
  // run_complete, run_failed}.
  const isRunEvent =
    !!meta.run_id &&
    (meta.event === "run_event" ||
      meta.event === "run_complete" ||
      meta.event === "run_failed");
  const runId = typeof meta.run_id === "number" ? meta.run_id : Number(meta.run_id);
  const runWorkflowName =
    (meta.workflow_name as string | undefined) ??
    raw.match(/^The \*\*(.+?)\*\* run you dispatched/)?.[1];

  if (isRunEvent && Number.isFinite(runId)) {
    return (
      <div className={`chat-bubble ${role} run-event`}>
        <DesktopRunStatusCard
          runId={runId}
          workflowName={runWorkflowName}
          createdAtLabel={timeLabel}
        />
      </div>
    );
  }

  const cleanContent = raw.replace(FENCED_CARD_BLOCKS, "").trim();

  return (
    <div className={`chat-bubble ${role}`}>
      <div className="chat-bubble-content markdown">
        <ReactMarkdown remarkPlugins={[remarkGfm]}>{cleanContent}</ReactMarkdown>
      </div>
      {timeLabel && <div className="chat-bubble-meta">{timeLabel}</div>}
    </div>
  );
}
