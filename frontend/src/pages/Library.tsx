import { useTranslation } from "react-i18next";
import { useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AssetsAPI,
  AssetRow,
  AssetKind,
  CreateAssetInput,
  AdminUsersAPI,
} from "../api/client";
import { useIsAdmin, useMe } from "../auth";
import Modal from "../components/Modal";
import "./Records.css"; // reuses .page-tabs
import "./Library.css";

type TabKey = "skill" | "tool" | "mcp" | "rag";

const TABS: { key: TabKey; labelKey: string; kind: AssetKind; hintKey: string }[] = [
  { key: "skill", labelKey: "library.tab.skill", kind: "skill", hintKey: "library.hintSkill" },
  { key: "tool", labelKey: "library.tab.tool", kind: "tool", hintKey: "library.hintTool" },
  { key: "mcp", labelKey: "library.tab.mcp", kind: "mcp", hintKey: "library.hintMcp" },
  { key: "rag", labelKey: "library.tab.rag", kind: "rag", hintKey: "library.hintRag" },
];

type SortKey = "created_desc" | "created_asc" | "name_asc" | "name_desc";
const SORT_OPTIONS: { key: SortKey; labelKey: string }[] = [
  { key: "created_desc", labelKey: "library.sort.createdDesc" },
  { key: "created_asc", labelKey: "library.sort.createdAsc" },
  { key: "name_asc", labelKey: "library.sort.nameAsc" },
  { key: "name_desc", labelKey: "library.sort.nameDesc" },
];

export default function Library() {
  const { t } = useTranslation();
  const [params, setParams] = useSearchParams();
  const raw = params.get("tab") as TabKey | null;
  const tab: TabKey = (raw && TABS.some((tb) => tb.key === raw)) ? raw : "skill";

  function setTab(k: TabKey) {
    const next = new URLSearchParams(params);
    next.set("tab", k);
    setParams(next, { replace: true });
  }

  const currentTab = TABS.find((t) => t.key === tab)!;
  const isAdmin = useIsAdmin();

  return (
    <div className="page library-page">
      <h1>{t("library.title")}</h1>
      <div className="subtitle">
        {t("library.subtitle")}
      </div>

      <nav className="page-tabs" data-testid="library-tabs">
        {TABS.map((tb) => (
          <button
            key={tb.key}
            className={`page-tab ${tab === tb.key ? "active" : ""}`}
            data-testid={`library-tab-${tb.key}`}
            onClick={() => setTab(tb.key)}
          >
            {t(tb.labelKey)}
          </button>
        ))}
      </nav>

      <div style={{ fontSize: 12, color: "var(--ink-3)", marginBottom: 14 }}>
        {t(currentTab.hintKey)}
      </div>

      <AssetKindPanel kind={currentTab.kind} isAdmin={isAdmin} />
    </div>
  );
}


