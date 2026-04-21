import { useTranslation } from "react-i18next";
import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { AgentsAPI, Agent, AvatarConfig, api, ModelClientsAPI } from "../api/client";
import Avatar from "../components/Avatar";
import Modal from "../components/Modal";
import AvatarBuilder from "../components/AvatarBuilder";

export default function Agents() {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const { data: agents = [] } = useQuery({ queryKey: ["agents"], queryFn: AgentsAPI.list });
  const navigate = useNavigate();
  const [createOpen, setCreateOpen] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const importAgent = useMutation({
    mutationFn: (bundle: unknown) => api.post<{ id: number }>("/agents/import", bundle),
    onSuccess: ({ id }) => {
      qc.invalidateQueries({ queryKey: ["agents"] });
      navigate(`/agents/${id}`);
    },
  });

  async function exportAgent(a: Agent, e: React.MouseEvent) {
    e.stopPropagation();
    const bundle = await api.get<unknown>(`/agents/${a.id}/export`);
    const blob = new Blob([JSON.stringify(bundle, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const el = document.createElement("a");
    el.href = url;
    el.download = `agent-${a.name.replace(/[^\w-]+/g, "_")}.json`;
    document.body.appendChild(el);
    el.click();
    document.body.removeChild(el);
    URL.revokeObjectURL(url);
  }

  async function onImportFileChange(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    try {
      const text = await file.text();
      const bundle = JSON.parse(text);
      importAgent.mutate(bundle);
    } catch {
      alert(t("agents.importError"));
    }
  }

  return (
    <div className="page">
      <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", marginBottom: 4 }}>
        <div>
          <h1>{t("agents.title")}</h1>
          <div className="subtitle">{t("agents.subtitle")}</div>
        </div>
        <div style={{ display: "flex", gap: 8 }}>
          <input
            ref={fileInputRef}
            type="file"
            accept="application/json"
            data-testid="import-agent-input"
            onChange={onImportFileChange}
            style={{ display: "none" }}
          />
          <button
            data-testid="import-agent-btn"
            onClick={() => fileInputRef.current?.click()}
            className="mbtn"
            style={{ padding: "10px 16px", fontSize: 13, fontWeight: 800 }}
          >
            {t("agents.import")}
          </button>
          <button
            data-testid="new-agent-btn"
            onClick={() => setCreateOpen(true)}
            style={{
              padding: "10px 18px",
              background: "var(--accent)",
              color: "white",
              border: "1px solid var(--accent)",
              borderRadius: 10,
              fontSize: 13,
              fontWeight: 800,
              cursor: "pointer",
            }}
          >
            {t("agents.createNew")}
          </button>
        </div>
      </div>

      {(() => {
        const owned = agents.filter((a) => !a.borrowed);
        const borrowed = agents.filter((a) => a.borrowed);
        return (
          <>
            <div style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fill, minmax(260px, 1fr))",
              gap: 16,
              marginTop: 20,
            }}>
              {owned.map((a) => (
                <AgentCard
                  key={a.id}
                  agent={a}
                  onClick={() => navigate(`/agents/${a.id}`)}
                  onExport={(e) => exportAgent(a, e)}
                />
              ))}
            </div>

            {borrowed.length > 0 && (
              <section style={{ marginTop: 36 }}>
                <div style={{ display: "flex", alignItems: "baseline", gap: 10, marginBottom: 10 }}>
                  <h2 style={{ fontSize: 18, fontWeight: 800, color: "var(--ink)" }}>{t("agents.borrowed")}</h2>
                  <span style={{ fontSize: 12, color: "var(--ink-3)" }}>
                    {t("agents.borrowedDesc")}
                  </span>
                </div>
                <div style={{
                  display: "grid",
                  gridTemplateColumns: "repeat(auto-fill, minmax(260px, 1fr))",
                  gap: 16,
                }}>
                  {borrowed.map((a) => (
                    <AgentCard
                      key={a.id}
                      agent={a}
                      onClick={() => navigate(`/dialog?agent=${a.id}`)}
                      borrowed
                    />
                  ))}
                </div>
              </section>
            )}
          </>
        );
      })()}

      <CreateAgentModal
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        onCreated={(id) => {
          setCreateOpen(false);
          navigate(`/agents/${id}`);
        }}
      />
    </div>
  );
}

