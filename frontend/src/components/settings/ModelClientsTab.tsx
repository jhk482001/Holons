import { useTranslation } from "react-i18next";
import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AdminUsersAPI,
  AdminUserRow,
  CreateModelClientInput,
  ModelClientKind,
  ModelClientKindSchema,
  ModelClientRow,
  ModelClientsAPI,
} from "../../api/client";
import Modal from "../Modal";

/**
 * Admin-only tab for managing model client connections (Bedrock / Claude
 * native / OpenAI / Azure / Gemini / Minimax / local). Lists all clients
 * with grant/agent counts; supports create, edit, delete, grant
 * management, and the "default for new users" toggle.
 */
export default function ModelClientsTab() {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const { data: rows = [], isLoading } = useQuery({
    queryKey: ["model-clients"],
    queryFn: ModelClientsAPI.list,
  });
  const { data: kinds = [] } = useQuery({
    queryKey: ["model-client-kinds"],
    queryFn: ModelClientsAPI.kinds,
  });

  const [createOpen, setCreateOpen] = useState(false);
  const [editing, setEditing] = useState<ModelClientRow | null>(null);
  const [granting, setGranting] = useState<ModelClientRow | null>(null);
  const [err, setErr] = useState("");

  const deleteClient = useMutation({
    mutationFn: (id: number) => ModelClientsAPI.remove(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["model-clients"] });
      setErr("");
    },
    onError: (e: Error) => setErr(e.message),
  });

  const toggleEnabled = useMutation({
    mutationFn: ({ id, enabled }: { id: number; enabled: boolean }) =>
      ModelClientsAPI.update(id, { enabled }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["model-clients"] }),
  });

  const toggleDefault = useMutation({
    mutationFn: ({ id, value }: { id: number; value: boolean }) =>
      ModelClientsAPI.update(id, { default_for_new_users: value }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["model-clients"] }),
  });

  return (
    <div data-testid="settings-models-tab">
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          marginBottom: 10,
        }}
      >
        <h3 style={{ fontSize: 15, fontWeight: 800 }}>{t("models.title")}</h3>
        <button
          className="mbtn primary"
          data-testid="create-model-client-btn"
          onClick={() => setCreateOpen(true)}
        >
          {t("models.create")}
        </button>
      </div>
      <div style={{ fontSize: 11, color: "var(--ink-3)", marginBottom: 16 }}>
        {t("models.help")}
      </div>

      {err && (
        <div
          style={{
            background: "var(--danger-soft)",
            color: "var(--danger)",
            padding: "10px 14px",
            borderRadius: 10,
            fontSize: 12,
            fontWeight: 700,
            marginBottom: 14,
          }}
        >
          {err}
        </div>
      )}

      {isLoading ? (
        <div style={{ padding: 20, color: "var(--ink-4)" }}>{t("models.loading")}</div>
      ) : rows.length === 0 ? (
        <div style={{ padding: 30, color: "var(--ink-4)", textAlign: "center" }}>
          {t("models.empty")}
        </div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          {rows.map((row) => (
            <ClientCard
              key={row.id}
              row={row}
              kindLabel={kinds.find((k) => k.kind === row.kind)?.label || row.kind}
              onEdit={() => setEditing(row)}
              onGrants={() => setGranting(row)}
              onDelete={() => {
                if (confirm(t("models.deleteConfirm", { name: row.name }))) {
                  deleteClient.mutate(row.id);
                }
              }}
              onToggleEnabled={(v) => toggleEnabled.mutate({ id: row.id, enabled: v })}
              onToggleDefault={(v) => toggleDefault.mutate({ id: row.id, value: v })}
            />
          ))}
        </div>
      )}

      {createOpen && (
        <ClientFormModal
          kinds={kinds}
          onClose={() => setCreateOpen(false)}
          onError={setErr}
        />
      )}
      {editing && (
        <ClientFormModal
          kinds={kinds}
          initial={editing}
          onClose={() => setEditing(null)}
          onError={setErr}
        />
      )}
      {granting && (
        <GrantModal client={granting} onClose={() => setGranting(null)} />
      )}
    </div>
  );
}


