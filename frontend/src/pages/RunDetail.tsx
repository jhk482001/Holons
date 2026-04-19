import { useTranslation } from "react-i18next";
import { useState } from "react";
import { useParams, useNavigate, Link } from "react-router-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { RunsAPI, AgentsAPI } from "../api/client";
import Avatar from "../components/Avatar";
import Markdown from "../components/Markdown";
import RunFlowDiagram from "../components/RunFlowDiagram";
import UsageStackChart from "../components/UsageStackChart";

function useStatusLabel(): Record<string, string> {
  const { t } = useTranslation();
  return {
    running: t("runs.statusLabels.running"),
    done: t("runs.statusLabels.done"),
    error: t("runs.statusLabels.error"),
    cancelling: t("runs.statusLabels.cancelling"),
    cancelled: t("runs.statusLabels.cancelled"),
    queued: t("runs.statusLabels.queued"),
    paused: t("runs.statusLabels.paused"),
    failed: t("runs.statusLabels.failed"),
  };
}

const STATUS_COLOR: Record<string, { bg: string; fg: string }> = {
  running: { bg: "var(--accent-soft)", fg: "var(--accent)" },
  done: { bg: "var(--good-soft)", fg: "var(--good)" },
  error: { bg: "var(--danger-soft)", fg: "var(--danger)" },
  failed: { bg: "var(--danger-soft)", fg: "var(--danger)" },
  cancelled: { bg: "var(--surface-2)", fg: "var(--ink-3)" },
  cancelling: { bg: "var(--surface-2)", fg: "var(--ink-3)" },
  queued: { bg: "var(--surface-2)", fg: "var(--ink-3)" },
  paused: { bg: "var(--surface-2)", fg: "var(--ink-3)" },
};

interface ToolCall {
  toolUseId?: string;
  name: string;
  input?: unknown;
  output?: unknown;
  error?: string | null;
  duration_ms?: number;
}

interface StepRow {
  id: number;
  iteration: number;
  agent_id: number | null;
  role_label: string | null;
  prompt: string | null;
  response: string | null;
  input_tokens: number;
  output_tokens: number;
  cost_usd: number;
  duration_ms: number;
  started_at: string;
  model_id: string | null;
  turn?: number;
  tool_calls?: ToolCall[] | null;
  node_position?: number | null;
  error?: string | null;
}

interface TaskRow {
  id: number;
  agent_id: number;
  priority: string;
  status: string;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  error_message: string | null;
}