function AssetKindPanel({ kind, isAdmin }: { kind: AssetKind; isAdmin: boolean }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const { data: rows = [], isLoading } = useQuery({
    queryKey: ["assets", kind],
    queryFn: () => AssetsAPI.list(kind),
  });
  const [createOpen, setCreateOpen] = useState(false);
  const [detail, setDetail] = useState<AssetRow | null>(null);
  const [err, setErr] = useState("");
  const [search, setSearch] = useState("");
  const [sortKey, setSortKey] = useState<SortKey>(
    () => (localStorage.getItem(`library-sort-${kind}`) as SortKey | null) || "created_desc",
  );

  const onSortChange = (next: SortKey) => {
    setSortKey(next);
    localStorage.setItem(`library-sort-${kind}`, next);
  };

  const visibleRows = useMemo(() => {
    const q = search.trim().toLowerCase();
    const filtered = q
      ? rows.filter(
          (r) =>
            r.name.toLowerCase().includes(q) ||
            (r.description || "").toLowerCase().includes(q),
        )
      : rows.slice();
    const cmpName = (a: AssetRow, b: AssetRow) =>
      a.name.localeCompare(b.name, "zh-Hant");
    const cmpCreated = (a: AssetRow, b: AssetRow) => {
      const ta = a.created_at ? Date.parse(a.created_at) : 0;
      const tb = b.created_at ? Date.parse(b.created_at) : 0;
      return ta - tb;
    };
    switch (sortKey) {
      case "created_desc":
        filtered.sort((a, b) => cmpCreated(b, a));
        break;
      case "created_asc":
        filtered.sort(cmpCreated);
        break;
      case "name_asc":
        filtered.sort(cmpName);
        break;
      case "name_desc":
        filtered.sort((a, b) => cmpName(b, a));
        break;
    }
    return filtered;
  }, [rows, search, sortKey]);

  const toggleEnabled = useMutation({
    mutationFn: ({ id, enabled }: { id: number; enabled: boolean }) =>
      AssetsAPI.update(id, { enabled }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["assets", kind] }),
    onError: (e: Error) => setErr(e.message),
  });

  const deleteAsset = useMutation({
    mutationFn: (id: number) => AssetsAPI.remove(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["assets", kind] });
      setErr("");
    },
    onError: (e: Error) => setErr(e.message),
  });

  return (
    <div>
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 10,
          marginBottom: 12,
          flexWrap: "wrap",
        }}
      >
        <div style={{ fontSize: 12, color: "var(--ink-3)", flexShrink: 0 }}>
          {search || sortKey !== "created_desc"
            ? `${visibleRows.length} / ${rows.length}`
            : rows.length}{" "}
          {t(`common.${kind}`)}
        </div>
        <input
          type="search"
          data-testid={`library-search-${kind}`}
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder={t("library.search")}
          style={{
            flex: "1 1 200px",
            minWidth: 160,
            maxWidth: 320,
            padding: "7px 11px",
            border: "1px solid var(--border)",
            borderRadius: 8,
            fontSize: 12,
            background: "var(--surface)",
          }}
        />
        <select
          data-testid={`library-sort-${kind}`}
          value={sortKey}
          onChange={(e) => onSortChange(e.target.value as SortKey)}
          style={{
            padding: "7px 10px",
            border: "1px solid var(--border)",
            borderRadius: 8,
            fontSize: 12,
            background: "var(--surface)",
          }}
        >
          {SORT_OPTIONS.map((o) => (
            <option key={o.key} value={o.key}>
              {t(o.labelKey)}
            </option>
          ))}
        </select>
        <div style={{ flex: 1 }} />
        <button
          className="mbtn primary"
          data-testid={`library-new-${kind}-btn`}
          onClick={() => setCreateOpen(true)}
        >
          {t("library.createNew", { kind: t(`common.${kind}`) })}
        </button>
      </div>

      {err && (
        <div
          data-testid="library-error"
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
        <div style={{ padding: 30, textAlign: "center", color: "var(--ink-4)" }}>{t("btn.loading")}</div>
      ) : rows.length === 0 ? (
        <div className="library-empty" data-testid={`library-empty-${kind}`}>
          {t("library.empty", { kind: t(`common.${kind}`) })}
        </div>
      ) : visibleRows.length === 0 ? (
        <div className="library-empty" data-testid={`library-no-match-${kind}`}>
          {t("library.noMatch", { kind: t(`common.${kind}`), search })}
        </div>
      ) : (
        <div className="library-grid" data-testid={`library-grid-${kind}`}>
          {visibleRows.map((row) => (
            <AssetCard
              key={row.id}
              row={row}
              onOpen={() => setDetail(row)}
              onToggle={(enabled) =>
                toggleEnabled.mutate({ id: row.id, enabled })
              }
              onDelete={() => {
                if (confirm(t("library.confirmDelete", { name: row.name }))) {
                  deleteAsset.mutate(row.id);
                }
              }}
            />
          ))}
        </div>
      )}

      {createOpen && (
        <CreateAssetModal
          kind={kind}
          onClose={() => setCreateOpen(false)}
          onError={setErr}
        />
      )}
      {detail && (
        <AssetDetailModal
          asset={detail}
          onClose={() => setDetail(null)}
          isAdmin={isAdmin}
        />
      )}
    </div>
  );
}