function ClientCard({
  row,
  kindLabel,
  onEdit,
  onGrants,
  onDelete,
  onToggleEnabled,
  onToggleDefault,
}: {
  row: ModelClientRow;
  kindLabel: string;
  onEdit: () => void;
  onGrants: () => void;
  onDelete: () => void;
  onToggleEnabled: (v: boolean) => void;
  onToggleDefault: (v: boolean) => void;
}) {
  const { t } = useTranslation();
  return (
    <div
      data-testid={`model-client-card-${row.id}`}
      style={{
        background: "var(--surface)",
        border: "1px solid var(--border)",
        borderRadius: 12,
        padding: 14,
        opacity: row.enabled ? 1 : 0.55,
      }}
    >
      <div style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
        <div style={{ fontSize: 14, fontWeight: 800 }}>{row.name}</div>
        <span
          style={{
            fontSize: 10,
            background: "var(--surface-2)",
            padding: "2px 8px",
            borderRadius: 999,
            color: "var(--ink-3)",
          }}
        >
          {kindLabel}
        </span>
        {row.has_credential ? (
          <span style={{ fontSize: 10, color: "var(--ok)" }}>{t("models.credentialSet")}</span>
        ) : (
          <span style={{ fontSize: 10, color: "var(--ink-4)" }}>{t("models.credentialUnset")}</span>
        )}
      </div>
      {row.description && (
        <div style={{ fontSize: 11, color: "var(--ink-3)", marginTop: 4 }}>{row.description}</div>
      )}
      <div style={{ display: "flex", gap: 16, marginTop: 8, fontSize: 11, color: "var(--ink-3)" }}>
        <div>{t("models.assignedAgents")}<strong>{row.agent_count ?? 0}</strong></div>
        <div>{t("models.grantedUsers")}<strong>{row.grant_count ?? 0}</strong></div>
        {row.config.region && <div>region：{row.config.region}</div>}
        {row.config.base_url && <div>base_url：{row.config.base_url}</div>}
        {row.config.endpoint && <div>endpoint：{row.config.endpoint}</div>}
      </div>
      <div
        style={{
          display: "flex",
          gap: 12,
          marginTop: 10,
          alignItems: "center",
          flexWrap: "wrap",
        }}
      >
        <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 11 }}>
          <input
            type="checkbox"
            data-testid={`model-client-enabled-${row.id}`}
            checked={row.enabled}
            onChange={(e) => onToggleEnabled(e.target.checked)}
          />
          {t("models.enabled")}
        </label>
        <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 11 }}>
          <input
            type="checkbox"
            data-testid={`model-client-default-${row.id}`}
            checked={row.default_for_new_users}
            onChange={(e) => onToggleDefault(e.target.checked)}
          />
          {t("models.defaultForNew")}
        </label>
        <div style={{ flex: 1 }} />
        <button
          className="mbtn"
          data-testid={`model-client-grants-${row.id}`}
          onClick={onGrants}
          style={{ fontSize: 11 }}
        >
          {t("models.grants")}
        </button>
        <button
          className="mbtn"
          data-testid={`model-client-edit-${row.id}`}
          onClick={onEdit}
          style={{ fontSize: 11 }}
        >
          {t("models.edit")}
        </button>
        <button
          className="mbtn danger"
          data-testid={`model-client-delete-${row.id}`}
          onClick={onDelete}
          style={{ fontSize: 11 }}
        >
          {t("models.delete")}
        </button>
      </div>
    </div>
  );
}


// ============================================================================
// Create / Edit Modal
// ============================================================================

const DEFAULT_CONFIG_BY_KIND: Record<ModelClientKind, string> = {
  bedrock: JSON.stringify(
    {
      region: "ap-northeast-1",
      models: [
        {
          id: "jp.anthropic.claude-sonnet-4-6",
          label: "Claude Sonnet 4.6",
          price_in: 0.003,
          price_out: 0.015,
        },
      ],
    },
    null,
    2,
  ),
  claude_native: JSON.stringify(
    {
      base_url: "https://api.anthropic.com",
      models: [{ id: "claude-sonnet-4-6", label: "Claude Sonnet 4.6" }],
    },
    null,
    2,
  ),
  openai: JSON.stringify(
    {
      base_url: "https://api.openai.com/v1",
      models: [{ id: "gpt-4o", label: "GPT-4o" }],
    },
    null,
    2,
  ),
  azure_openai: JSON.stringify(
    {
      endpoint: "https://<resource>.openai.azure.com",
      api_version: "2024-10-01-preview",
      deployments: [{ id: "my-gpt4o-deployment", label: "GPT-4o deployment" }],
    },
    null,
    2,
  ),
  gemini: JSON.stringify(
    {
      models: [{ id: "gemini-1.5-pro-latest", label: "Gemini 1.5 Pro" }],
    },
    null,
    2,
  ),
  minimax: JSON.stringify(
    {
      group_id: "",
      models: [{ id: "MiniMax-M1", label: "MiniMax M1" }],
    },
    null,
    2,
  ),
  local: JSON.stringify(
    {
      base_url: "http://localhost:11434/v1",
      models: [{ id: "llama3.1:latest", label: "Llama 3.1 (local)" }],
    },
    null,
    2,
  ),
};


