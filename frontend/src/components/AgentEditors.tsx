import { useTranslation } from "react-i18next";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Agent, AvatarConfig, api, ModelClientsAPI } from "../api/client";
import AvatarBuilder from "./AvatarBuilder";

/**
 * Editable Overview block — description, system prompt, and (optionally)
 * the avatar builder. Used by both the Agents detail page and the Dialog
 * Center floating settings panel.
 */
export function AgentOverviewEditor({
  agent,
  showAvatar = true,
}: {
  agent: Agent;
  showAvatar?: boolean;
}) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const [avatarCfg, setAvatarCfg] = useState<AvatarConfig>(
    (agent.avatar_config as AvatarConfig) || {},
  );
  const [avatarDirty, setAvatarDirty] = useState(false);
  const [description, setDescription] = useState(agent.description || "");
  const [systemPrompt, setSystemPrompt] = useState(agent.system_prompt || "");
  const [clientId, setClientId] = useState<number | null>(agent.model_client_id);
  const [modelId, setModelId] = useState<string>(agent.primary_model_id || "");
  const [fallbackModelId, setFallbackModelId] = useState<string>(
    (agent as any).fallback_model_id || "",
  );
  const textDirty =
    description !== (agent.description || "") ||
    systemPrompt !== (agent.system_prompt || "");
  const clientDirty =
    clientId !== agent.model_client_id ||
    modelId !== (agent.primary_model_id || "") ||
    fallbackModelId !== ((agent as any).fallback_model_id || "");

  const { data: clients = [] } = useQuery({
    queryKey: ["my-model-clients"],
    queryFn: ModelClientsAPI.list,
  });
  const selectedClient = clients.find((c) => c.id === clientId);
  const availableModels = selectedClient?.config.models || [];

  const saveClient = useMutation({
    mutationFn: () =>
      api.put(`/agents/${agent.id}`, {
        model_client_id: clientId,
        primary_model_id: modelId,
        fallback_model_id: fallbackModelId || null,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["agent", agent.id] });
      qc.invalidateQueries({ queryKey: ["agents"] });
    },
  });

  const saveAvatar = useMutation({
    mutationFn: () => api.put(`/agents/${agent.id}`, { avatar_config: avatarCfg }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["agent", agent.id] });
      qc.invalidateQueries({ queryKey: ["agents"] });
      setAvatarDirty(false);
    },
  });

  const saveText = useMutation({
    mutationFn: () =>
      api.put(`/agents/${agent.id}`, {
        description,
        system_prompt: systemPrompt,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["agent", agent.id] });
      qc.invalidateQueries({ queryKey: ["agents"] });
    },
  });

  return (
    <div className="overview-card">
      <section>
        <h3>{t("overview.description")}</h3>
        <textarea
          data-testid="agent-description-input"
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          placeholder={t("overview.descPlaceholder")}
          style={{
            width: "100%",
            minHeight: 80,
            padding: "10px 14px",
            borderRadius: 10,
            border: "1px solid var(--border)",
            background: "var(--surface)",
            font: "inherit",
            fontSize: 13,
            color: "var(--ink)",
            outline: "none",
            resize: "vertical",
            lineHeight: 1.6,
          }}
        />
      </section>
      <section>
        <h3>{t("overview.systemPrompt")}</h3>
        <textarea
          data-testid="agent-system-prompt-input"
          value={systemPrompt}
          onChange={(e) => setSystemPrompt(e.target.value)}
          placeholder={t("overview.promptPlaceholder")}
          style={{
            width: "100%",
            minHeight: 160,
            padding: "10px 14px",
            borderRadius: 10,
            border: "1px solid var(--border)",
            background: "var(--surface-2)",
            fontFamily: "var(--font-mono)",
            fontSize: 12,
            color: "var(--ink-2)",
            outline: "none",
            resize: "vertical",
            lineHeight: 1.6,
          }}
        />
        {textDirty && (
          <div className="avatar-save-bar">
            <span>{t("overview.unsavedText")}</span>
            <button
              data-testid="save-agent-text-btn"
              onClick={() => saveText.mutate()}
              disabled={saveText.isPending}
            >
              {saveText.isPending ? t("overview.saving") : t("overview.saveText")}
            </button>
          </div>
        )}
      </section>
      <section>
        <h3>{t("overview.llmConnection")}</h3>
        <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
          <select
            data-testid="agent-client-select"
            value={clientId ?? ""}
            onChange={(e) => {
              const next = Number(e.target.value);
              setClientId(next);
              const nextClient = clients.find((c) => c.id === next);
              const firstModel = (nextClient?.config.models || [])[0]?.id || "";
              setModelId(firstModel);
            }}
            style={{
              flex: "1 1 200px",
              padding: "8px 10px",
              border: "1px solid var(--border)",
              borderRadius: 8,
              fontSize: 12,
              background: "var(--surface)",
            }}
          >
            {clients.length === 0 && (
              <option value="">{t("overview.noConnection")}</option>
            )}
            {clients.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}（{c.kind}）
              </option>
            ))}
          </select>
          <select
            data-testid="agent-model-select"
            value={modelId}
            onChange={(e) => setModelId(e.target.value)}
            style={{
              flex: "1 1 200px",
              padding: "8px 10px",
              border: "1px solid var(--border)",
              borderRadius: 8,
              fontSize: 12,
              background: "var(--surface)",
            }}
          >
            {availableModels.length === 0 && (
              <option value="">{t("overview.noModel")}</option>
            )}
            {availableModels.map((m) => (
              <option key={m.id} value={m.id}>
                {m.label || m.id}
              </option>
            ))}
          </select>
        </div>
        <div style={{ marginTop: 10 }}>
          <label style={{
            display: "block",
            fontSize: 10, fontWeight: 700, letterSpacing: 0.6,
            color: "var(--ink-3)", textTransform: "uppercase",
            marginBottom: 4,
          }}>
            {t("overview.fallbackModel")}
          </label>
          <select
            data-testid="agent-fallback-model-select"
            value={fallbackModelId}
            onChange={(e) => setFallbackModelId(e.target.value)}
            style={{
              width: "100%",
              padding: "8px 10px",
              border: "1px solid var(--border)",
              borderRadius: 8,
              fontSize: 12,
              background: "var(--surface)",
            }}
          >
            <option value="">{t("overview.fallbackNone")}</option>
            {availableModels
              .filter((m) => m.id !== modelId)
              .map((m) => (
                <option key={m.id} value={m.id}>
                  {m.label || m.id}
                </option>
              ))}
          </select>
          <div style={{ fontSize: 10, color: "var(--ink-4)", marginTop: 3, lineHeight: 1.5 }}>
            {t("overview.fallbackHint")}
          </div>
        </div>
        {clientDirty && (
          <div className="avatar-save-bar">
            <span>{t("overview.unsavedConnection")}</span>
            <button
              data-testid="save-agent-client-btn"
              onClick={() => saveClient.mutate()}
              disabled={saveClient.isPending || !clientId}
            >
              {saveClient.isPending ? t("overview.saving") : t("overview.saveConnection")}
            </button>
          </div>
        )}
      </section>
      {showAvatar && (
        <section>
          <h3>{t("overview.appearance")}</h3>
          <AvatarBuilder
            value={avatarCfg}
            onChange={(c) => {
              setAvatarCfg(c);
              setAvatarDirty(true);
            }}
          />
          {avatarDirty && (
            <div className="avatar-save-bar">
              <span>{t("overview.unsavedAppearance")}</span>
              <button
                onClick={() => saveAvatar.mutate()}
                disabled={saveAvatar.isPending}
              >
                {saveAvatar.isPending ? t("overview.saving") : t("overview.saveAppearance")}
              </button>
            </div>
          )}
        </section>
      )}
    </div>
  );
}

