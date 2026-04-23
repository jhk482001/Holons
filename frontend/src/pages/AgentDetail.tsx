import { useTranslation } from "react-i18next";
import { useEffect, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { AgentsAPI, Agent, api } from "../api/client";
import Avatar from "../components/Avatar";
import QuotaEditor from "../components/QuotaEditor";
import WorkingHoursEditor from "../components/WorkingHoursEditor";
import SharingEditor from "../components/SharingEditor";
import { AgentOverviewEditor, AgentSkillsEditor } from "../components/AgentEditors";
import AgentAssetsEditor from "../components/AgentAssetsEditor";
import AgentBudgetEditor from "../components/AgentBudgetEditor";
import Modal from "../components/Modal";
import UsageStackChart from "../components/UsageStackChart";
import "./AgentDetail.css";

type Tab = "overview" | "quotas" | "hours" | "sharing" | "skills" | "assets";

export default function AgentDetail() {
  const { t } = useTranslation();
  const { id } = useParams<{ id: string }>();
  const agentId = Number(id);
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [tab, setTab] = useState<Tab>("overview");
  const [confirmDelete, setConfirmDelete] = useState(false);

  const { data: agent, isLoading } = useQuery({
    queryKey: ["agent", agentId],
    queryFn: () => AgentsAPI.get(agentId),
    enabled: !isNaN(agentId),
  });

  const del = useMutation({
    mutationFn: () => AgentsAPI.delete(agentId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["agents"] });
      navigate("/agents");
    },
  });

  if (isLoading) return <div className="page">{t("btn.loading")}</div>;
  if (!agent) return <div className="page">{t("agents.notFound")}</div>;

  return (
    <div className="page agent-detail">
      <button className="back-btn" onClick={() => navigate("/agents")}>
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M19 12H5M12 19l-7-7 7-7" />
        </svg>
        {t("agentDetail.back")}
      </button>

      <div className="agent-head">
        <Avatar cfg={agent.avatar_config} size={88} title={agent.name} className="avatar-large" />
        <div>
          <EditableHeader agent={agent} />
          <div className="subtitle">
            <EditableRoleTitle agent={agent} />
            {agent.is_lead && <span className="lead-badge">{t("common.lead")}</span>}
          </div>
          <div className="meta-row">
            <span>{t("agentDetail.status")}<strong>{agent.status}</strong></span>
            <span>{t("agentDetail.queue")}<strong>{(agent as any).queue_depth ?? 0} / {agent.max_queue_depth}</strong></span>
            <span>{t("agentDetail.model")}<strong>{agent.primary_model_id || "—"}</strong></span>
          </div>
          {!agent.is_lead && <DialogVisibilityToggle agentId={agent.id} />}
        </div>
      </div>

      <div className="tabs">
        <button className={tab === "overview" ? "active" : ""} onClick={() => setTab("overview")}>{t("agentDetail.tab.overview")}</button>
        <button className={tab === "quotas" ? "active" : ""} onClick={() => setTab("quotas")}>{t("agentDetail.tab.quotas")}</button>
        <button className={tab === "hours" ? "active" : ""} onClick={() => setTab("hours")}>{t("agentDetail.tab.hours")}</button>
        <button className={tab === "sharing" ? "active" : ""} onClick={() => setTab("sharing")}>{t("agentDetail.tab.sharing")}</button>
        <button className={tab === "skills" ? "active" : ""} onClick={() => setTab("skills")}>{t("agentDetail.tab.skills")}</button>
        <button
          className={tab === "assets" ? "active" : ""}
          onClick={() => setTab("assets")}
          data-testid="agent-tab-assets"
        >{t("agentDetail.tab.assets")}</button>
      </div>

      <div className="tab-content">
        {tab === "overview" && <AgentOverviewEditor agent={agent} />}
        {tab === "quotas" && (
          <>
            <AgentBudgetEditor agent={agent} />
            <QuotaEditor agentId={agentId} />
          </>
        )}
        {tab === "hours" && <WorkingHoursEditor agent={agent} />}
        {tab === "sharing" && <SharingEditor agent={agent} />}
        {tab === "skills" && <AgentSkillsEditor agentId={agentId} />}
        {tab === "assets" && <AgentAssetsEditor agentId={agentId} />}
      </div>

      <div style={{ marginTop: 24 }}>
        <h3 style={{ fontSize: 11, textTransform: "uppercase",
                     color: "var(--ink-3)", letterSpacing: 1,
                     fontWeight: 800, marginBottom: 12 }}>
          {t("agentDetail.usageByProject")}
        </h3>
        <UsageStackChart group_by="project" agent_id={agentId} days={14} />
      </div>

      {!agent.is_lead && (
        <div
          style={{
            marginTop: 28,
            padding: "16px 20px",
            border: "1px solid rgba(232, 100, 80, 0.3)",
            background: "rgba(232, 100, 80, 0.05)",
            borderRadius: 14,
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
          }}
        >
          <div>
            <div style={{ fontSize: 13, fontWeight: 800, color: "var(--danger)" }}>{t("agentDetail.dangerZone")}</div>
            <div style={{ fontSize: 11, color: "var(--ink-3)", marginTop: 2 }}>
              {t("agentDetail.dangerZoneDesc")}
            </div>
          </div>
          <button
            className="mbtn danger"
            data-testid="delete-agent-btn"
            onClick={() => setConfirmDelete(true)}
          >
            {t("agentDetail.deleteBtn")}
          </button>
        </div>
      )}

      <Modal
        open={confirmDelete}
        onClose={() => setConfirmDelete(false)}
        title={t("agentDetail.deleteConfirm")}
        subtitle={t("agentDetail.deleteConfirmSub", { name: agent.name })}
        size="sm"
        footer={
          <>
            <button className="mbtn" onClick={() => setConfirmDelete(false)} disabled={del.isPending}>{t("btn.cancel")}</button>
            <button
              className="mbtn danger"
              data-testid="confirm-delete-agent"
              onClick={() => del.mutate()}
              disabled={del.isPending}
            >
              {del.isPending ? t("agentDetail.deleting") : t("agentDetail.deleteSubmit")}
            </button>
          </>
        }
      >
        <div style={{ fontSize: 13, color: "var(--ink-2)", lineHeight: 1.6 }}>
          {t("agentDetail.deleteConfirmDesc", { name: agent.name })}
          <br />
          
        </div>
      </Modal>
    </div>
  );
}