function AgentCard({
  agent, onClick, onExport, borrowed,
}: {
  agent: Agent;
  onClick: () => void;
  onExport?: (e: React.MouseEvent) => void;
  borrowed?: boolean;
}) {
  const { t } = useTranslation();
  return (
    <div
      data-testid={`agent-card-${agent.id}`}
      onClick={onClick}
      style={{
        background: "var(--surface)",
        border: borrowed ? "1px dashed rgba(255, 122, 89, 0.4)" : "1px solid var(--border)",
        borderRadius: 20,
        padding: 18,
        boxShadow: "var(--shadow-sm)",
        position: "relative",
        cursor: "pointer",
        transition: "transform 0.15s, box-shadow 0.15s",
      }}
      onMouseEnter={(e) => { e.currentTarget.style.transform = "translateY(-2px)"; e.currentTarget.style.boxShadow = "var(--shadow-md)"; }}
      onMouseLeave={(e) => { e.currentTarget.style.transform = ""; e.currentTarget.style.boxShadow = "var(--shadow-sm)"; }}
    >
      {agent.is_lead && (
        <div style={{
          position: "absolute",
          top: 14, right: 14,
          fontSize: 9,
          fontWeight: 800,
          letterSpacing: 1.5,
          background: "var(--accent-soft)",
          color: "var(--accent)",
          padding: "3px 10px",
          borderRadius: 999,
        }}>{t("agents.badge.lead")}</div>
      )}
      {borrowed && (
        <div style={{
          position: "absolute",
          top: 14, right: 14,
          fontSize: 9,
          fontWeight: 800,
          letterSpacing: 1.5,
          background: "rgba(255, 122, 89, 0.12)",
          color: "var(--accent)",
          padding: "3px 10px",
          borderRadius: 999,
        }}>{t("agents.badge.borrowed")}</div>
      )}
      {!agent.is_lead && !borrowed && onExport && (
        <button
          data-testid={`export-agent-${agent.id}`}
          onClick={onExport}
          title={t("agents.export")}
          style={{
            position: "absolute",
            top: 12, right: 12,
            width: 28,
            height: 28,
            background: "white",
            border: "1px solid var(--border)",
            borderRadius: 8,
            color: "var(--ink-3)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            cursor: "pointer",
          }}
        >
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
            <polyline points="7 10 12 15 17 10"/>
            <line x1="12" y1="15" x2="12" y2="3"/>
          </svg>
        </button>
      )}
      <div style={{ marginBottom: 12 }}>
        <Avatar cfg={agent.avatar_config} size={60} title={agent.name} />
      </div>
      <div style={{ fontSize: 15, fontWeight: 800 }}>{agent.name}</div>
      <div style={{ fontSize: 11, color: "var(--ink-3)", marginBottom: 12 }}>{agent.role_title}</div>
      {borrowed ? (
        <div style={{ fontSize: 11, color: "var(--ink-3)" }}>
          {t("agents.owner")}<strong style={{ color: "var(--ink)" }}>
            {agent.owner_display_name || agent.owner_username || "—"}
          </strong>
        </div>
      ) : (
        <StatusPill status={agent.status} />
      )}
    </div>
  );
}

// Status visual: small filled dot with a subtle halo, plus the status
// label next to it. Colors are stable across light/dark themes.
const STATUS_STYLE: Record<string, { color: string; labelKey: string }> = {
  active:          { color: "#5fb57e", labelKey: "agentStatus.active" },
  paused:          { color: "#e0a835", labelKey: "agentStatus.paused" },
  offline:         { color: "#9aa0a6", labelKey: "agentStatus.offline" },
  off_duty:        { color: "#9aa0a6", labelKey: "agentStatus.offDuty" },
  budget_exceeded: { color: "#e86450", labelKey: "agentStatus.budgetExceeded" },
  quota_exceeded:  { color: "#e86450", labelKey: "agentStatus.quotaExceeded" },
};

function StatusPill({ status }: { status: string }) {
  const { t } = useTranslation();
  const s = STATUS_STYLE[status] ?? { color: "#9aa0a6", labelKey: "" };
  const label = s.labelKey ? t(s.labelKey) : status;
  const isActive = status === "active";
  return (
    <div
      data-testid="agent-status-pill"
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 6,
        fontSize: 11,
        fontWeight: 700,
        color: "var(--ink-2)",
      }}
    >
      <span
        style={{
          width: 8,
          height: 8,
          borderRadius: "50%",
          background: s.color,
          boxShadow: isActive ? `0 0 0 4px ${s.color}22, 0 0 6px ${s.color}88` : "none",
          display: "inline-block",
        }}
      />
      {label}
    </div>
  );
}

