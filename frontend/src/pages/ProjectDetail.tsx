import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate, useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { AgentsAPI, ProjectsAPI, api } from "../api/client";
import type { Artifact } from "../api/client";
import Avatar from "../components/Avatar";
import ArtifactBubble from "../components/ArtifactBubble";
import UsageStackChart from "../components/UsageStackChart";

interface ProjectArtifactRow {
  id: number;
  agent_id: number | null;
  source: string;
  source_ref: number | null;
  kind: string;
  title: string | null;
  payload: Artifact;
  created_at: string;
  agent_name?: string | null;
  agent_role?: string | null;
}

export default function ProjectDetail() {
  const { t } = useTranslation();
  const { id } = useParams<{ id: string }>();
  const pid = Number(id);
  const navigate = useNavigate();
  const qc = useQueryClient();

  const { data: project, isLoading } = useQuery({
    queryKey: ["project", pid],
    queryFn: () => ProjectsAPI.get(pid),
    enabled: !!pid,
  });
  const { data: agents = [] } = useQuery({ queryKey: ["agents"], queryFn: AgentsAPI.list });
  const { data: milestones = [] } = useQuery({
    queryKey: ["project-milestones", pid],
    queryFn: () => api.get<Array<{id:number; title:string; description?:string;
      status:string; due_date?:string; position:number}>>(`/projects/${pid}/milestones`),
    enabled: !!pid,
  });
  const { data: reports = [] } = useQuery({
    queryKey: ["project-reports", pid],
    queryFn: () => api.get<Array<{id:number; report_date:string;
      summary_md:string; metrics:any; created_at:string}>>(`/projects/${pid}/reports`),
    enabled: !!pid,
  });
  const { data: events = [] } = useQuery({
    queryKey: ["project-events", pid],
    queryFn: () => api.get<Array<{id:number; actor:string|null; event_type:string;
      payload:any; created_at:string}>>(`/projects/${pid}/events`),
    enabled: !!pid,
  });
  const { data: outputs = [] } = useQuery({
    queryKey: ["project-outputs", pid],
    queryFn: () => api.get<Array<{run_id:number; status:string; started_at:string;
      finished_at:string|null; total_cost_usd:number|string;
      workflow_name:string|null; final_output:string}>>(`/projects/${pid}/outputs`),
    enabled: !!pid,
  });
  const { data: artifacts = [] } = useQuery({
    queryKey: ["project-artifacts", pid],
    queryFn: () => api.get<ProjectArtifactRow[]>(`/projects/${pid}/artifacts`),
    enabled: !!pid,
  });

  const [composer, setComposer] = useState("");
  const { data: chatBundle, refetch: refetchChat } = useQuery({
    queryKey: ["project-chat", pid],
    queryFn: () => ProjectsAPI.chatMessages(pid),
    enabled: !!pid,
  });
  const chat = useMutation({
    mutationFn: async (message: string) =>
      ProjectsAPI.chatSend(pid, message, chatBundle?.thread_id),
    onSuccess: () => { setComposer(""); refetchChat(); },
  });

  const setStatus = useMutation({
    mutationFn: async (status: string) => ProjectsAPI.update(pid, { status: status as any }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["project", pid] }),
  });

  if (isLoading) return <div className="page">{t("btn.loading")}</div>;
  if (!project) {
    return (
      <div className="page" style={{ padding: 40, textAlign: "center" }}>
        {t("projectDetail.notFound")} <button className="mbtn" onClick={() => navigate("/projects")}>{t("projects.backToProjects")}</button>
      </div>
    );
  }

  const coord = agents.find((a) => a.id === project.coordinator_agent_id);

  return (
    <div className="page">
      {/* Header */}
      <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 16 }}>
        <div style={{ flex: 1 }}>
          <button className="mbtn" onClick={() => navigate("/projects")} style={{ marginBottom: 8, padding: "4px 10px", fontSize: 11 }}>
            {t("projects.backToProjects")}
          </button>
          <h1 style={{ margin: 0 }}>{project.name}</h1>
          {project.description && (
            <div className="subtitle" style={{ marginTop: 4 }}>{project.description}</div>
          )}
          {project.goal && (
            <div style={{
              marginTop: 10, padding: 10, fontSize: 12,
              background: "var(--surface-2)", borderLeft: "3px solid var(--accent)",
              borderRadius: 4, color: "var(--ink-2)",
            }}>
              <strong>{t("projects.goal")}:</strong> {project.goal}
            </div>
          )}
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 6, alignItems: "flex-end" }}>
          <StatusBadge status={project.status} />
          <div style={{ display: "flex", gap: 6 }}>
            {project.status === "active" && (
              <button className="mbtn" onClick={() => setStatus.mutate("paused")}
                      style={{ padding: "6px 12px", fontSize: 11 }}>{t("projects.pause")}</button>
            )}
            {project.status === "paused" && (
              <button className="mbtn" onClick={() => setStatus.mutate("active")}
                      style={{ padding: "6px 12px", fontSize: 11 }}>{t("projects.resume")}</button>
            )}
            {project.status !== "done" && (
              <button className="mbtn" onClick={() => setStatus.mutate("done")}
                      style={{ padding: "6px 12px", fontSize: 11 }}>{t("projects.markDone")}</button>
            )}
          </div>
        </div>
      </div>

      {/* Members */}
      <Section title={t("projectDetail.team")}>
        <div style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fill, minmax(220px, 1fr))",
          gap: 10,
        }}>
          {(project.members || []).map((m) => {
            const isCoord = project.coordinator_agent_id === m.agent_id;
            return (
              <div key={m.agent_id} style={{
                padding: 10, background: "var(--surface)",
                border: "1px solid var(--border)", borderRadius: 10,
                display: "flex", gap: 10, alignItems: "center",
              }}>
                <Avatar cfg={m.avatar_config} size={36} title={m.agent_name} />
                <div style={{ minWidth: 0, flex: 1 }}>
                  <div style={{ fontSize: 12, fontWeight: 800 }}>
                    {m.agent_name}
                    {isCoord && <span style={{
                      marginLeft: 6, fontSize: 9, background: "var(--accent)",
                      color: "white", padding: "1px 6px", borderRadius: 4,
                    }}>COORD</span>}
                  </div>
                  <div style={{ fontSize: 10, color: "var(--ink-3)" }}>
                    {m.role_title || ""}
                  </div>
                  <div style={{ fontSize: 10, color: "var(--ink-4)", marginTop: 2 }}>
                    {t("projectDetail.dailySlice")} {Math.round(Number(m.daily_alloc_pct))}%
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      </Section>

      {/* Coordinator chat */}
      <Section title={t("projectDetail.chatWith", { name: coord?.name || t("common.members") })}>
        <div style={{
          background: "var(--surface)", border: "1px solid var(--border)",
          borderRadius: 10, padding: 10, maxHeight: 360, overflowY: "auto",
          display: "flex", flexDirection: "column", gap: 8, marginBottom: 8,
        }}>
          {(chatBundle?.messages || []).length === 0 && (
            <div style={{ textAlign: "center", color: "var(--ink-4)", padding: 20, fontSize: 12 }}>
              {coord
                ? t("projectDetail.chatHelp", { name: coord.name })
                : t("projectDetail.chatNoCoord")}
            </div>
          )}
          {(chatBundle?.messages || []).map((m) => (
            <div key={m.id} style={{
              alignSelf: m.role === "user" ? "flex-end" : "flex-start",
              maxWidth: "75%",
              padding: "8px 12px",
              borderRadius: 12,
              background: m.role === "user" ? "var(--accent-soft)" : "var(--surface-2)",
              fontSize: 13, whiteSpace: "pre-wrap",
            }}>{m.content}</div>
          ))}
        </div>
        <div style={{ display: "flex", gap: 8 }}>
          <textarea
            value={composer}
            onChange={(e) => setComposer(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                if (composer.trim() && !chat.isPending) chat.mutate(composer.trim());
              }
            }}
            placeholder={chat.isPending ? t("projectDetail.chatWaiting") : t("projectDetail.chatPlaceholder")}
            disabled={chat.isPending || !coord}
            rows={2}
            style={{ flex: 1, resize: "none", padding: 10, borderRadius: 8, border: "1px solid var(--border)", fontSize: 13 }}
          />
          <button className="mbtn primary"
                  disabled={chat.isPending || !composer.trim() || !coord}
                  onClick={() => chat.mutate(composer.trim())}>
            {chat.isPending ? "…" : t("btn.send")}
          </button>
        </div>
      </Section>

      {/* Daily usage chart */}
      <Section title={t("projectDetail.usageTitle")}>
        <UsageStackChart group_by="agent" project_id={pid} days={14} />
      </Section>

      {/* Milestones */}
      <Section title={t("projectDetail.milestones", { count: milestones.length })}>
        <MilestonesPanel pid={pid} milestones={milestones} />
      </Section>

      {/* Reports */}
      <Section title={t("projectDetail.reports", { count: reports.length })}>
        <ReportsPanel pid={pid} reports={reports} />
      </Section>

      {/* Artifacts — agent-produced HTML, slides, markdown, files */}
      <Section title={t("projectDetail.artifacts", { count: artifacts.length })}>
        <ArtifactsPanel artifacts={artifacts} />
      </Section>

      {/* Outputs library */}
      <Section title={t("projectDetail.outputs", { count: outputs.length })}>
        <OutputsPanel pid={pid} outputs={outputs} />
      </Section>

      {/* Activity */}
      <Section title={t("projectDetail.activity", { count: events.length })}>
        <ActivityFeed events={events} />
      </Section>

      {/* Runs */}
      <Section title={t("projectDetail.runs", { count: project.recent_runs?.length || 0 })}>
        {(project.recent_runs || []).length === 0 ? (
          <div style={{ color: "var(--ink-4)", fontSize: 12 }}>{t("projectDetail.noRuns")}</div>
        ) : (
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
            <thead>
              <tr style={{ textAlign: "left", color: "var(--ink-4)" }}>
                <th style={{ padding: 6 }}>{t("projectDetail.tableId")}</th>
                <th>{t("projectDetail.tableWorkflow")}</th>
                <th>{t("projectDetail.tableStatus")}</th>
                <th>{t("projectDetail.tableStarted")}</th>
                <th style={{ textAlign: "right" }}>{t("projectDetail.tableCost")}</th>
              </tr>
            </thead>
            <tbody>
              {project.recent_runs!.map((r) => (
                <tr key={r.id} style={{ borderTop: "1px solid var(--border)", cursor: "pointer" }}
                    onClick={() => navigate(`/runs/${r.id}`)}>
                  <td style={{ padding: 6 }}>#{r.id}</td>
                  <td>{r.workflow_name || "—"}</td>
                  <td>{r.status}</td>
                  <td>{new Date(r.started_at).toLocaleString()}</td>
                  <td style={{ textAlign: "right" }}>${Number(r.total_cost_usd ?? 0).toFixed(3)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Section>
    </div>
  );
}


function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section style={{ marginTop: 28 }}>
      <h3 style={{ fontSize: 11, textTransform: "uppercase", color: "var(--ink-3)",
                   letterSpacing: 1, fontWeight: 800, marginBottom: 12 }}>
        {title}
      </h3>
      {children}
    </section>
  );
}


function StatusBadge({ status }: { status: string }) {
  const color = {
    active: { bg: "var(--accent-soft)", fg: "var(--accent)" },
    paused: { bg: "var(--surface-2)", fg: "var(--ink-3)" },
    done:   { bg: "#e7f5ed", fg: "#0a7a41" },
    archived: { bg: "var(--surface-2)", fg: "var(--ink-4)" },
  }[status] || { bg: "var(--surface-2)", fg: "var(--ink-3)" };
  return (
    <span style={{
      fontSize: 10, fontWeight: 800, letterSpacing: 1.2,
      background: color.bg, color: color.fg,
      padding: "3px 10px", borderRadius: 999, textTransform: "uppercase",
    }}>{status}</span>
  );
}


function ActivityFeed({ events }: {
  events: Array<{id:number; actor:string|null; event_type:string; payload:any; created_at:string}>;
}) {
  const { t } = useTranslation();
  if (!events.length) {
    return <div style={{ color: "var(--ink-4)", fontSize: 12 }}>{t("projectDetail.noActivity")}</div>;
  }
  const icons: Record<string, string> = {
    created: "🎬", status_changed: "🔁", members_updated: "👥",
    coordinator_changed: "👑", milestone_added: "🏁",
    milestone_status_changed: "🏁",
  };
  return (
    <ul style={{ listStyle: "none", padding: 0, margin: 0, display: "flex",
                 flexDirection: "column", gap: 6 }}>
      {events.map((e) => (
        <li key={e.id} style={{
          display: "flex", gap: 8, alignItems: "center",
          fontSize: 12, padding: "6px 10px",
          borderLeft: "3px solid var(--border)", background: "var(--surface)",
        }}>
          <span style={{ fontSize: 14 }}>{icons[e.event_type] || "•"}</span>
          <span style={{ color: "var(--ink-2)", fontWeight: 700 }}>
            {e.event_type.replace(/_/g, " ")}
          </span>
          <span style={{ color: "var(--ink-3)", fontSize: 11 }}>
            {summarizePayload(e.event_type, e.payload)}
          </span>
          <span style={{ flex: 1 }} />
          <span style={{ color: "var(--ink-4)", fontSize: 10 }}>
            {new Date(e.created_at).toLocaleString()}
          </span>
        </li>
      ))}
    </ul>
  );
}

function summarizePayload(type: string, payload: any): string {
  if (!payload) return "";
  if (type === "status_changed") return `→ ${payload.to}`;
  if (type === "members_updated") return `${payload.member_count} members`;
  if (type === "coordinator_changed") return `agent #${payload.to_agent_id}`;
  if (type === "milestone_added") return `"${payload.title}"`;
  if (type === "milestone_status_changed") return `#${payload.milestone_id} → ${payload.to}`;
  if (type === "created") return `${payload.name}`;
  return "";
}


function ArtifactsPanel({ artifacts }: { artifacts: ProjectArtifactRow[] }) {
  const { t } = useTranslation();
  if (!artifacts.length) {
    return <div style={{ color: "var(--ink-4)", fontSize: 12 }}>{t("projectDetail.noArtifacts")}</div>;
  }
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      {artifacts.map((row) => (
        <div key={row.id}>
          <div style={{
            fontSize: 11, color: "var(--ink-4)", marginBottom: 4,
            display: "flex", gap: 8, flexWrap: "wrap",
          }}>
            <span>#{row.id}</span>
            {row.agent_name && <span>· {row.agent_name}{row.agent_role ? ` (${row.agent_role})` : ""}</span>}
            <span>· {new Date(row.created_at).toLocaleString()}</span>
          </div>
          <ArtifactBubble artifact={row.payload} />
        </div>
      ))}
    </div>
  );
}


function OutputsPanel({ pid, outputs }: {
  pid: number;
  outputs: Array<{run_id:number; status:string; started_at:string;
    finished_at:string|null; total_cost_usd:number|string;
    workflow_name:string|null; final_output:string}>;
}) {
  const { t } = useTranslation();
  const [expanded, setExpanded] = useState<number | null>(outputs[0]?.run_id ?? null);

  if (!outputs.length) {
    return <div style={{ color: "var(--ink-4)", fontSize: 12 }}>{t("projectDetail.noOutputs")}</div>;
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      {outputs.map((o) => (
        <div key={o.run_id} style={{
          background: "var(--surface)", border: "1px solid var(--border)",
          borderRadius: 10, overflow: "hidden",
        }}>
          <div style={{
            display: "flex", alignItems: "center", gap: 10,
            padding: "10px 14px", cursor: "pointer",
            background: expanded === o.run_id ? "var(--surface-2)" : "transparent",
          }} onClick={() => setExpanded(expanded === o.run_id ? null : o.run_id)}>
            <div style={{ fontWeight: 700, fontSize: 13 }}>
              {o.workflow_name || `Run #${o.run_id}`}
            </div>
            <div style={{ fontSize: 11, color: "var(--ink-3)" }}>
              run #{o.run_id} · {o.status} · ${Number(o.total_cost_usd ?? 0).toFixed(3)}
            </div>
            <div style={{ flex: 1 }} />
            <a href={`/api/projects/${pid}/outputs/${o.run_id}/download`}
               onClick={(e) => e.stopPropagation()}
               className="mbtn"
               style={{ padding: "4px 10px", fontSize: 11, textDecoration: "none" }}>
              {t("btn.download")}
            </a>
            <div style={{ fontSize: 10, color: "var(--ink-4)" }}>
              {expanded === o.run_id ? "▲" : "▼"}
            </div>
          </div>
          {expanded === o.run_id && (
            <pre style={{
              padding: "10px 16px", borderTop: "1px solid var(--border)",
              fontSize: 12, lineHeight: 1.6, whiteSpace: "pre-wrap",
              wordBreak: "break-word", margin: 0, maxHeight: 360, overflowY: "auto",
              fontFamily: "inherit",
            }}>{o.final_output}</pre>
          )}
        </div>
      ))}
    </div>
  );
}


function MilestonesPanel({ pid, milestones }: {
  pid: number;
  milestones: Array<{id:number; title:string; description?:string;
    status:string; due_date?:string; position:number}>;
}) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const [adding, setAdding] = useState(false);
  const [title, setTitle] = useState("");
  const [desc, setDesc] = useState("");
  const [due, setDue] = useState("");

  const add = useMutation({
    mutationFn: () => api.post(`/projects/${pid}/milestones`,
      { title, description: desc || undefined, due_date: due || undefined }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["project-milestones", pid] });
      setTitle(""); setDesc(""); setDue(""); setAdding(false);
    },
  });
  const setStatus = useMutation({
    mutationFn: ({ mid, status }: { mid: number; status: string }) =>
      api.put(`/projects/${pid}/milestones/${mid}`, { status }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["project-milestones", pid] }),
  });
  const del = useMutation({
    mutationFn: (mid: number) => api.del(`/projects/${pid}/milestones/${mid}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["project-milestones", pid] }),
  });

  const mark = {pending: "◽", in_progress: "⏳", done: "✅"} as any;
  const next = (s: string) =>
    s === "pending" ? "in_progress" : s === "in_progress" ? "done" : "pending";

  return (
    <div>
      {milestones.length === 0 && !adding && (
        <div style={{ color: "var(--ink-4)", fontSize: 12, marginBottom: 8 }}>
          {t("projectDetail.noMilestones")}
        </div>
      )}
      <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
        {milestones.map((m) => (
          <div key={m.id} style={{
            display: "flex", alignItems: "center", gap: 8,
            padding: "8px 10px", background: "var(--surface)",
            border: "1px solid var(--border)", borderRadius: 8, fontSize: 13,
          }}>
            <button className="mbtn"
                    style={{ padding: "2px 8px", fontSize: 11 }}
                    onClick={() => setStatus.mutate({ mid: m.id, status: next(m.status) })}
                    title={t("projectDetail.cycleStatus")}>{mark[m.status]}</button>
            <div style={{ flex: 1 }}>
              <div style={{ fontWeight: 700 }}>{m.title}</div>
              {m.description && (
                <div style={{ fontSize: 11, color: "var(--ink-3)" }}>{m.description}</div>
              )}
              {m.due_date && (
                <div style={{ fontSize: 10, color: "var(--ink-4)" }}>{t("projectDetail.due")} {m.due_date}</div>
              )}
            </div>
            <button className="mbtn danger"
                    style={{ padding: "4px 10px", fontSize: 10 }}
                    onClick={() => del.mutate(m.id)}>{t("btn.remove")}</button>
          </div>
        ))}
      </div>
      {adding ? (
        <div style={{
          marginTop: 10, padding: 10,
          background: "var(--surface)", border: "1px dashed var(--border)",
          borderRadius: 8, display: "flex", flexDirection: "column", gap: 6,
        }}>
          <input placeholder={t("projectDetail.milestoneTitle")}
                 value={title} onChange={(e) => setTitle(e.target.value)} />
          <input placeholder={t("projectDetail.milestoneDescription")}
                 value={desc} onChange={(e) => setDesc(e.target.value)} />
          <input placeholder={t("projectDetail.milestoneDue")} type="date"
                 value={due} onChange={(e) => setDue(e.target.value)} />
          <div style={{ display: "flex", gap: 6, justifyContent: "flex-end" }}>
            <button className="mbtn" onClick={() => setAdding(false)}>{t("btn.cancel")}</button>
            <button className="mbtn primary"
                    disabled={!title.trim() || add.isPending}
                    onClick={() => add.mutate()}>
              {add.isPending ? t("projectDetail.addingMilestone") : t("projectDetail.addMilestone")}
            </button>
          </div>
        </div>
      ) : (
        <button className="mbtn" onClick={() => setAdding(true)}
                style={{ marginTop: 8, padding: "6px 12px", fontSize: 11 }}>
          {t("projectDetail.addMilestone")}
        </button>
      )}
    </div>
  );
}


function ReportsPanel({ pid, reports }: {
  pid: number;
  reports: Array<{id:number; report_date:string; summary_md:string;
    metrics:any; created_at:string}>;
}) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const generate = useMutation({
    mutationFn: () => api.post(`/projects/${pid}/reports/generate`, { force: true }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["project-reports", pid] }),
  });
  const [expanded, setExpanded] = useState<number | null>(
    reports[0]?.id ?? null
  );

  return (
    <div>
      <div style={{ marginBottom: 10 }}>
        <button className="mbtn primary" disabled={generate.isPending}
                onClick={() => generate.mutate()}>
          {generate.isPending ? t("projectDetail.generating") : t("projectDetail.generateReport")}
        </button>
      </div>
      {reports.length === 0 && (
        <div style={{ color: "var(--ink-4)", fontSize: 12 }}>
          {t("projectDetail.noReports")}
        </div>
      )}
      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        {reports.map((r) => (
          <div key={r.id} style={{
            background: "var(--surface)", border: "1px solid var(--border)",
            borderRadius: 10, overflow: "hidden",
          }}>
            <div style={{
              display: "flex", alignItems: "center", gap: 10,
              padding: "10px 14px", cursor: "pointer",
              background: expanded === r.id ? "var(--surface-2)" : "transparent",
            }} onClick={() => setExpanded(expanded === r.id ? null : r.id)}>
              <div style={{ fontWeight: 700, fontSize: 13 }}>{r.report_date}</div>
              <div style={{ fontSize: 11, color: "var(--ink-3)" }}>
                ${(r.metrics?.today_cost_usd ?? 0).toFixed(3)} ·
                {" "}{(r.metrics?.today_tokens ?? 0).toLocaleString()} tokens ·
                {" "}{r.metrics?.today_runs ?? 0} runs
              </div>
              <div style={{ flex: 1 }} />
              <div style={{ fontSize: 10, color: "var(--ink-4)" }}>
                {expanded === r.id ? "▲" : "▼"}
              </div>
            </div>
            {expanded === r.id && (
              <div style={{
                padding: "10px 16px", borderTop: "1px solid var(--border)",
                fontSize: 13, lineHeight: 1.6, whiteSpace: "pre-wrap",
              }}>{r.summary_md}</div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