function ClientFormModal({
  kinds,
  initial,
  onClose,
  onError,
}: {
  kinds: ModelClientKindSchema[];
  initial?: ModelClientRow;
  onClose: () => void;
  onError: (msg: string) => void;
}) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const isEdit = !!initial;

  const [kind, setKind] = useState<ModelClientKind>(
    (initial?.kind as ModelClientKind) || "bedrock",
  );
  const [name, setName] = useState(initial?.name || "");
  const [description, setDescription] = useState(initial?.description || "");
  const [enabled, setEnabled] = useState(initial?.enabled ?? true);
  const [defaultForNew, setDefaultForNew] = useState(initial?.default_for_new_users ?? false);
  const [configJson, setConfigJson] = useState(
    initial
      ? JSON.stringify(initial.config ?? {}, null, 2)
      : DEFAULT_CONFIG_BY_KIND[kind],
  );
  const [credentialJson, setCredentialJson] = useState("");
  const [clearCredential, setClearCredential] = useState(false);

  // When creating and switching kind, reset config textarea to the template
  function handleKindChange(next: ModelClientKind) {
    if (!isEdit) {
      setConfigJson(DEFAULT_CONFIG_BY_KIND[next]);
    }
    setKind(next);
  }

  const kindMeta = kinds.find((k) => k.kind === kind);

  const save = useMutation({
    mutationFn: () => {
      let config: Record<string, unknown> = {};
      try {
        config = configJson.trim() ? JSON.parse(configJson) : {};
      } catch {
        throw new Error("Config JSON parse error");
      }
      let credential: Record<string, string> | undefined;
      if (credentialJson.trim()) {
        try {
          credential = JSON.parse(credentialJson);
        } catch {
          throw new Error("Credential JSON parse error");
        }
      }
      const payload: CreateModelClientInput = {
        name: name.trim(),
        kind,
        description: description.trim() || undefined,
        config,
        credential,
        enabled,
        default_for_new_users: defaultForNew,
      };
      if (isEdit && initial) {
        return ModelClientsAPI.update(initial.id, {
          ...payload,
          clear_credential: clearCredential && !credential,
        });
      }
      return ModelClientsAPI.create(payload);
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["model-clients"] });
      onClose();
    },
    onError: (e: Error) => onError(e.message),
  });

  return (
    <Modal
      open={true}
      title={isEdit ? t("models.editTitle", { name: initial?.name }) : t("models.createNewTitle")}
      onClose={onClose}
      size="lg"
    >
      <div className="modal-field">
        <label>{t("models.typeLabel")}</label>
        <select
          value={kind}
          onChange={(e) => handleKindChange(e.target.value as ModelClientKind)}
          disabled={isEdit}
          data-testid="model-client-kind-select"
        >
          {kinds.map((k) => (
            <option key={k.kind} value={k.kind}>
              {k.label}
            </option>
          ))}
        </select>
        {kindMeta && (
          <div className="hint">{kindMeta.hint}</div>
        )}
      </div>

      <div className="modal-field">
        <label>{t("agentCreate.name")}</label>
        <input
          data-testid="model-client-name-input"
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="e.g., Company AWS (ap-northeast-1)"
        />
      </div>

      <div className="modal-field">
        <label>{t("library.descLabel")}</label>
        <input
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          placeholder="Purpose / Owner / Notes"
        />
      </div>

      <div className="modal-field">
        <label>Config (JSON)</label>
        <textarea
          data-testid="model-client-config-input"
          rows={8}
          value={configJson}
          onChange={(e) => setConfigJson(e.target.value)}
          style={{
            fontFamily: "ui-monospace,SFMono-Regular,Menlo,monospace",
            fontSize: 12,
          }}
        />
        {kindMeta && (
          <div className="hint">
            {t("models.configFields", { fields: kindMeta.config_fields.join(", ") })}
          </div>
        )}
      </div>

      <div className="modal-field">
        <label>
          Credential (JSON){" "}
          {initial?.has_credential && (
            <span style={{ color: "var(--ok)", fontSize: 10 }}>🔒 {t("models.credentialSet")}</span>
          )}
        </label>
        <textarea
          data-testid="model-client-credential-input"
          rows={4}
          value={credentialJson}
          onChange={(e) => {
            setCredentialJson(e.target.value);
            if (e.target.value) setClearCredential(false);
          }}
          placeholder={
            kindMeta
              ? `e.g., {"${kindMeta.credential_fields[0]}": "..."}`
              : ""
          }
          style={{
            fontFamily: "ui-monospace,SFMono-Regular,Menlo,monospace",
            fontSize: 12,
          }}
        />
        {kindMeta && (
          <div className="hint">
            {t("models.credentialFields", { fields: kindMeta.credential_fields.join(", ") })}
            {initial?.has_credential && t("models.leaveBlankKeep")}
          </div>
        )}
        {isEdit && initial?.has_credential && !credentialJson && (
          <label
            style={{
              display: "flex",
              alignItems: "center",
              gap: 6,
              fontSize: 11,
              marginTop: 6,
              color: "var(--ink-3)",
            }}
          >
            <input
              type="checkbox"
              checked={clearCredential}
              onChange={(e) => setClearCredential(e.target.checked)}
            />
            {t("models.clearCredential")}
          </label>
        )}
      </div>

      <div style={{ display: "flex", gap: 16, marginBottom: 12, flexWrap: "wrap" }}>
        <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12 }}>
          <input
            type="checkbox"
            checked={enabled}
            onChange={(e) => setEnabled(e.target.checked)}
          />
          {t("models.enabled")}
        </label>
        <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12 }}>
          <input
            type="checkbox"
            checked={defaultForNew}
            onChange={(e) => setDefaultForNew(e.target.checked)}
          />
          {t("models.defaultForNewAuto")}
        </label>
      </div>

      <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
        <button className="mbtn" onClick={onClose}>{t("btn.cancel")}</button>
        <button
          className="mbtn primary"
          data-testid="model-client-save-btn"
          disabled={!name.trim() || save.isPending}
          onClick={() => save.mutate()}
        >
          {save.isPending ? t("btn.saving") : isEdit ? t("models.saveChanges") : t("models.create_submit")}
        </button>
      </div>
    </Modal>
  );
}


