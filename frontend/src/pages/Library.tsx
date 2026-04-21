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
import { api } from "../api/client";
import Modal from "../components/Modal";
import Avatar from "../components/Avatar";
import Markdown from "../components/Markdown";
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

export type ViewMode = "card" | "list";
const VIEW_MODE_KEY = "library-view-mode";

export default function Library() {
  const { t } = useTranslation();
  const [params, setParams] = useSearchParams();
  const raw = params.get("tab") as TabKey | null;
  const tab: TabKey = (raw && TABS.some((tb) => tb.key === raw)) ? raw : "skill";

  const [viewMode, setViewModeState] = useState<ViewMode>(() => {
    const stored = localStorage.getItem(VIEW_MODE_KEY);
    return stored === "card" ? "card" : "list";
  });
  const setViewMode = (m: ViewMode) => {
    setViewModeState(m);
    localStorage.setItem(VIEW_MODE_KEY, m);
  };

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

      <div style={{
        display: "flex",
        alignItems: "center",
        gap: 12,
        marginBottom: 14,
      }}>
        <div style={{ fontSize: 12, color: "var(--ink-3)", flex: 1 }}>
          {t(currentTab.hintKey)}
        </div>
        <ViewModeToggle mode={viewMode} onChange={setViewMode} />
      </div>

      <AssetKindPanel kind={currentTab.kind} isAdmin={isAdmin} viewMode={viewMode} />

      {currentTab.kind === "skill" && <SelfLearnedSkillsSection viewMode={viewMode} />}
    </div>
  );
}

function ViewModeToggle({
  mode,
  onChange,
}: {
  mode: ViewMode;
  onChange: (m: ViewMode) => void;
}) {
  const { t } = useTranslation();
  return (
    <div
      data-testid="library-view-toggle"
      style={{
        display: "inline-flex",
        border: "1px solid var(--border)",
        borderRadius: 8,
        overflow: "hidden",
      }}
    >
      {(["list", "card"] as ViewMode[]).map((m) => {
        const active = mode === m;
        return (
          <button
            key={m}
            data-testid={`library-view-${m}`}
            onClick={() => onChange(m)}
            style={{
              padding: "5px 12px",
              fontSize: 11,
              fontWeight: 700,
              cursor: "pointer",
              background: active ? "var(--accent)" : "var(--surface)",
              color: active ? "white" : "var(--ink-3)",
              border: "none",
            }}
          >
            {t(m === "list" ? "library.viewList" : "library.viewCards")}
          </button>
        );
      })}
    </div>
  );
}