function AssetCard({
  row,
  onOpen,
  onToggle,
  onDelete,
}: {
  row: AssetRow;
  onOpen: () => void;
  onToggle: (enabled: boolean) => void;
  onDelete: () => void;
}) {
  const { t } = useTranslation();
  const stats = [
    { label: t("library.grantUsers"), value: row.grant_count ?? 0 },
    { label: t("library.assignedAgents"), value: row.assigned_agent_count ?? 0 },
    { label: t("library.totalCalls"), value: row.total_calls ?? 0 },
  ];
  return (
    <div
      className={`library-card ${row.enabled ? "" : "disabled"}`}
      data-testid={`asset-card-${row.id}`}
    >
      <div className="library-card-head">
        <div style={{ flex: 1, minWidth: 0 }}>
          <div className="library-card-name">{row.name}</div>
          {row.description && (
            <div className="library-card-desc">{row.description}</div>
          )}
        </div>
        <label
          className="library-toggle"
          title={row.enabled ? t("library.disabled") : t("library.enabled")}
          onClick={(e) => e.stopPropagation()}
        >
          <input
            type="checkbox"
            checked={row.enabled}
            onChange={(e) => onToggle(e.target.checked)}
            data-testid={`asset-toggle-${row.id}`}
          />
          <span>{row.enabled ? t("library.enabled") : t("library.disabled")}</span>
        </label>
      </div>

      <div className="library-card-stats">
        {stats.map((s) => (
          <div key={s.label} className="library-stat">
            <div className="library-stat-value">{s.value}</div>
            <div className="library-stat-label">{s.label}</div>
          </div>
        ))}
      </div>

      {row.last_used_at && (
        <div className="library-card-last">
          {t("library.lastUsed")}{new Date(row.last_used_at).toLocaleString("zh-TW")}
        </div>
      )}

      <div className="library-card-footer">
        <div style={{ fontSize: 10, color: "var(--ink-4)" }}>
          {t("library.owner")}{row.owner_display_name || row.owner_username || `#${row.owner_user_id}`}
          {row.has_credential && <span className="library-cred-badge">🔒 credential</span>}
        </div>
        <div style={{ display: "flex", gap: 6 }}>
          <button
            className="mbtn"
            onClick={onOpen}
            data-testid={`asset-detail-${row.id}`}
            style={{ fontSize: 11 }}
          >
            {t("library.detail")}
          </button>
          <button
            className="mbtn danger"
            onClick={onDelete}
            data-testid={`asset-delete-${row.id}`}
            style={{ fontSize: 11 }}
          >
            {t("library.delete")}
          </button>
        </div>
      </div>
    </div>
  );
}


function CreateAssetModal({
  kind,
  onClose,
  onError,
}: {
  kind: AssetKind;
  onClose: () => void;
  onError: (msg: string) => void;
}) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [configJson, setConfigJson] = useState(() => defaultConfigForKind(kind));
  const [credential, setCredential] = useState("");

  const create = useMutation({
    mutationFn: () => {
      let config: Record<string, unknown> = {};
      try {
        config = configJson.trim() ? JSON.parse(configJson) : {};
      } catch {
        throw new Error(t("library.configParseError"));
      }
      const payload: CreateAssetInput = {
        kind,
        name: name.trim(),
        description: description.trim() || undefined,
        config,
      };
      if (credential.trim()) payload.credential = credential;
      return AssetsAPI.create(payload);
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["assets", kind] });
      onClose();
    },
    onError: (e: Error) => onError(e.message),
  });

  const needsCredential = kind === "mcp" || kind === "rag";

  return (
    <Modal open={true} title={t("library.createNew", { kind: t(`common.${kind}`) })} onClose={onClose} size="lg">
      <div className="modal-field">
        <label>{t("agentCreate.name")}</label>
        <input
          data-testid="new-asset-name"
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder={namePlaceholder(kind)}
        />
      </div>
      <div className="modal-field">
        <label>{t("library.descLabel")}</label>
        <input
          data-testid="new-asset-description"
          value={description}
          onChange={(e) => setDescription(e.target.value)}
        />
      </div>
      <div className="modal-field">
        <label>Config (JSON)</label>
        <textarea
          data-testid="new-asset-config"
          rows={6}
          value={configJson}
          onChange={(e) => setConfigJson(e.target.value)}
          style={{
            fontFamily: "ui-monospace,SFMono-Regular,Menlo,monospace",
            fontSize: 12,
          }}
        />
        <div className="hint">{configHint(kind)}</div>
      </div>
      {needsCredential && (
        <div className="modal-field">
          <label>{t("library.credentialLabel")} ({t("library.credentialKeepBlank")})</label>
          <input
            type="password"
            data-testid="new-asset-credential"
            value={credential}
            onChange={(e) => setCredential(e.target.value)}
            placeholder={credentialPlaceholder(kind)}
          />
          <div className="hint">{t("library.credentialHintDb")}</div>
        </div>
      )}
      <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
        <button className="mbtn" onClick={onClose}>{t("btn.cancel")}</button>
        <button
          className="mbtn primary"
          data-testid="confirm-create-asset"
          onClick={() => create.mutate()}
          disabled={!name.trim() || create.isPending}
        >
          {create.isPending ? t("library.creating") : t("library.createSubmit")}
        </button>
      </div>
    </Modal>
  );
}