export default function RunDetail() {
  const { t } = useTranslation();
  const STATUS_LABEL = useStatusLabel();
  const { id } = useParams<{ id: string }>();
  const runId = Number(id);
  const navigate = useNavigate();
  const qc = useQueryClient();

  const { data: run, isLoading } = useQuery({
    queryKey: ["run", runId],
    queryFn: () => RunsAPI.get(runId),
    enabled: !isNaN(runId),
    refetchInterval: (query) => {
      const d = query.state.data as any;
      if (!d) return 3_000;
      return d.status === "running" || d.status === "queued" || d.status === "cancelling" ? 3_000 : false;
    },
  });
  const { data: agents = [] } = useQuery({ queryKey: ["agents"], queryFn: AgentsAPI.list });

  const stop = useMutation({
    mutationFn: () => RunsAPI.stop(runId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["run", runId] }),
  });

  if (isLoading) return <div className="page">{t("btn.loading")}</div>;
  if (!run) return <div className="page">{t("runDetail.notFound")}</div>;

  const steps = (run.steps || []) as StepRow[];
  const tasks = (run.tasks || []) as TaskRow[];
  const statusStyle = STATUS_COLOR[run.status] || STATUS_COLOR.queued;
  const agentById = (id: number | null) => agents.find((a) => a.id === id);
  const canStop = run.status === "running" || run.status === "queued" || run.status === "paused";

  return (
    <div className="page" data-testid="run-detail-page">
      <button
        onClick={() => navigate("/runs")}
        style={{
          display: "inline-flex",
          alignItems: "center",
          gap: 6,
          padding: "6px 12px",
          background: "var(--surface)",
          border: "1px solid var(--border)",
          borderRadius: 8,
          color: "var(--ink-2)",
          fontSize: 12,
          fontWeight: 700,
          marginBottom: 20,
          cursor: "pointer",
        }}
      >
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M19 12H5M12 19l-7-7 7-7" />
        </svg>
        {t("runDetail.back")}
      </button>

      <div style={{
        display: "flex",
        alignItems: "center",
        gap: 16,
        paddingBottom: 20,
        marginBottom: 20,
        borderBottom: "1px solid var(--border)",
      }}>
        <div>
          <h1 style={{ fontSize: 24 }}>{t("runDetail.runPrefix")}#{run.id}</h1>
          <div style={{ fontSize: 12, color: "var(--ink-3)", marginTop: 4 }}>
            {t("runDetail.workflowLabel")} <Link to={`/workflows/${run.workflow_id}`} style={{ color: "var(--accent)", textDecoration: "none" }}>#{run.workflow_id}</Link>
            {" · "}
            {new Date(run.started_at).toLocaleString()}
          </div>
        </div>
        <div style={{
          fontSize: 12,
          fontWeight: 700,
          padding: "6px 14px",
          borderRadius: 999,
          background: statusStyle.bg,
          color: statusStyle.fg,
        }}
          data-testid="run-status"
        >
          {STATUS_LABEL[run.status] || run.status}
        </div>
        <div style={{ flex: 1 }} />
        {canStop && (
          <button
            data-testid="run-stop-btn"
            onClick={() => stop.mutate()}
            disabled={stop.isPending}
            style={{
              padding: "8px 18px",
              border: "1px solid var(--danger)",
              color: "var(--danger)",
              background: "white",
              borderRadius: 10,
              fontSize: 12,
              fontWeight: 700,
              cursor: "pointer",
            }}
          >
            {stop.isPending ? t("runDetail.stopping") : t("runDetail.stopRun")}
          </button>
        )}
      </div>

      {/* Summary cards */}
      <div style={{
        display: "grid",
        gridTemplateColumns: "repeat(4, 1fr)",
        gap: 12,
        marginBottom: 24,
      }}>
        <SumCard label={t("runDetail.totalCost")} value={`$${Number(run.total_cost_usd).toFixed(4)}`} />
        <SumCard label="Input tokens" value={Number(run.total_input_tokens).toLocaleString()} />
        <SumCard label="Output tokens" value={Number(run.total_output_tokens).toLocaleString()} />
        <SumCard label={t("runDetail.iterations")} value={String(run.iterations || 1)} />
      </div>

      {/* Initial input */}
      <section style={{ marginBottom: 24 }}>
        <h3 style={{ fontSize: 11, textTransform: "uppercase", color: "var(--ink-3)", letterSpacing: 1, fontWeight: 800, marginBottom: 8 }}>
          {t("runDetail.initialInput")}
        </h3>
        <pre style={{
          fontFamily: "var(--font-mono)",
          fontSize: 12,
          color: "var(--ink-2)",
          background: "var(--surface-2)",
          padding: "12px 14px",
          borderRadius: 10,
          border: "1px solid var(--border)",
          whiteSpace: "pre-wrap",
          lineHeight: 1.6,
        }}>{run.initial_input || t("runDetail.noInput")}</pre>
      </section>

      {/* Final output — rendered as Markdown so lists, headings, code
          fences, and tables all show up correctly. */}
      {run.final_output && (
        <section style={{ marginBottom: 24 }} data-testid="run-final-output">
          <h3 style={{ fontSize: 11, textTransform: "uppercase", color: "var(--ink-3)", letterSpacing: 1, fontWeight: 800, marginBottom: 8 }}>
            {t("runDetail.finalOutput")}
          </h3>
          <div style={{
            background: "var(--good-soft)",
            padding: "14px 18px",
            borderRadius: 10,
            border: "1px solid rgba(95, 181, 126, 0.3)",
          }}>
            <Markdown content={run.final_output} />
          </div>
        </section>
      )}

      {/* Flow diagram — clickable nodes scroll to the matching step below */}
      <RunFlowDiagram workflowId={run.workflow_id} steps={steps} />

      <div style={{ marginTop: 24 }}>
        <h3 style={{ fontSize: 11, textTransform: "uppercase",
                     color: "var(--ink-3)", letterSpacing: 1,
                     fontWeight: 800, marginBottom: 12 }}>
          {t("runDetail.usageByAgent")}
        </h3>
        <UsageStackChart group_by="agent" workflow_id={run.workflow_id} days={14} />
      </div>

      {/* Step tree */}
      <section style={{ marginBottom: 24 }}>
        <h3 style={{ fontSize: 11, textTransform: "uppercase", color: "var(--ink-3)", letterSpacing: 1, fontWeight: 800, marginBottom: 12 }}>
          {t("runDetail.steps", { count: steps.length })}
        </h3>
        {steps.length === 0 ? (
          <div style={{ color: "var(--ink-4)", fontSize: 13, textAlign: "center", padding: 30 }}>
            {t("runDetail.noSteps")}
          </div>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 10 }} data-testid="run-steps">
            {steps.map((s, idx) => {
              const agent = agentById(s.agent_id);
              return (
                <div
                  key={s.id}
                  id={`step-row-${s.id}`}
                  style={{
                    background: "var(--surface)",
                    border: "1px solid var(--border)",
                    borderRadius: 12,
                    padding: 14,
                    transition: "background 0.3s, box-shadow 0.3s",
                  }}
                >
                  <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 10 }}>
                    <div style={{
                      width: 28,
                      height: 28,
                      borderRadius: "50%",
                      background: "var(--accent-soft)",
                      color: "var(--accent)",
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "center",
                      fontWeight: 800,
                      fontSize: 11,
                    }}>{idx + 1}</div>
                    {agent && <Avatar cfg={agent.avatar_config} size={36} title={agent.name} />}
                    <div style={{ flex: 1 }}>
                      <div style={{ fontSize: 13, fontWeight: 800, display: "flex", alignItems: "center", gap: 6 }}>
                        {agent?.name || `Agent #${s.agent_id}`}
                        {s.turn && s.turn > 0 && (
                          <span style={{
                            fontSize: 9,
                            fontWeight: 800,
                            letterSpacing: 0.8,
                            background: "var(--surface-2)",
                            color: "var(--ink-3)",
                            padding: "2px 8px",
                            borderRadius: 999,
                          }}>TURN {s.turn}</span>
                        )}
                      </div>
                      <div style={{ fontSize: 11, color: "var(--ink-3)" }}>
                        {s.role_label || agent?.role_title || "—"}
                        {s.iteration > 1 && ` · ${t("runDetail.iteration", { n: s.iteration })}`}
                      </div>
                    </div>
                    <div style={{ fontSize: 10, color: "var(--ink-4)", textAlign: "right" }}>
                      <div>{Number(s.input_tokens) + Number(s.output_tokens)} tokens</div>
                      <div>${Number(s.cost_usd).toFixed(4)}</div>
                      <div>{(Number(s.duration_ms) / 1000).toFixed(2)}s</div>
                    </div>
                  </div>
                  {s.response && (
                    <div
                      style={{
                        background: "var(--surface-2)",
                        padding: "10px 14px",
                        borderRadius: 8,
                        border: "1px solid var(--border)",
                        maxHeight: 320,
                        overflow: "auto",
                      }}
                      data-testid={`step-response-${s.id}`}
                    >
                      <Markdown content={s.response} />
                    </div>
                  )}
                  {s.tool_calls && s.tool_calls.length > 0 && (
                    <ToolCallsList calls={s.tool_calls} />
                  )}
                </div>
              );
            })}
          </div>
        )}
      </section>

      {/* Task queue entries */}
      {tasks.length > 0 && (
        <section>
          <h3 style={{ fontSize: 11, textTransform: "uppercase", color: "var(--ink-3)", letterSpacing: 1, fontWeight: 800, marginBottom: 8 }}>
            {t("runDetail.queueTasks")}
          </h3>
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            {tasks.map((t) => {
              const agent = agentById(t.agent_id);
              return (
                <div key={t.id} style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 12,
                  padding: "8px 14px",
                  background: "var(--surface)",
                  border: "1px solid var(--border)",
                  borderRadius: 10,
                  fontSize: 11,
                }}>
                  <div style={{ fontWeight: 700, color: "var(--ink-3)" }}>#{t.id}</div>
                  <div style={{ flex: 1, color: "var(--ink)" }}>
                    {agent?.name || `Agent #${t.agent_id}`}
                  </div>
                  <div style={{
                    fontSize: 9,
                    fontWeight: 800,
                    letterSpacing: 1,
                    padding: "2px 8px",
                    borderRadius: 999,
                    background: "var(--surface-2)",
                    color: "var(--ink-3)",
                  }}>{t.priority.toUpperCase()}</div>
                  <div style={{ color: "var(--ink-3)" }}>{t.status}</div>
                  {t.error_message && (
                    <div style={{ color: "var(--danger)", maxWidth: 300, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={t.error_message}>
                      {t.error_message}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </section>
      )}
    </div>
  );
}

function ToolCallsList({ calls }: { calls: ToolCall[] }) {
  const { t } = useTranslation();
  return (
    <div style={{ marginTop: 10, display: "flex", flexDirection: "column", gap: 6 }}>
      <div style={{
        fontSize: 9,
        fontWeight: 800,
        textTransform: "uppercase",
        letterSpacing: 1,
        color: "var(--ink-4)",
      }}>
        {t("runDetail.toolCalls", { count: calls.length })}
      </div>
      {calls.map((c, i) => (
        <ToolCallRow key={`${c.toolUseId || i}`} call={c} />
      ))}
    </div>
  );
}

function ToolCallRow({ call }: { call: ToolCall }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const isError = !!call.error;
  return (
    <div style={{
      background: isError ? "var(--danger-soft)" : "rgba(95, 181, 126, 0.06)",
      border: `1px solid ${isError ? "rgba(232, 100, 80, 0.3)" : "rgba(95, 181, 126, 0.3)"}`,
      borderRadius: 8,
      overflow: "hidden",
    }}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        style={{
          display: "flex",
          alignItems: "center",
          gap: 10,
          width: "100%",
          padding: "8px 12px",
          background: "transparent",
          border: "none",
          cursor: "pointer",
          fontFamily: "inherit",
          fontSize: 11,
          textAlign: "left",
        }}
      >
        <span style={{
          fontFamily: "var(--font-mono)",
          fontWeight: 800,
          color: isError ? "var(--danger)" : "var(--good)",
        }}>
          {call.name}
        </span>
        <span style={{ color: "var(--ink-4)" }}>
          {isError ? t("skills.failure") : t("skills.success")}
          {typeof call.duration_ms === "number" && ` · ${(call.duration_ms / 1000).toFixed(2)}s`}
        </span>
        <span style={{ marginLeft: "auto", color: "var(--ink-4)" }}>
          {open ? t("skills.collapse") + " ▲" : t("skills.expand") + " ▼"}
        </span>
      </button>
      {open && (
        <div style={{
          padding: "4px 12px 12px",
          display: "flex",
          flexDirection: "column",
          gap: 8,
        }}>
          <div>
            <div style={{ fontSize: 9, fontWeight: 800, letterSpacing: 0.6, color: "var(--ink-4)", textTransform: "uppercase", marginBottom: 3 }}>
              Input
            </div>
            <pre style={{
              fontFamily: "var(--font-mono)",
              fontSize: 10,
              color: "var(--ink-2)",
              background: "white",
              border: "1px solid var(--border)",
              borderRadius: 6,
              padding: 8,
              margin: 0,
              whiteSpace: "pre-wrap",
              wordBreak: "break-word",
              maxHeight: 180,
              overflow: "auto",
            }}>{JSON.stringify(call.input ?? {}, null, 2)}</pre>
          </div>
          <div>
            <div style={{ fontSize: 9, fontWeight: 800, letterSpacing: 0.6, color: "var(--ink-4)", textTransform: "uppercase", marginBottom: 3 }}>
              {isError ? "Error" : "Output"}
            </div>
            <pre style={{
              fontFamily: "var(--font-mono)",
              fontSize: 10,
              color: "var(--ink-2)",
              background: "white",
              border: "1px solid var(--border)",
              borderRadius: 6,
              padding: 8,
              margin: 0,
              whiteSpace: "pre-wrap",
              wordBreak: "break-word",
              maxHeight: 240,
              overflow: "auto",
            }}>
              {isError
                ? call.error
                : JSON.stringify(call.output ?? null, null, 2)}
            </pre>
          </div>
        </div>
      )}
    </div>
  );
}

function SumCard({ label, value }: { label: string; value: string }) {
  return (
    <div style={{
      background: "var(--surface)",
      border: "1px solid var(--border)",
      borderRadius: 12,
      padding: "14px 16px",
    }}>
      <div style={{ fontSize: 10, fontWeight: 800, letterSpacing: 1, color: "var(--ink-4)", textTransform: "uppercase" }}>
        {label}
      </div>
      <div style={{ fontSize: 18, fontWeight: 800, marginTop: 4 }}>{value}</div>
    </div>
  );
}
