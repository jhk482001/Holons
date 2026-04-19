import { useTranslation } from "react-i18next";
import { useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { api, Workflow, WorkflowsAPI } from "../api/client";
import Modal from "../components/Modal";

export default function Workflows() {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [scope, setScope] = useState<"mine" | "templates">("mine");
  const { data: workflows = [] } = useQuery({
    queryKey: ["workflows", scope],
    queryFn: () => WorkflowsAPI.list(scope),
  });
  const [createOpen, setCreateOpen] = useState(false);
  const [toDelete, setToDelete] = useState<Workflow | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const cloneWf = useMutation({
    mutationFn: (id: number) => WorkflowsAPI.clone(id),
    onSuccess: ({ id }) => {
      qc.invalidateQueries({ queryKey: ["workflows"] });
      navigate(`/workflows/${id}`);
    },
  });

  const del = useMutation({
    mutationFn: (id: number) => api.del(`/workflows/${id}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["workflows"] });
      setToDelete(null);
    },
  });

  const importWf = useMutation({
    mutationFn: (bundle: unknown) =>
      api.post<{ id: number }>("/workflows/import", bundle),
    onSuccess: ({ id }) => {
      qc.invalidateQueries({ queryKey: ["workflows"] });
      navigate(`/workflows/${id}`);
    },
  });

  async function exportWorkflow(wf: Workflow) {
    const bundle = await api.get<unknown>(`/workflows/${wf.id}/export`);
    const blob = new Blob([JSON.stringify(bundle, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${(wf.name || `workflow-${wf.id}`).replace(/[^\w-]+/g, "_")}.json`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  }

  async function onImportFileChange(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    e.target.value = ""; // allow re-picking the same file
    if (!file) return;
    try {
      const text = await file.text();
      const bundle = JSON.parse(text);
      importWf.mutate(bundle);
    } catch {
      alert(t("workflows.importError"));
    }
  }

  return (
    <div className="page">
      <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", marginBottom: 4 }}>
        <div>
          <h1>{t("workflows.title")}</h1>
          <div className="subtitle">{t("workflows.subtitle")}</div>
        </div>
        <div style={{ display: "flex", gap: 8 }}>
          <input
            ref={fileInputRef}
            type="file"
            accept="application/json"
            data-testid="import-workflow-input"
            onChange={onImportFileChange}
            style={{ display: "none" }}
          />
          <button
            data-testid="import-workflow-btn"
            onClick={() => fileInputRef.current?.click()}
            className="mbtn"
            style={{ padding: "10px 16px", fontSize: 13, fontWeight: 800 }}
          >
            {t("workflows.import")}
          </button>
          <button
            data-testid="new-workflow-btn"
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
            {t("workflows.createNew")}
          </button>
        </div>
      </div>

      {/* Scope tabs */}
      <div style={{ display: "flex", gap: 4, marginTop: 18, padding: 4, background: "var(--surface-2)", borderRadius: 12, width: "fit-content" }}>
        <button
          data-testid="wf-scope-mine"
          onClick={() => setScope("mine")}
          style={{
            padding: "8px 18px",
            border: "none",
            background: scope === "mine" ? "var(--accent)" : "transparent",
            color: scope === "mine" ? "white" : "var(--ink-3)",
            borderRadius: 8,
            fontSize: 12,
            fontWeight: 700,
            cursor: "pointer",
          }}
        >
          {t("workflows.mine")}
        </button>
        <button
          data-testid="wf-scope-templates"
          onClick={() => setScope("templates")}
          style={{
            padding: "8px 18px",
            border: "none",
            background: scope === "templates" ? "var(--accent)" : "transparent",
            color: scope === "templates" ? "white" : "var(--ink-3)",
            borderRadius: 8,
            fontSize: 12,
            fontWeight: 700,
            cursor: "pointer",
          }}
        >
          {t("workflows.templates")}
        </button>
      </div>

      <div style={{
        display: "grid",
        gridTemplateColumns: "repeat(auto-fill, minmax(320px, 1fr))",
        gap: 16,
        marginTop: 20,
      }}>
        {workflows.length === 0 && (
          <div style={{ gridColumn: "1 / -1", padding: 60, textAlign: "center", color: "var(--ink-4)" }}>
            {scope === "templates"
              ? t("workflows.emptyTemplates")
              : t("workflows.emptyMine")}
          </div>
        )}
        {workflows.map((wf) => (
          <div
            key={wf.id}
            data-testid={`workflow-card-${wf.id}`}
            style={{
              background: "var(--surface)",
              border: "1px solid var(--border)",
              borderRadius: 16,
              padding: 18,
              boxShadow: "var(--shadow-sm)",
              position: "relative",
            }}
          >
            <div
              onClick={() => navigate(`/workflows/${wf.id}`)}
              style={{ cursor: "pointer" }}
            >
              <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6, flexWrap: "wrap" }}>
                <div style={{ fontSize: 15, fontWeight: 800 }}>{wf.name || t("workflows.unnamed")}</div>
                {wf.is_template && (
                  <span style={{
                    fontSize: 9,
                    fontWeight: 800,
                    letterSpacing: 1.2,
                    background: "var(--good-soft)",
                    color: "var(--good)",
                    padding: "2px 8px",
                    borderRadius: 999,
                  }}>{t("workflows.badgeTemplate")}</span>
                )}
                {wf.is_draft && (
                  <span style={{
                    fontSize: 9,
                    fontWeight: 800,
                    letterSpacing: 1.2,
                    background: "var(--surface-2)",
                    color: "var(--ink-3)",
                    padding: "2px 8px",
                    borderRadius: 999,
                  }}>{t("workflows.badgeDraft")}</span>
                )}
                {wf.loop_enabled && (
                  <span style={{
                    fontSize: 9,
                    fontWeight: 800,
                    letterSpacing: 1.2,
                    background: "var(--accent-soft)",
                    color: "var(--accent)",
                    padding: "2px 8px",
                    borderRadius: 999,
                  }}>LOOP ×{wf.max_loops}</span>
                )}
              </div>
              <div style={{ fontSize: 12, color: "var(--ink-3)", minHeight: 32 }}>
                {wf.description || "—"}
              </div>
              <div style={{ fontSize: 11, color: "var(--ink-4)", marginTop: 10 }}>
                {scope === "templates" && wf.owner_username && (
                  <>
                    {t("workflows.author")}：<strong style={{ color: "var(--ink-2)" }}>
                      {wf.owner_display_name || wf.owner_username}
                    </strong>
                    <span style={{ marginLeft: 8 }}>·</span>
                    <span style={{ marginLeft: 8 }}>{t("workflows.source")} <strong style={{ color: "var(--ink-2)" }}>{wf.source}</strong></span>
                  </>
                )}
                {scope !== "templates" && (
                  <>{t("workflows.source")}<strong style={{ color: "var(--ink-2)" }}>{wf.source}</strong></>
                )}
              </div>
            </div>
            <div style={{ display: "flex", gap: 6, marginTop: 14, justifyContent: "flex-end" }}>
              {scope === "templates" ? (
                <button
                  className="mbtn primary"
                  data-testid={`clone-template-${wf.id}`}
                  disabled={cloneWf.isPending}
                  onClick={() => cloneWf.mutate(wf.id)}
                  style={{ padding: "6px 14px", fontSize: 11 }}
                >
                  {cloneWf.isPending ? t("workflows.cloning") : t("workflows.clone")}
                </button>
              ) : (
                <>
                  <button
                    className="mbtn"
                    data-testid={`export-workflow-${wf.id}`}
                    onClick={() => exportWorkflow(wf)}
                    style={{ padding: "6px 12px", fontSize: 11 }}
                  >
                    {t("workflows.export")}
                  </button>
                  <button
                    className="mbtn"
                    onClick={() => navigate(`/workflows/${wf.id}`)}
                    style={{ padding: "6px 12px", fontSize: 11 }}
                  >
                    {t("workflows.edit")}
                  </button>
                  <button
                    className="mbtn danger"
                    data-testid={`delete-workflow-${wf.id}`}
                    onClick={() => setToDelete(wf)}
                    style={{ padding: "6px 12px", fontSize: 11 }}
                  >
                    {t("workflows.delete")}
                  </button>
                </>
              )}
            </div>
          </div>
        ))}
      </div>

      <CreateWorkflowModal
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        onCreated={(id) => {
          setCreateOpen(false);
          navigate(`/workflows/${id}`);
        }}
      />

      <Modal
        open={!!toDelete}
        onClose={() => setToDelete(null)}
        title={t("agentDetail.deleteConfirm")}
        subtitle={toDelete ? t("workflows.deleteConfirm", { name: toDelete.name }) : ""}
        size="sm"
        footer={
          <>
            <button className="mbtn" onClick={() => setToDelete(null)} disabled={del.isPending}>
              {t("btn.cancel")}
            </button>
            <button
              className="mbtn danger"
              data-testid="confirm-delete-workflow"
              onClick={() => toDelete && del.mutate(toDelete.id)}
              disabled={del.isPending}
            >
              {del.isPending ? t("workflows.deleting") : t("agentDetail.deleteSubmit")}
            </button>
          </>
        }
      >
        <div style={{ fontSize: 13, color: "var(--ink-2)", lineHeight: 1.6 }}>
          {t("workflows.deleteConfirmDesc")}
        </div>
      </Modal>
    </div>
  );
}

function CreateWorkflowModal({
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
  const [description, setDescription] = useState("");
  const [loopEnabled, setLoopEnabled] = useState(false);
  const [maxLoops, setMaxLoops] = useState(1);

  const create = useMutation({
    mutationFn: () =>
      api.post<{ id: number }>("/workflows", {
        name,
        description,
        loop_enabled: loopEnabled,
        max_loops: maxLoops,
      }),
    onSuccess: ({ id }) => {
      qc.invalidateQueries({ queryKey: ["workflows"] });
      setName("");
      setDescription("");
      setLoopEnabled(false);
      setMaxLoops(1);
      onCreated(id);
    },
  });

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={t("workflowCreate.title")}
      subtitle={t("workflowCreate.subtitle")}
      size="md"
      footer={
        <>
          <button className="mbtn" onClick={onClose} disabled={create.isPending}>{t("btn.cancel")}</button>
          <button
            className="mbtn primary"
            data-testid="create-workflow-submit"
            onClick={() => create.mutate()}
            disabled={!name.trim() || create.isPending}
          >
            {create.isPending ? t("workflowCreate.submitting") : t("workflowCreate.submit")}
          </button>
        </>
      }
    >
      <div className="modal-field">
        <label>{t("workflowCreate.name")}</label>
        <input
          data-testid="new-workflow-name"
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder={t("workflowCreate.namePlaceholder")}
          autoFocus
        />
      </div>
      <div className="modal-field">
        <label>{t("workflowCreate.description")}</label>
        <textarea
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          placeholder={t("workflowCreate.descPlaceholder")}
        />
      </div>
      <div className="modal-field">
        <label>
          <input
            type="checkbox"
            checked={loopEnabled}
            onChange={(e) => setLoopEnabled(e.target.checked)}
            style={{ width: "auto", marginRight: 8 }}
          />
          {t("workflowCreate.loopEnabled")}
        </label>
      </div>
      {loopEnabled && (
        <div className="modal-field">
          <label>{t("workflowCreate.maxLoops")}</label>
          <input
            type="number"
            min={1}
            max={10}
            value={maxLoops}
            onChange={(e) => setMaxLoops(Number(e.target.value))}
          />
        </div>
      )}
    </Modal>
  );
}