function DialogVisibilityToggle({ agentId }: { agentId: number }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const { data: castLayout } = useQuery<{
    hidden_agents?: number[];
    [k: string]: unknown;
  }>({
    queryKey: ["cast-layout"],
    queryFn: async () => {
      const r = await fetch("/api/me/cast_layout", { credentials: "include" });
      return r.ok ? r.json() : {};
    },
  });

  const hidden = (castLayout?.hidden_agents || []).includes(agentId);

  function toggle() {
    const current = castLayout?.hidden_agents || [];
    const next = hidden
      ? current.filter((id: number) => id !== agentId)
      : [...current, agentId];
    const newLayout = { ...castLayout, hidden_agents: next };
    fetch("/api/me/cast_layout", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify(newLayout),
    }).then(() => qc.invalidateQueries({ queryKey: ["cast-layout"] }));
  }

  return (
    <label
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 6,
        fontSize: 12,
        color: "var(--ink-3)",
        cursor: "pointer",
        marginTop: 6,
      }}
    >
      <input
        type="checkbox"
        checked={!hidden}
        onChange={toggle}
        data-testid="agent-dialog-visible-toggle"
      />
      {t("agentDetail.dialogVisible")}
    </label>
  );
}


// Inline-editable <h1> for the agent's name. Click to edit, Enter or
// blur to save, Escape to cancel. The backend `update_agent` endpoint
// accepts `name` and also propagates the rename into the user's Lead
// system_prompt if the Lead mentions the old name.
function EditableHeader({ agent }: { agent: Agent }) {
  const qc = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(agent.name);
  useEffect(() => { setDraft(agent.name); }, [agent.name]);

  const save = useMutation({
    mutationFn: (next: string) => api.put(`/agents/${agent.id}`, { name: next }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["agent", agent.id] });
      qc.invalidateQueries({ queryKey: ["agents"] });
      setEditing(false);
    },
    onError: () => { setDraft(agent.name); setEditing(false); },
  });

  function commit() {
    const next = draft.trim();
    if (!next || next === agent.name) {
      setDraft(agent.name);
      setEditing(false);
      return;
    }
    save.mutate(next);
  }

  if (editing) {
    return (
      <input
        data-testid="agent-name-edit"
        autoFocus
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === "Enter") { e.preventDefault(); commit(); }
          if (e.key === "Escape") { setDraft(agent.name); setEditing(false); }
        }}
        disabled={save.isPending}
        style={{
          fontSize: "1.8rem", fontWeight: 800, lineHeight: 1.1,
          border: "1px solid var(--accent)", borderRadius: 8,
          padding: "2px 8px", background: "var(--surface)",
          font: "inherit", outline: "none", minWidth: 160,
        }}
      />
    );
  }
  return (
    <h1
      data-testid="agent-name-display"
      onClick={() => setEditing(true)}
      title="Click to rename"
      style={{ cursor: "text" }}
    >
      {agent.name}
    </h1>
  );
}


function EditableRoleTitle({ agent }: { agent: Agent }) {
  const qc = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(agent.role_title || "");
  useEffect(() => { setDraft(agent.role_title || ""); }, [agent.role_title]);

  const save = useMutation({
    mutationFn: (next: string) => api.put(`/agents/${agent.id}`, { role_title: next }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["agent", agent.id] });
      qc.invalidateQueries({ queryKey: ["agents"] });
      setEditing(false);
    },
    onError: () => { setDraft(agent.role_title || ""); setEditing(false); },
  });

  function commit() {
    const next = draft.trim();
    if (next === (agent.role_title || "")) {
      setEditing(false);
      return;
    }
    save.mutate(next);
  }

  if (editing) {
    return (
      <input
        data-testid="agent-role-edit"
        autoFocus
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === "Enter") { e.preventDefault(); commit(); }
          if (e.key === "Escape") { setDraft(agent.role_title || ""); setEditing(false); }
        }}
        disabled={save.isPending}
        style={{
          fontSize: "inherit", fontWeight: "inherit",
          border: "1px solid var(--border)", borderRadius: 6,
          padding: "1px 6px", background: "var(--surface)",
          font: "inherit", outline: "none", minWidth: 140,
        }}
      />
    );
  }
  return (
    <span
      data-testid="agent-role-display"
      onClick={() => setEditing(true)}
      title="Click to edit"
      style={{ cursor: "text" }}
    >
      {agent.role_title || "—"}
    </span>
  );
}

