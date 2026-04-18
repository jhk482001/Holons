import { useTranslation } from "react-i18next";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { AgentsAPI, api } from "../api/client";
import Avatar from "../components/Avatar";

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
        {skills.map((s) => (
          <div
            key={s.id}
            data-testid={`skill-row-${s.id}`}
            style={{
              padding: "12px 14px",
              background: s.approved_by_user ? "var(--good-soft)" : "var(--surface-2)",
              border: "1px solid " + (s.approved_by_user ? "rgba(95, 181, 126, 0.3)" : "var(--border)"),
              borderRadius: 10,
              display: "flex",
              alignItems: "center",
              gap: 12,
            }}
          >
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
            </div>
            {!s.approved_by_user && (
              <button
                data-testid={`approve-skill-${s.id}`}
                onClick={() => approve.mutate(s.id)}
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
              onClick={() => reject.mutate(s.id)}
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
        ))}
      </div>
    </div>
  );
}