function CreateAgentModal({
  open,
  onClose,
  onCreated,
}: {
  open: boolean;
  onClose: () => void;
  onCreated: (id: number) => void;
}) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const [name, setName] = useState("");
  const [roleTitle, setRoleTitle] = useState("");
  const [description, setDescription] = useState("");
  const [systemPrompt, setSystemPrompt] = useState("");
  const [clientId, setClientId] = useState<number | null>(null);
  const [model, setModel] = useState("");
  const [avatar, setAvatar] = useState<AvatarConfig>({});

  // Load the model clients this user is allowed to use. Seeds default
  // selection to the first default_for_new_users entry, falling back to
  // the first available client.
  const { data: clients = [] } = useQuery({
    queryKey: ["my-model-clients"],
    queryFn: ModelClientsAPI.list,
  });
  useEffect(() => {
    if (clientId || clients.length === 0) return;
    const def = clients.find((c) => c.default_for_new_users) || clients[0];
    setClientId(def.id);
    const firstModel = (def.config.models || [])[0]?.id || "";
    setModel(firstModel);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [clients.length]);

  const selectedClient = clients.find((c) => c.id === clientId);
  const availableModels = selectedClient?.config.models || [];

  const create = useMutation({
    mutationFn: () =>
      AgentsAPI.create({
        name,
        role_title: roleTitle,
        description,
        system_prompt: systemPrompt,
        primary_model_id: model,
        model_client_id: clientId ?? undefined,
        avatar_config: avatar as Record<string, string>,
      }),
    onSuccess: ({ id }) => {
      qc.invalidateQueries({ queryKey: ["agents"] });
      setName("");
      setRoleTitle("");
      setDescription("");
      setSystemPrompt("");
      setAvatar({});
      onCreated(id);
    },
  });

  const canSubmit = name.trim().length > 0 && !create.isPending;

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={t("agentCreate.title")}
      subtitle={t("agentCreate.subtitle")}
      size="lg"
      footer={
        <>
          <button className="mbtn" onClick={onClose} disabled={create.isPending}>{t("btn.cancel")}</button>
          <button
            className="mbtn primary"
            onClick={() => create.mutate()}
            disabled={!canSubmit}
            data-testid="create-agent-submit"
          >
            {create.isPending ? t("agentCreate.submitting") : t("agentCreate.submit")}
          </button>
        </>
      }
    >
      <div className="modal-field">
        <label>{t("agentCreate.name")}</label>
        <input
          data-testid="new-agent-name"
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder={t("agentCreate.namePlaceholder")}
          autoFocus
        />
      </div>
      <div className="modal-field">
        <label>{t("agentCreate.role")}</label>
        <input
          data-testid="new-agent-role"
          value={roleTitle}
          onChange={(e) => setRoleTitle(e.target.value)}
          placeholder={t("agentCreate.rolePlaceholder")}
        />
      </div>
      <div className="modal-field">
        <label>{t("agentCreate.description")}</label>
        <textarea
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          placeholder={t("agentCreate.descriptionPlaceholder")}
          style={{ minHeight: 60 }}
        />
      </div>
      <div className="modal-field">
        <label>{t("agentCreate.systemPrompt")}</label>
        <textarea
          value={systemPrompt}
          onChange={(e) => setSystemPrompt(e.target.value)}
          placeholder={t("agentCreate.systemPromptPlaceholder")}
        />
      </div>
      <div className="modal-field">
        <label>{t("agentCreate.modelClient")}</label>
        <select
          value={clientId ?? ""}
          onChange={(e) => {
            const next = Number(e.target.value);
            setClientId(next);
            const nextClient = clients.find((c) => c.id === next);
            const firstModel = (nextClient?.config.models || [])[0]?.id || "";
            setModel(firstModel);
          }}
          data-testid="new-agent-client"
        >
          {clients.length === 0 && <option value="">{t("agentCreate.modelClientEmpty")}</option>}
          {clients.map((c) => (
            <option key={c.id} value={c.id}>
              {c.name}（{c.kind}）
            </option>
          ))}
        </select>
      </div>
      <div className="modal-field">
        <label>{t("agentCreate.model")}</label>
        <select
          value={model}
          onChange={(e) => setModel(e.target.value)}
          data-testid="new-agent-model"
        >
          {availableModels.length === 0 && <option value="">{t("agentCreate.modelEmpty")}</option>}
          {availableModels.map((m) => (
            <option key={m.id} value={m.id}>
              {m.label || m.id}
            </option>
          ))}
        </select>
      </div>
      <div className="modal-field">
        <label>{t("agentCreate.appearance")}</label>
        <AvatarBuilder value={avatar} onChange={setAvatar} />
      </div>
      {create.isError && (
        <div style={{ color: "var(--danger)", fontSize: 12, marginTop: 8 }}>
          {t("agentCreate.error")}：{(create.error as Error).message}
        </div>
      )}
    </Modal>
  );
}