function AssetKindPanel({ kind, isAdmin, viewMode }: { kind: AssetKind; isAdmin: boolean; viewMode: ViewMode }) {
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
      ) : viewMode === "card" ? (
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
      ) : (
        <div data-testid={`library-list-${kind}`} style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          {visibleRows.map((row) => (
            <AssetListRow
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


function AssetListRow({
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
  return (
    <div
      data-testid={`asset-row-${row.id}`}
      style={{
        display: "flex",
        alignItems: "center",
        gap: 12,
        padding: "10px 14px",
        background: row.enabled ? "var(--surface)" : "var(--surface-2)",
        border: "1px solid var(--border)",
        borderRadius: 10,
        opacity: row.enabled ? 1 : 0.65,
      }}
    >
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontSize: 13, fontWeight: 800, display: "flex", alignItems: "center", gap: 8 }}>
          {row.name}
          {row.has_credential && (
            <span style={{ fontSize: 9, color: "var(--ink-4)" }}>🔒</span>
          )}
        </div>
        {row.description && (
          <div style={{ fontSize: 11, color: "var(--ink-2)", marginTop: 2, lineHeight: 1.4, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
            {row.description}
          </div>
        )}
        <div style={{ fontSize: 10, color: "var(--ink-4)", marginTop: 3, display: "flex", gap: 10, flexWrap: "wrap" }}>
          <span>{t("library.owner")}{row.owner_display_name || row.owner_username || `#${row.owner_user_id}`}</span>
          <span>{t("library.assignedAgents")}{row.assigned_agent_count ?? 0}</span>
          <span>{t("library.totalCalls")}{row.total_calls ?? 0}</span>
          {row.last_used_at && (
            <span>{t("library.lastUsed")}{new Date(row.last_used_at).toLocaleString()}</span>
          )}
        </div>
      </div>

      <label
        className="library-toggle"
        title={row.enabled ? t("library.disabled") : t("library.enabled")}
        style={{ fontSize: 11, display: "inline-flex", alignItems: "center", gap: 6 }}
      >
        <input
          type="checkbox"
          checked={row.enabled}
          onChange={(e) => onToggle(e.target.checked)}
          data-testid={`asset-toggle-${row.id}`}
        />
        <span>{row.enabled ? t("library.enabled") : t("library.disabled")}</span>
      </label>

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


// ==========================================================================
// Self-learned skills section — each agent that owns any agent_skills gets
// its own card below the shared-asset grid. Inlined here (was its own page
// at /skills before) so everything skill-related lives under /library.
// ==========================================================================

const numCoerce = (v: unknown): number => {
  const n = typeof v === "number" ? v : Number(v);
  return Number.isFinite(n) ? n : 0;
};

interface SelfLearnedSkill {
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
  extraction_at?: string | null;
  last_used_at?: string | null;
}

interface SelfLearnedAgentBlock {
  id: number;
  name: string;
  role_title: string | null;
  avatar_config: Record<string, string>;
  is_lead: boolean;
  skills: SelfLearnedSkill[];
}

function SelfLearnedSkillsSection({ viewMode }: { viewMode: ViewMode }) {
  const { t } = useTranslation();
  const { data, isLoading } = useQuery({
    queryKey: ["me-self-learned-skills"],
    queryFn: () => api.get<{ agents: SelfLearnedAgentBlock[] }>("/me/self_learned_skills"),
    refetchInterval: 60_000,
  });
  const agents = data?.agents ?? [];
  const anySkills = agents.some((a) => a.skills.length > 0);

  return (
    <div style={{ marginTop: 32 }}>
      <div style={{
        display: "flex", alignItems: "baseline", gap: 10,
        marginBottom: 8, paddingBottom: 6,
        borderBottom: "1px solid var(--border)",
      }}>
        <h2 style={{ fontSize: 16, fontWeight: 800, margin: 0 }}>
          {t("library.selfLearnedTitle")}
        </h2>
        <div style={{ fontSize: 11, color: "var(--ink-4)", flex: 1 }}>
          {t("library.selfLearnedSubtitle")}
        </div>
      </div>

      {isLoading && (
        <div style={{ fontSize: 12, color: "var(--ink-4)", padding: 20, textAlign: "center" }}>
          {t("btn.loading")}
        </div>
      )}

      {!isLoading && !anySkills && (
        <div style={{
          fontSize: 12, color: "var(--ink-4)",
          padding: "20px 16px", textAlign: "center",
          background: "var(--surface-2)", borderRadius: 10,
          border: "1px dashed var(--border)",
        }}>
          {t("library.selfLearnedEmpty")}
        </div>
      )}

      <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
        {agents.map((a) => (
          <AgentSelfLearnedBlock key={a.id} agent={a} viewMode={viewMode} />
        ))}
      </div>
    </div>
  );
}

function AgentSelfLearnedBlock({ agent, viewMode }: { agent: SelfLearnedAgentBlock; viewMode: ViewMode }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const skills = agent.skills;

  const extract = useMutation({
    mutationFn: () => api.post(`/agents/${agent.id}/skills/extract`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["me-self-learned-skills"] }),
  });

  // Only render agent blocks that have at least one skill — empty blocks
  // clutter the page. The empty-state copy at the section level tells
  // users how to trigger extraction.
  if (skills.length === 0) return null;

  const extractionRounds = useMemo(() => {
    const byRound = new Map<string, SelfLearnedSkill[]>();
    for (const s of skills) {
      if (!s.extraction_at) continue;
      const key = new Date(s.extraction_at).toISOString().slice(0, 19);
      const bucket = byRound.get(key) ?? [];
      bucket.push(s);
      byRound.set(key, bucket);
    }
    return Array.from(byRound.entries())
      .map(([ts, items]) => ({
        ts,
        skills: items,
        cost: items.reduce((a, s) => a + numCoerce(s.extraction_cost_usd), 0),
        inTok: items.reduce((a, s) => a + numCoerce(s.extraction_input_tokens), 0),
        outTok: items.reduce((a, s) => a + numCoerce(s.extraction_output_tokens), 0),
        model: items[0]?.extraction_model_id,
        sourceCount: items[0]?.source_run_ids?.length ?? 0,
      }))
      .sort((a, b) => b.ts.localeCompare(a.ts));
  }, [skills]);

  return (
    <div
      data-testid={`skills-block-${agent.id}`}
      style={{
        background: "var(--surface)",
        border: "1px solid var(--border)",
        borderRadius: 14,
        padding: 16,
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 12 }}>
        <Avatar cfg={agent.avatar_config} size={36} title={agent.name} />
        <div style={{ flex: 1 }}>
          <div style={{ fontSize: 14, fontWeight: 800 }}>{agent.name}</div>
          <div style={{ fontSize: 11, color: "var(--ink-4)" }}>
            {agent.role_title || "—"} · {t("skills.skillCount", { count: skills.length })}
          </div>
        </div>
        <button
          data-testid={`extract-skills-${agent.id}`}
          onClick={() => extract.mutate()}
          disabled={extract.isPending}
          style={{
            padding: "6px 12px", fontSize: 11, fontWeight: 700,
            color: "var(--accent)",
            background: "var(--accent-soft)",
            border: "1px solid var(--accent-line)",
            borderRadius: 8, cursor: "pointer",
          }}
        >
          {extract.isPending ? t("skills.learning") : t("skills.relearn")}
        </button>
      </div>

      {viewMode === "card" ? (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(260px, 1fr))", gap: 10 }}>
          {skills.map((s) => (
            <SelfLearnedSkillCard key={s.id} skill={s} agentId={agent.id} />
          ))}
        </div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          {skills.map((s) => (
            <SelfLearnedSkillRow key={s.id} skill={s} agentId={agent.id} />
          ))}
        </div>
      )}

      {extractionRounds.length > 0 && (
        <ExtractionHistory agentId={agent.id} rounds={extractionRounds} />
      )}
    </div>
  );
}

function useSelfLearnedSkillMutations(skill: SelfLearnedSkill) {
  const qc = useQueryClient();
  const setApproved = useMutation({
    mutationFn: (approved: boolean) =>
      api.post(`/skills/${skill.id}/set_approved`, { approved }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["me-self-learned-skills"] }),
  });
  const remove = useMutation({
    mutationFn: () => api.post(`/skills/${skill.id}/reject`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["me-self-learned-skills"] }),
  });
  return { setApproved, remove };
}

function SelfLearnedSkillRow({ skill, agentId }: { skill: SelfLearnedSkill; agentId: number }) {
  const { t } = useTranslation();
  const [expanded, setExpanded] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const { setApproved, remove } = useSelfLearnedSkillMutations(skill);

  const inTok = numCoerce(skill.extraction_input_tokens);
  const outTok = numCoerce(skill.extraction_output_tokens);
  const cost = numCoerce(skill.extraction_cost_usd);
  const timesUsed = numCoerce(skill.times_used);

  return (
    <>
      <div
        data-testid={`skill-row-${skill.id}`}
        style={{
          background: skill.approved_by_user ? "var(--good-soft)" : "var(--surface-2)",
          border: "1px solid " + (skill.approved_by_user ? "rgba(95, 181, 126, 0.3)" : "var(--border)"),
          borderRadius: 8, overflow: "hidden",
        }}
      >
        <div
          data-testid={`skill-row-header-${skill.id}`}
          onClick={() => setExpanded(!expanded)}
          style={{
            padding: "10px 12px",
            display: "flex", alignItems: "center", gap: 10,
            cursor: "pointer", userSelect: "none",
          }}
        >
          <span style={{
            fontSize: 11, color: "var(--ink-3)",
            transform: expanded ? "rotate(90deg)" : "rotate(0deg)",
            transition: "transform 0.12s ease",
            display: "inline-block", width: 11,
          }}>▶</span>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontSize: 12.5, fontWeight: 800 }}>{skill.name}</div>
            {skill.description && (
              <div style={{ fontSize: 11, color: "var(--ink-2)", marginTop: 2, lineHeight: 1.4 }}>
                {skill.description}
              </div>
            )}
            <div style={{ fontSize: 10, color: "var(--ink-4)", marginTop: 3, display: "flex", gap: 10, flexWrap: "wrap" }}>
              <span>{t("skills.confidence")} {numCoerce(skill.confidence).toFixed(2)}</span>
              <span>{t("skills.used", { count: timesUsed })}</span>
              {skill.last_used_at && (
                <span>{t("skills.lastUsed")} {new Date(skill.last_used_at).toLocaleString()}</span>
              )}
              {skill.extraction_model_id && <span>🧠 {skill.extraction_model_id}</span>}
              {inTok > 0 && <span>↓{inTok} ↑{outTok}</span>}
              {cost > 0 && <span>${cost.toFixed(4)}</span>}
              {skill.extraction_at && <span>{t("skills.learnedAt")} {new Date(skill.extraction_at).toLocaleString()}</span>}
            </div>
          </div>

          <label
            onClick={(e) => e.stopPropagation()}
            title={skill.approved_by_user ? t("library.enabled") : t("library.disabled")}
            style={{ fontSize: 11, display: "inline-flex", alignItems: "center", gap: 6 }}
          >
            <input
              type="checkbox"
              data-testid={`skill-toggle-${skill.id}`}
              checked={skill.approved_by_user}
              onChange={(e) => setApproved.mutate(e.target.checked)}
              disabled={setApproved.isPending}
            />
            <span style={{ color: "var(--ink-3)" }}>
              {skill.approved_by_user ? t("library.enabled") : t("library.disabled")}
            </span>
          </label>

          <div
            style={{ position: "relative" }}
            onClick={(e) => e.stopPropagation()}
          >
            <button
              data-testid={`skill-menu-${skill.id}`}
              aria-label={t("skills.menu")}
              onClick={() => setMenuOpen(!menuOpen)}
              style={{
                padding: "3px 8px", fontSize: 14, fontWeight: 800,
                color: "var(--ink-3)", background: "transparent",
                border: "1px solid var(--border)",
                borderRadius: 6, cursor: "pointer",
                lineHeight: 1,
              }}
            >⋯</button>
            {menuOpen && (
              <>
                {/* click-outside backstop */}
                <div
                  onClick={() => setMenuOpen(false)}
                  style={{ position: "fixed", inset: 0, zIndex: 10 }}
                />
                <div
                  data-testid={`skill-menu-popup-${skill.id}`}
                  style={{
                    position: "absolute", right: 0, top: "calc(100% + 4px)",
                    zIndex: 11,
                    background: "var(--surface)",
                    border: "1px solid var(--border)",
                    borderRadius: 8,
                    boxShadow: "0 4px 16px rgba(0,0,0,0.08)",
                    minWidth: 160, padding: 4,
                  }}
                >
                  <button
                    data-testid={`skill-remove-${skill.id}`}
                    onClick={() => { setMenuOpen(false); setConfirmOpen(true); }}
                    style={{
                      display: "block", width: "100%",
                      padding: "8px 12px", fontSize: 12, fontWeight: 600,
                      color: "var(--danger)",
                      background: "transparent", border: "none",
                      textAlign: "left", cursor: "pointer",
                      borderRadius: 6,
                    }}
                  >
                    {t("skills.removeSkill")}
                  </button>
                </div>
              </>
            )}
          </div>
        </div>

        {expanded && (
          <div
            data-testid={`skill-row-body-${skill.id}`}
            style={{
              borderTop: "1px solid " + (skill.approved_by_user ? "rgba(95, 181, 126, 0.3)" : "var(--border)"),
              padding: "12px 16px",
              background: "var(--surface)",
            }}
          >
            {skill.content_md ? (
              <Markdown content={skill.content_md} />
            ) : (
              <div style={{ fontSize: 11, color: "var(--ink-4)", fontStyle: "italic" }}>
                {t("skills.noContent")}
              </div>
            )}
          </div>
        )}
      </div>

      <Modal
        open={confirmOpen}
        onClose={() => setConfirmOpen(false)}
        title={t("skills.removeConfirmTitle")}
        subtitle={skill.name}
        size="sm"
        footer={
          <>
            <button className="mbtn" onClick={() => setConfirmOpen(false)} disabled={remove.isPending}>
              {t("btn.cancel")}
            </button>
            <button
              className="mbtn danger"
              data-testid={`skill-remove-confirm-${skill.id}`}
              onClick={() => remove.mutate(undefined, { onSuccess: () => setConfirmOpen(false) })}
              disabled={remove.isPending}
            >
              {remove.isPending ? t("skills.removing") : t("skills.removeSkill")}
            </button>
          </>
        }
      >
        <div style={{ fontSize: 13, color: "var(--ink-2)", lineHeight: 1.6 }}>
          {t("skills.removeConfirmBody")}
        </div>
      </Modal>
    </>
  );
}

function SelfLearnedSkillCard({ skill, agentId }: { skill: SelfLearnedSkill; agentId: number }) {
  const { t } = useTranslation();
  const [menuOpen, setMenuOpen] = useState(false);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [detailOpen, setDetailOpen] = useState(false);
  const { setApproved, remove } = useSelfLearnedSkillMutations(skill);
  const timesUsed = numCoerce(skill.times_used);
  const cost = numCoerce(skill.extraction_cost_usd);

  return (
    <>
      <div
        data-testid={`skill-card-${skill.id}`}
        className={`library-card ${skill.approved_by_user ? "" : "disabled"}`}
        style={{ padding: 14 }}
      >
        <div style={{ display: "flex", alignItems: "flex-start", gap: 8, marginBottom: 8 }}>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontSize: 13, fontWeight: 800 }}>{skill.name}</div>
            {skill.description && (
              <div style={{ fontSize: 11, color: "var(--ink-2)", marginTop: 3, lineHeight: 1.4 }}>
                {skill.description}
              </div>
            )}
          </div>
          <label
            title={skill.approved_by_user ? t("library.enabled") : t("library.disabled")}
            style={{ fontSize: 11, display: "inline-flex", alignItems: "center", gap: 6, whiteSpace: "nowrap" }}
          >
            <input
              type="checkbox"
              data-testid={`skill-toggle-${skill.id}`}
              checked={skill.approved_by_user}
              onChange={(e) => setApproved.mutate(e.target.checked)}
              disabled={setApproved.isPending}
            />
            <span style={{ color: "var(--ink-3)" }}>
              {skill.approved_by_user ? t("library.enabled") : t("library.disabled")}
            </span>
          </label>
        </div>

        <div className="library-card-stats" style={{ marginTop: 6 }}>
          <div className="library-stat">
            <div className="library-stat-value">{timesUsed}</div>
            <div className="library-stat-label">{t("skills.timesUsedShort")}</div>
          </div>
          <div className="library-stat">
            <div className="library-stat-value">{numCoerce(skill.confidence).toFixed(2)}</div>
            <div className="library-stat-label">{t("skills.confidence")}</div>
          </div>
          <div className="library-stat">
            <div className="library-stat-value" style={{ fontFamily: "var(--font-mono)" }}>
              ${cost.toFixed(4)}
            </div>
            <div className="library-stat-label">{t("skills.histCost")}</div>
          </div>
        </div>

        {skill.last_used_at && (
          <div style={{ fontSize: 10, color: "var(--ink-4)", marginTop: 8 }}>
            {t("skills.lastUsed")} {new Date(skill.last_used_at).toLocaleString()}
          </div>
        )}

        <div style={{
          display: "flex", alignItems: "center", justifyContent: "space-between",
          marginTop: 10,
        }}>
          <button
            className="mbtn"
            onClick={() => setDetailOpen(!detailOpen)}
            style={{ fontSize: 11 }}
          >
            {detailOpen ? t("skills.collapse") : t("skills.expand")}
          </button>
          <div style={{ position: "relative" }}>
            <button
              data-testid={`skill-menu-${skill.id}`}
              aria-label={t("skills.menu")}
              onClick={() => setMenuOpen(!menuOpen)}
              style={{
                padding: "3px 10px", fontSize: 14, fontWeight: 800,
                color: "var(--ink-3)", background: "transparent",
                border: "1px solid var(--border)",
                borderRadius: 6, cursor: "pointer", lineHeight: 1,
              }}
            >⋯</button>
            {menuOpen && (
              <>
                <div onClick={() => setMenuOpen(false)} style={{ position: "fixed", inset: 0, zIndex: 10 }} />
                <div
                  data-testid={`skill-menu-popup-${skill.id}`}
                  style={{
                    position: "absolute", right: 0, top: "calc(100% + 4px)",
                    zIndex: 11,
                    background: "var(--surface)",
                    border: "1px solid var(--border)",
                    borderRadius: 8,
                    boxShadow: "0 4px 16px rgba(0,0,0,0.08)",
                    minWidth: 160, padding: 4,
                  }}
                >
                  <button
                    data-testid={`skill-remove-${skill.id}`}
                    onClick={() => { setMenuOpen(false); setConfirmOpen(true); }}
                    style={{
                      display: "block", width: "100%",
                      padding: "8px 12px", fontSize: 12, fontWeight: 600,
                      color: "var(--danger)",
                      background: "transparent", border: "none",
                      textAlign: "left", cursor: "pointer",
                      borderRadius: 6,
                    }}
                  >
                    {t("skills.removeSkill")}
                  </button>
                </div>
              </>
            )}
          </div>
        </div>

        {detailOpen && (
          <div
            data-testid={`skill-card-body-${skill.id}`}
            style={{
              borderTop: "1px solid var(--border)",
              marginTop: 10, paddingTop: 10,
              fontSize: 12,
            }}
          >
            {skill.content_md ? (
              <Markdown content={skill.content_md} />
            ) : (
              <div style={{ fontSize: 11, color: "var(--ink-4)", fontStyle: "italic" }}>
                {t("skills.noContent")}
              </div>
            )}
          </div>
        )}
      </div>

      <Modal
        open={confirmOpen}
        onClose={() => setConfirmOpen(false)}
        title={t("skills.removeConfirmTitle")}
        subtitle={skill.name}
        size="sm"
        footer={
          <>
            <button className="mbtn" onClick={() => setConfirmOpen(false)} disabled={remove.isPending}>
              {t("btn.cancel")}
            </button>
            <button
              className="mbtn danger"
              data-testid={`skill-remove-confirm-${skill.id}`}
              onClick={() => remove.mutate(undefined, { onSuccess: () => setConfirmOpen(false) })}
              disabled={remove.isPending}
            >
              {remove.isPending ? t("skills.removing") : t("skills.removeSkill")}
            </button>
          </>
        }
      >
        <div style={{ fontSize: 13, color: "var(--ink-2)", lineHeight: 1.6 }}>
          {t("skills.removeConfirmBody")}
        </div>
      </Modal>
    </>
  );
}