function defaultConfigForKind(kind: AssetKind): string {
  switch (kind) {
    case "skill":
      return JSON.stringify({ content_md: "" }, null, 2);
    case "tool":
      return JSON.stringify({ module: "", fn: "" }, null, 2);
    case "mcp":
      return JSON.stringify({ url: "https://" }, null, 2);
    case "rag":
      return JSON.stringify({ backend: "pgvector" }, null, 2);
  }
}

function namePlaceholder(k: AssetKind): string {
  return {
    skill: "e.g., Brand Voice Writing Style",
    tool: "e.g., http_get_wrapper",
    mcp: "e.g., GitHub MCP",
    rag: "e.g., Script Library",
  }[k];
}

function credentialPlaceholder(k: AssetKind): string {
  return {
    skill: "",
    tool: "",
    mcp: "Bearer <token> or X-API-Key <key>",
    rag: "API Key (Pinecone etc.) — leave blank for Bedrock KB to use AWS default credentials",
  }[k];
}

function configHint(k: AssetKind): string {
  return {
    skill: '{"content_md": "..."} — Markdown-format prompt / playbook',
    tool: '{"module": "backend.tools.xxx", "fn": "handler"} — points to Python implementation',
    mcp: '{"url": "https://..."} — MCP server Streamable HTTP endpoint',
    rag: '{"backend": "pgvector" | "bedrock_kb" | "pinecone", ...} — other connection fields',
  }[k];
}


