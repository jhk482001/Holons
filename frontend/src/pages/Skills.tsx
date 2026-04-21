import { useTranslation } from "react-i18next";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { AgentsAPI, api } from "../api/client";
import Avatar from "../components/Avatar";
import Markdown from "../components/Markdown";

interface Skill {
  id: number;
  agent_id: number;
  slug: string;
  name: string;
  description: string;
  content_md: string;
  source: string;
  confidence: number;
  approved_by_user: boolean;
  times_used: number;
  source_run_ids?: number[];
  extraction_model_id?: string | null;
  extraction_input_tokens?: number | null;
  extraction_output_tokens?: number | null;
  extraction_cost_usd?: number | null;
  extraction_prompt_preview?: string | null;
  extraction_response_preview?: string | null;
  extraction_at?: string | null;
}

export default function Skills() {
  const { t } = useTranslation();
  const { data: agents = [] } = useQuery({ queryKey: ["agents"], queryFn: AgentsAPI.list });

  return (
    <div className="page">
      <h1>{t("skills.title")}</h1>
      <div className="subtitle">{t("skills.subtitle") || "Skills internalized by each agent — approve, reject, export"}</div>

      <div style={{ display: "flex", flexDirection: "column", gap: 24, marginTop: 20 }}>
        {agents.filter((a) => !a.is_lead).map((a) => (
          <AgentSkillsBlock
            key={a.id}
            agentId={a.id}
            agentName={a.name}
            avatarCfg={a.avatar_config}
            roleTitle={a.role_title}
          />
        ))}
      </div>
    </div>
  );
}