// ============================================================================
// Grant Management Modal
// ============================================================================

function GrantModal({
  client,
  onClose,
}: {
  client: ModelClientRow;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const { data: users = [] } = useQuery<AdminUserRow[]>({
    queryKey: ["admin-users"],
    queryFn: AdminUsersAPI.list,
  });
  const { data: grants = [] } = useQuery({
    queryKey: ["model-client-grants", client.id],
    queryFn: () => ModelClientsAPI.listGrants(client.id),
  });

  const grantedIds = useMemo(() => new Set(grants.map((g) => g.user_id)), [grants]);

  const grant = useMutation({
    mutationFn: (uid: number) => ModelClientsAPI.grant(client.id, uid),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["model-client-grants", client.id] });
      qc.invalidateQueries({ queryKey: ["model-clients"] });
    },
  });
  const revoke = useMutation({
    mutationFn: (uid: number) => ModelClientsAPI.revoke(client.id, uid),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["model-client-grants", client.id] });
      qc.invalidateQueries({ queryKey: ["model-clients"] });
    },
  });

  return (
    <Modal open={true} title={t("models.grantTitle", { name: client.name })} onClose={onClose} size="md">
      {client.default_for_new_users && (
        <div
          style={{
            background: "var(--surface-2)",
            padding: "8px 12px",
            borderRadius: 8,
            fontSize: 11,
            color: "var(--ink-3)",
            marginBottom: 12,
          }}
        >
          {t("models.grantDefaultHint")}
        </div>
      )}

      <div style={{ fontSize: 12, fontWeight: 700, marginBottom: 8 }}>
        {t("models.grantedCount", { count: grants.length })}
      </div>
      {grants.length === 0 ? (
        <div style={{ fontSize: 11, color: "var(--ink-4)", marginBottom: 12 }}>
          {t("models.noGrants")}
        </div>
      ) : (
        <ul style={{ listStyle: "none", padding: 0, marginBottom: 12 }}>
          {grants.map((g) => (
            <li
              key={g.id}
              style={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                padding: "6px 10px",
                border: "1px solid var(--border)",
                borderRadius: 8,
                marginBottom: 6,
              }}
            >
              <span style={{ fontSize: 12 }}>
                {g.display_name || g.username}
              </span>
              <button
                className="mbtn"
                onClick={() => revoke.mutate(g.user_id)}
                style={{ fontSize: 10 }}
              >
                {t("models.revokeGrant")}
              </button>
            </li>
          ))}
        </ul>
      )}

      <div style={{ fontSize: 12, fontWeight: 700, marginBottom: 8 }}>
        {t("models.grantToOthers")}
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
        {users
          .filter((u) => !grantedIds.has(u.id))
          .map((u) => (
            <div
              key={u.id}
              style={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                padding: "6px 10px",
              }}
            >
              <span style={{ fontSize: 12 }}>{u.display_name || u.username}</span>
              <button
                className="mbtn primary"
                onClick={() => grant.mutate(u.id)}
                style={{ fontSize: 10 }}
              >
                {t("models.grant")}
              </button>
            </div>
          ))}
      </div>

      <div style={{ display: "flex", justifyContent: "flex-end", marginTop: 12 }}>
        <button className="mbtn" onClick={onClose}>{t("btn.close")}</button>
      </div>
    </Modal>
  );
}