function AssetDetailModal({
  asset,
  onClose,
  isAdmin,
}: {
  asset: AssetRow;
  onClose: () => void;
  isAdmin: boolean;
}) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const { data: me } = useMe();
  const canEdit = isAdmin || me?.id === asset.owner_user_id;

  // Editable fields (initial values = current asset values)
  const [editName, setEditName] = useState(asset.name);
  const [editDescription, setEditDescription] = useState(asset.description || "");
  const [editConfigJson, setEditConfigJson] = useState(() =>
    JSON.stringify(asset.config ?? {}, null, 2),
  );
  const [editCredential, setEditCredential] = useState("");
  const [clearCred, setClearCred] = useState(false);
  const [saveErr, setSaveErr] = useState("");

  const dirty =
    editName !== asset.name ||
    editDescription !== (asset.description || "") ||
    editConfigJson !== JSON.stringify(asset.config ?? {}, null, 2) ||
    editCredential.length > 0 ||
    clearCred;

  const saveEdit = useMutation({
    mutationFn: () => {
      let config: Record<string, unknown>;
      try {
        config = editConfigJson.trim() ? JSON.parse(editConfigJson) : {};
      } catch {
        throw new Error(t("library.configParseError"));
      }
      const payload: Partial<CreateAssetInput> & { clear_credential?: boolean } = {
        name: editName.trim(),
        description: editDescription.trim() || undefined,
        config,
      };
      if (editCredential.trim()) payload.credential = editCredential;
      if (clearCred) payload.clear_credential = true;
      return AssetsAPI.update(asset.id, payload);
    },
    onSuccess: () => {
      setSaveErr("");
      setEditCredential("");
      setClearCred(false);
      qc.invalidateQueries({ queryKey: ["assets", asset.kind] });
      qc.invalidateQueries({ queryKey: ["asset-audit", asset.id] });
      onClose();
    },
    onError: (e: Error) => setSaveErr(e.message),
  });

  const { data: users = [] } = useQuery({
    queryKey: ["admin-users"],
    queryFn: AdminUsersAPI.list,
    enabled: isAdmin,
  });
  const { data: grants = [] } = useQuery({
    queryKey: ["asset-grants", asset.id],
    queryFn: () => AssetsAPI.listGrants(asset.id),
  });
  const { data: usage } = useQuery({
    queryKey: ["asset-usage", asset.id],
    queryFn: () => AssetsAPI.usage(asset.id, 24),
  });
  const { data: audit = [] } = useQuery({
    queryKey: ["asset-audit", asset.id],
    queryFn: () => AssetsAPI.audit(asset.id),
  });

  const [granteePick, setGranteePick] = useState("");
  const grant = useMutation({
    mutationFn: (uid: number) => AssetsAPI.grant(asset.id, uid),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["asset-grants", asset.id] }),
  });
  const revoke = useMutation({
    mutationFn: (uid: number) => AssetsAPI.revoke(asset.id, uid),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["asset-grants", asset.id] }),
  });

  const grantedIds = useMemo(() => new Set(grants.map((g) => g.grantee_user_id)), [grants]);
  const eligible = users.filter(
    (u) => u.id !== asset.owner_user_id && !grantedIds.has(u.id),
  );

  const maxBucket = useMemo(() => {
    if (!usage) return 1;
    return Math.max(1, ...usage.timeseries.map((t) => t.n));
  }, [usage]);

  return (
    <Modal open={true} title={asset.name} subtitle={t(`common.${asset.kind}`)} onClose={onClose} size="lg">
      {/* edit form — admins / owner only */}
      {canEdit && (
        <div className="library-detail-section">
          <h4>{t("library.editSettings")}</h4>
          {asset.metadata && (asset.metadata as any).seed_key && (
            <div
              style={{
                fontSize: 11,
                color: "var(--ink-3)",
                background: "var(--surface-2)",
                padding: "6px 10px",
                borderRadius: 8,
                marginBottom: 10,
              }}
            >
              {t("library.seedNote", { key: String((asset.metadata as any).seed_key) })}
            </div>
          )}
          <div className="modal-field">
            <label>{t("library.nameLabel")}</label>
            <input
              data-testid="edit-asset-name"
              value={editName}
              onChange={(e) => setEditName(e.target.value)}
            />
          </div>
          <div className="modal-field">
            <label>{t("library.descLabel")}</label>
            <input
              data-testid="edit-asset-description"
              value={editDescription}
              onChange={(e) => setEditDescription(e.target.value)}
            />
          </div>
          <div className="modal-field">
            <label>Config (JSON)</label>
            <textarea
              data-testid="edit-asset-config"
              rows={8}
              value={editConfigJson}
              onChange={(e) => setEditConfigJson(e.target.value)}
              style={{
                fontFamily: "ui-monospace,SFMono-Regular,Menlo,monospace",
                fontSize: 12,
              }}
            />
            <div className="hint">{configHint(asset.kind)}</div>
          </div>
          {(asset.kind === "mcp" || asset.kind === "rag") && (
            <div className="modal-field">
              <label>
                Credential{" "}
                {asset.has_credential ? (
                  <span style={{ color: "var(--ok)", fontSize: 10 }}>🔒 {t("models.credentialSet")}</span>
                ) : (
                  <span style={{ color: "var(--ink-4)", fontSize: 10 }}>（{t("models.credentialUnset")}）</span>
                )}
              </label>
              <input
                type="password"
                data-testid="edit-asset-credential"
                value={editCredential}
                onChange={(e) => {
                  setEditCredential(e.target.value);
                  if (e.target.value) setClearCred(false);
                }}
                placeholder={
                  asset.has_credential
                    ? t("library.credentialOverwrite")
                    : credentialPlaceholder(asset.kind)
                }
              />
              {asset.has_credential && !editCredential && (
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
                    checked={clearCred}
                    onChange={(e) => setClearCred(e.target.checked)}
                    data-testid="edit-asset-clear-cred"
                  />
                  {t("library.clearCredential")}
                </label>
              )}
            </div>
          )}
          {saveErr && (
            <div
              style={{
                background: "var(--danger-soft)",
                color: "var(--danger)",
                padding: "8px 12px",
                borderRadius: 8,
                fontSize: 11,
                fontWeight: 700,
                marginBottom: 8,
              }}
            >
              {saveErr}
            </div>
          )}
          <div style={{ display: "flex", justifyContent: "flex-end" }}>
            <button
              className="mbtn primary"
              data-testid="save-asset-edit"
              disabled={!dirty || !editName.trim() || saveEdit.isPending}
              onClick={() => saveEdit.mutate()}
            >
              {saveEdit.isPending ? t("btn.saving") : t("library.saveChanges")}
            </button>
          </div>
        </div>
      )}

      {/* summary */}
      <div className="library-detail-summary">
        <div>
          <div className="library-detail-label">{t("library.totalCallsSummary")}</div>
          <div className="library-detail-value">{usage?.summary.total_calls ?? 0}</div>
        </div>
        <div>
          <div className="library-detail-label">{t("library.userCount")}</div>
          <div className="library-detail-value">{usage?.summary.distinct_users ?? 0}</div>
        </div>
        <div>
          <div className="library-detail-label">{t("library.agentCount")}</div>
          <div className="library-detail-value">{usage?.summary.distinct_agents ?? 0}</div>
        </div>
      </div>

      {/* 24h bar chart */}
      <div className="library-detail-section">
        <h4>{t("library.usage24h")}</h4>
        <div className="library-chart">
          {usage?.timeseries.map((bucket, i) => (
            <div
              key={i}
              className="library-chart-bar"
              style={{ height: `${(bucket.n / maxBucket) * 100}%` }}
              title={`${new Date(bucket.bucket).toLocaleString()} — ${bucket.n}`}
            />
          ))}
        </div>
      </div>

      {/* grants */}
      <div className="library-detail-section">
        <h4>{t("library.grantCount", { count: grants.length })}</h4>
        {grants.length === 0 ? (
          <div style={{ fontSize: 11, color: "var(--ink-4)", marginBottom: 10 }}>
            {t("library.ownerOnlyAsset")}
          </div>
        ) : (
          <ul className="library-grant-list">
            {grants.map((g) => (
              <li key={g.id}>
                <span>{g.grantee_display_name || g.grantee_username}</span>
                <button
                  className="mbtn"
                  onClick={() => revoke.mutate(g.grantee_user_id)}
                  style={{ fontSize: 10 }}
                  data-testid={`asset-revoke-${g.grantee_user_id}`}
                >
                  {t("library.revokeGrant")}
                </button>
              </li>
            ))}
          </ul>
        )}
        {isAdmin && eligible.length > 0 && (
          <div style={{ display: "flex", gap: 8, marginTop: 8 }}>
            <select
              value={granteePick}
              onChange={(e) => setGranteePick(e.target.value)}
              style={{
                flex: 1,
                padding: "6px 10px",
                border: "1px solid var(--border)",
                borderRadius: 8,
                fontSize: 12,
              }}
            >
              <option value="">{t("library.selectUser")}</option>
              {eligible.map((u) => (
                <option key={u.id} value={u.id}>
                  {u.display_name || u.username}
                </option>
              ))}
            </select>
            <button
              className="mbtn primary"
              onClick={() => {
                if (granteePick) {
                  grant.mutate(Number(granteePick));
                  setGranteePick("");
                }
              }}
              disabled={!granteePick}
              style={{ fontSize: 11 }}
            >
              {t("library.grantUser")}
            </button>
          </div>
        )}
      </div>

      {/* audit */}
      <div className="library-detail-section">
        <h4>{t("library.changeLog")}</h4>
        {audit.length === 0 ? (
          <div style={{ fontSize: 11, color: "var(--ink-4)" }}>{t("library.noChanges")}</div>
        ) : (
          <ul className="library-audit-list">
            {audit.slice(0, 10).map((a) => (
              <li key={a.id}>
                <span className="library-audit-action">{a.action}</span>
                <span style={{ flex: 1 }}>{a.actor_username || "system"}</span>
                <span className="library-audit-time">
                  {new Date(a.created_at).toLocaleString("zh-TW")}
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>

      <div style={{ display: "flex", justifyContent: "flex-end", marginTop: 8 }}>
        <button className="mbtn" onClick={onClose}>{t("btn.close")}</button>
      </div>
    </Modal>
  );
}