function ExtractionHistory({
  agentId,
  rounds,
}: {
  agentId: number;
  rounds: Array<{
    ts: string;
    skills: SelfLearnedSkill[];
    cost: number;
    inTok: number;
    outTok: number;
    model: string | null | undefined;
    sourceCount: number;
  }>;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);

  return (
    <div style={{ marginTop: 12 }}>
      <button
        data-testid={`extraction-history-toggle-${agentId}`}
        onClick={() => setOpen(!open)}
        style={{
          fontSize: 11, fontWeight: 700, color: "var(--ink-3)",
          background: "transparent", border: "none",
          cursor: "pointer", padding: "3px 0",
          display: "flex", alignItems: "center", gap: 6,
        }}
      >
        <span style={{
          fontSize: 9,
          transform: open ? "rotate(90deg)" : "rotate(0deg)",
          transition: "transform 0.12s ease",
          display: "inline-block", width: 9,
        }}>▶</span>
        {t("skills.extractionHistory", { count: rounds.length })}
      </button>
      {open && (
        <div style={{
          marginTop: 6,
          background: "var(--surface-2)",
          border: "1px solid var(--border)",
          borderRadius: 6, overflow: "hidden",
        }}>
          <table style={{ width: "100%", fontSize: 11, borderCollapse: "collapse" }}>
            <thead>
              <tr style={{ background: "var(--surface)", color: "var(--ink-4)" }}>
                <th style={{ padding: "6px 10px", textAlign: "left" }}>{t("skills.histWhen")}</th>
                <th style={{ padding: "6px 10px", textAlign: "left" }}>{t("skills.histModel")}</th>
                <th style={{ padding: "6px 10px", textAlign: "right" }}>{t("skills.histSources")}</th>
                <th style={{ padding: "6px 10px", textAlign: "right" }}>{t("skills.histProduced")}</th>
                <th style={{ padding: "6px 10px", textAlign: "right" }}>{t("skills.histTokens")}</th>
                <th style={{ padding: "6px 10px", textAlign: "right" }}>{t("skills.histCost")}</th>
              </tr>
            </thead>
            <tbody>
              {rounds.map((r) => (
                <tr key={r.ts} style={{ borderTop: "1px solid var(--border)" }}>
                  <td style={{ padding: "6px 10px" }}>{new Date(r.ts + "Z").toLocaleString()}</td>
                  <td style={{ padding: "6px 10px", color: "var(--ink-3)" }}>{r.model || "—"}</td>
                  <td style={{ padding: "6px 10px", textAlign: "right" }}>{r.sourceCount}</td>
                  <td style={{ padding: "6px 10px", textAlign: "right", fontWeight: 700 }}>{r.skills.length}</td>
                  <td style={{ padding: "6px 10px", textAlign: "right", color: "var(--ink-3)" }}>
                    ↓{r.inTok} ↑{r.outTok}
                  </td>
                  <td style={{ padding: "6px 10px", textAlign: "right", fontFamily: "var(--font-mono)" }}>
                    ${r.cost.toFixed(4)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