function AgentSkillsBlock({
  agentId,
  agentName,
  avatarCfg,
  roleTitle,
}: {
  agentId: number;
  agentName: string;
  avatarCfg: Record<string, string>;
  roleTitle: string | null;
}) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const { data: skills = [] } = useQuery({
    queryKey: ["skills", agentId],
    queryFn: () => api.get<Skill[]>(`/agents/${agentId}/skills`),
  });

  const extract = useMutation({
    mutationFn: () => api.post(`/agents/${agentId}/skills/extract`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["skills", agentId] }),
  });

  const approve = useMutation({
    mutationFn: (sid: number) => api.post(`/skills/${sid}/approve`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["skills", agentId] }),
  });

  const reject = useMutation({
    mutationFn: (sid: number) => api.post(`/skills/${sid}/reject`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["skills", agentId] }),
  });

  const [expandedId, setExpandedId] = useState<number | null>(null);
  const [historyOpen, setHistoryOpen] = useState(false);

  async function exportBundle() {
    const data = await api.get<unknown>(`/agents/${agentId}/skills/export`);
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `skills-${agentName.replace(/[^\w-]+/g, "_")}.json`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  }

  // Group self-learned skills by extraction_at (second precision) to
  // surface each extraction round as a history entry.
  const extractionRounds = useMemo(() => {
    const selfLearned = skills.filter((s) => s.source === "self_learned" && s.extraction_at);
    const byRound = new Map<string, Skill[]>();
    for (const s of selfLearned) {
      // Bucket by extraction_at truncated to the second — skills saved
      // in one extract_for_agent() call share the NOW() timestamp.
      const key = new Date(s.extraction_at!).toISOString().slice(0, 19);
      const bucket = byRound.get(key) ?? [];
      bucket.push(s);
      byRound.set(key, bucket);
    }
    return Array.from(byRound.entries())
      .map(([ts, items]) => {
        const cost = items.reduce((a, s) => a + (s.extraction_cost_usd ?? 0), 0);
        const inTok = items.reduce((a, s) => a + (s.extraction_input_tokens ?? 0), 0);
        const outTok = items.reduce((a, s) => a + (s.extraction_output_tokens ?? 0), 0);
        return {
          ts,
          skills: items,
          cost,
          inTok,
          outTok,
          model: items[0]?.extraction_model_id,
          sourceStepCount: items[0]?.source_run_ids?.length ?? 0,
        };
      })
      .sort((a, b) => b.ts.localeCompare(a.ts));
  }, [skills]);

  return (
    <div
      data-testid={`skills-block-${agentId}`}
      style={{
        background: "var(--surface)",
        border: "1px solid var(--border)",
        borderRadius: 18,
        padding: 20,
      }}
    >
      <div style={{ display: "flex", alignItems: "center", marginBottom: 16, gap: 12 }}>
        <Avatar cfg={avatarCfg} size={44} title={agentName} />
        <div style={{ flex: 1 }}>
          <h3 style={{ fontSize: 16, fontWeight: 800 }}>{agentName}</h3>
          <div style={{ fontSize: 11, color: "var(--ink-4)", marginTop: 2 }}>
            {roleTitle || "—"} · {t("skills.skillCount", { count: skills.length })}
            {" · "}
            {t("skills.approved", { count: skills.filter((s) => s.approved_by_user).length })}
          </div>
        </div>
        <button
          data-testid={`export-skills-${agentId}`}
          onClick={exportBundle}
          disabled={skills.length === 0}
          style={{
            padding: "8px 14px",
            fontSize: 11,
            fontWeight: 700,
            color: "var(--ink-2)",
            background: "white",
            border: "1px solid var(--border)",
            borderRadius: 8,
            cursor: skills.length === 0 ? "not-allowed" : "pointer",
            opacity: skills.length === 0 ? 0.5 : 1,
          }}
        >
          {t("agents.export")}
        </button>
        <button
          data-testid={`extract-skills-${agentId}`}
          onClick={() => extract.mutate()}
          disabled={extract.isPending}
          style={{
            padding: "8px 14px",
            fontSize: 11,
            fontWeight: 700,
            color: "var(--accent)",
            background: "var(--accent-soft)",
            border: "1px solid var(--accent-line)",
            borderRadius: 8,
            cursor: "pointer",
            marginLeft: 6,
          }}
        >
          {extract.isPending ? t("skills.learning") : t("skills.learn")}
        </button>
      </div>

      {skills.length === 0 && (
        <div style={{ fontSize: 12, color: "var(--ink-4)", padding: "10px 0", textAlign: "center" }}>
          {t("skills.noSkills")}
        </div>
      )}

      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        {skills.map((s) => {
          const expanded = expandedId === s.id;
          return (
            <div
              key={s.id}
              data-testid={`skill-row-${s.id}`}
              style={{
                background: s.approved_by_user ? "var(--good-soft)" : "var(--surface-2)",
                border: "1px solid " + (s.approved_by_user ? "rgba(95, 181, 126, 0.3)" : "var(--border)"),
                borderRadius: 10,
                overflow: "hidden",
              }}
            >
              <div
                data-testid={`skill-row-header-${s.id}`}
                onClick={() => setExpandedId(expanded ? null : s.id)}
                style={{
                  padding: "12px 14px",
                  display: "flex",
                  alignItems: "center",
                  gap: 12,
                  cursor: "pointer",
                  userSelect: "none",
                }}
              >
                <span style={{
                  fontSize: 12, color: "var(--ink-3)",
                  transform: expanded ? "rotate(90deg)" : "rotate(0deg)",
                  transition: "transform 0.12s ease",
                  display: "inline-block", width: 12,
                }}>▶</span>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                    <div style={{ fontSize: 13, fontWeight: 800 }}>{s.name}</div>
                    {s.approved_by_user && (
                      <span style={{
                        fontSize: 9,
                        fontWeight: 800,
                        letterSpacing: 1,
                        background: "rgba(95, 181, 126, 0.15)",
                        color: "var(--good)",
                        padding: "2px 8px",
                        borderRadius: 999,
                      }}>APPROVED</span>
                    )}
                  </div>
                  {s.description && (
                    <div style={{ fontSize: 11, color: "var(--ink-2)", marginTop: 3, lineHeight: 1.4 }}>
                      {s.description}
                    </div>
                  )}
                  <div style={{ fontSize: 10, color: "var(--ink-4)", marginTop: 4 }}>
                    {s.source} · {t("skills.confidence")} {(s.confidence || 0).toFixed(2)} · {t("skills.used", { count: s.times_used })}
                  </div>
                  {(s.extraction_model_id || (s.extraction_input_tokens ?? 0) > 0) && (
                    <div style={{
                      fontSize: 10,
                      color: "var(--ink-4)",
                      marginTop: 3,
                      display: "flex",
                      gap: 10,
                      flexWrap: "wrap",
                    }}>
                      {s.extraction_model_id && (
                        <span title={t("skills.auditModel")}>🧠 {s.extraction_model_id}</span>
                      )}
                      {(s.extraction_input_tokens ?? 0) > 0 && (
                        <span title={t("skills.auditTokens")}>
                          ↓{s.extraction_input_tokens} ↑{s.extraction_output_tokens}
                        </span>
                      )}
                      {(s.extraction_cost_usd ?? 0) > 0 && (
                        <span title={t("skills.auditCost")}>${(s.extraction_cost_usd ?? 0).toFixed(4)}</span>
                      )}
                      {s.source_run_ids && s.source_run_ids.length > 0 && (
                        <span title={t("skills.auditSource")}>
                          ← {s.source_run_ids.length} {t("skills.sourceSteps")}
                        </span>
                      )}
                      {s.extraction_at && (
                        <span>{new Date(s.extraction_at).toLocaleString()}</span>
                      )}
                    </div>
                  )}
                </div>
                {!s.approved_by_user && (
                  <button
                    data-testid={`approve-skill-${s.id}`}
                    onClick={(e) => { e.stopPropagation(); approve.mutate(s.id); }}
                    disabled={approve.isPending}
                    style={{
                      padding: "6px 14px",
                      fontSize: 11,
                      fontWeight: 700,
                      background: "var(--accent)",
                      color: "white",
                      border: "none",
                      borderRadius: 6,
                      cursor: "pointer",
                    }}
                  >
                    {t("skills.approve")}
                  </button>
                )}
                <button
                  data-testid={`reject-skill-${s.id}`}
                  onClick={(e) => { e.stopPropagation(); reject.mutate(s.id); }}
                  disabled={reject.isPending}
                  style={{
                    padding: "6px 14px",
                    fontSize: 11,
                    fontWeight: 700,
                    background: "white",
                    color: "var(--danger)",
                    border: "1px solid rgba(232, 100, 80, 0.4)",
                    borderRadius: 6,
                    cursor: "pointer",
                  }}
                >
                  {t("skills.reject")}
                </button>
              </div>
              {expanded && (
                <div
                  data-testid={`skill-row-body-${s.id}`}
                  style={{
                    borderTop: "1px solid " + (s.approved_by_user ? "rgba(95, 181, 126, 0.3)" : "var(--border)"),
                    padding: "14px 18px",
                    background: "var(--surface)",
                  }}
                >
                  {s.content_md ? (
                    <Markdown content={s.content_md} />
                  ) : (
                    <div style={{ fontSize: 11, color: "var(--ink-4)", fontStyle: "italic" }}>
                      {t("skills.noContent")}
                    </div>
                  )}
                </div>
              )}
            </div>
          );
        })}
      </div>

      {extractionRounds.length > 0 && (
        <div style={{ marginTop: 16 }}>
          <button
            data-testid={`extraction-history-toggle-${agentId}`}
            onClick={() => setHistoryOpen(!historyOpen)}
            style={{
              fontSize: 11,
              fontWeight: 700,
              color: "var(--ink-2)",
              background: "transparent",
              border: "none",
              cursor: "pointer",
              padding: "4px 0",
              display: "flex",
              alignItems: "center",
              gap: 6,
            }}
          >
            <span style={{
              fontSize: 10,
              transform: historyOpen ? "rotate(90deg)" : "rotate(0deg)",
              transition: "transform 0.12s ease",
              display: "inline-block", width: 10,
            }}>▶</span>
            {t("skills.extractionHistory", { count: extractionRounds.length })}
          </button>
          {historyOpen && (
            <div style={{
              marginTop: 8,
              background: "var(--surface-2)",
              border: "1px solid var(--border)",
              borderRadius: 8,
              overflow: "hidden",
            }}>
              <table style={{ width: "100%", fontSize: 11, borderCollapse: "collapse" }}>
                <thead>
                  <tr style={{ background: "var(--surface)", color: "var(--ink-4)" }}>
                    <th style={{ padding: "6px 12px", textAlign: "left" }}>{t("skills.histWhen")}</th>
                    <th style={{ padding: "6px 12px", textAlign: "left" }}>{t("skills.histModel")}</th>
                    <th style={{ padding: "6px 12px", textAlign: "right" }}>{t("skills.histSources")}</th>
                    <th style={{ padding: "6px 12px", textAlign: "right" }}>{t("skills.histProduced")}</th>
                    <th style={{ padding: "6px 12px", textAlign: "right" }}>{t("skills.histTokens")}</th>
                    <th style={{ padding: "6px 12px", textAlign: "right" }}>{t("skills.histCost")}</th>
                  </tr>
                </thead>
                <tbody>
                  {extractionRounds.map((r) => (
                    <tr key={r.ts} style={{ borderTop: "1px solid var(--border)" }}>
                      <td style={{ padding: "6px 12px" }}>{new Date(r.ts + "Z").toLocaleString()}</td>
                      <td style={{ padding: "6px 12px", color: "var(--ink-3)" }}>{r.model || "—"}</td>
                      <td style={{ padding: "6px 12px", textAlign: "right" }}>{r.sourceStepCount}</td>
                      <td style={{ padding: "6px 12px", textAlign: "right", fontWeight: 700 }}>{r.skills.length}</td>
                      <td style={{ padding: "6px 12px", textAlign: "right", color: "var(--ink-3)" }}>
                        ↓{r.inTok} ↑{r.outTok}
                      </td>
                      <td style={{ padding: "6px 12px", textAlign: "right", fontFamily: "var(--font-mono)" }}>
                        ${r.cost.toFixed(4)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