/**
 * Editable Skills list — extract, approve, reject. Used by both the Agents
 * detail page and the Dialog Center floating settings panel.
 */
export function AgentSkillsEditor({ agentId }: { agentId: number }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const { data: skills = [] } = useQuery({
    queryKey: ["agent-skills", agentId],
    queryFn: () => api.get<any[]>(`/agents/${agentId}/skills`),
  });
  const extract = useMutation({
    mutationFn: () => api.post(`/agents/${agentId}/skills/extract`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["agent-skills", agentId] }),
  });
  const approve = useMutation({
    mutationFn: (sid: number) => api.post(`/skills/${sid}/approve`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["agent-skills", agentId] }),
  });
  const reject = useMutation({
    mutationFn: (sid: number) => api.post(`/skills/${sid}/reject`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["agent-skills", agentId] }),
  });

  return (
    <div className="skills-tab">
      <button
        className="extract-btn"
        disabled={extract.isPending}
        onClick={() => extract.mutate()}
      >
        {extract.isPending ? t("skills.learning") : t("skills.learn")}
      </button>
      {skills.length === 0 ? (
        <div className="empty">{t("skills.noSkills")}</div>
      ) : (
        skills.map((s: any) => (
          <div key={s.id} className={`skill-row ${s.approved_by_user ? "approved" : ""}`}>
            <div className="skill-main">
              <div className="skill-name">{s.name}</div>
              <div className="skill-info">
                {s.source} · {t("skills.confidence")} {(s.confidence || 0).toFixed(2)} · {t("skills.used", { count: s.times_used })}
              </div>
            </div>
            {!s.approved_by_user && (
              <button onClick={() => approve.mutate(s.id)} className="approve-btn">
                {t("skills.approve")}
              </button>
            )}
            <button
              onClick={() => reject.mutate(s.id)}
              className="approve-btn"
              style={{
                background: "white",
                color: "var(--danger)",
                border: "1px solid rgba(232, 100, 80, 0.4)",
                marginLeft: 6,
              }}
            >
              {t("skills.reject")}
            </button>
          </div>
        ))
      )}
    </div>
  );
}
