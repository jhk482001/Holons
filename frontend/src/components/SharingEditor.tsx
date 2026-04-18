import { useTranslation } from "react-i18next";
import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api, Agent } from "../api/client";

// VIS_LABELS moved to use t() inline

interface ShareRow {
  id: number;
  agent_id: number;
  borrower_username: string;
  scope: string;
  created_at: string;
}

interface UserRow {
  id: number;
  username: string;
  display_name: string | null;
}

export default function SharingEditor({ agent }: { agent: Agent }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const [visibility, setVisibility] = useState<string>((agent as any).visibility || "private");
  const [visibleUserIds, setVisibleUserIds] = useState<number[]>(() => {
    const raw = (agent as any).visible_user_ids;
    if (Array.isArray(raw)) return raw.map((x) => Number(x)).filter(Number.isFinite);
    if (typeof raw === "string") {
      try {
        const parsed = JSON.parse(raw);
        return Array.isArray(parsed) ? parsed.map((x) => Number(x)).filter(Number.isFinite) : [];
      } catch { return []; }
    }
    return [];
  });

  const updateVisibility = useMutation({
    mutationFn: (args: { vis: string; ids: number[] }) =>
      api.post(`/agents/${agent.id}/visibility`, {
        visibility: args.vis,
        visible_user_ids: args.vis === "user_list" ? args.ids : [],
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["agent", agent.id] }),
  });

  const { data: sharesOut = [] } = useQuery({
    queryKey: ["shares-out"],
    queryFn: () => api.get<ShareRow[]>("/shares/out"),
  });

  const { data: users = [] } = useQuery({
    queryKey: ["users"],
    queryFn: () => api.get<UserRow[]>("/users"),
  });

  const [borrowerUsername, setBorrowerUsername] = useState("");
  const [shareError, setShareError] = useState("");

  const createShare = useMutation({
    mutationFn: (username: string) =>
      api.post(`/agents/${agent.id}/shares`, { borrower_username: username }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["shares-out"] });
      setBorrowerUsername("");
      setShareError("");
    },
    onError: (err: Error) => setShareError(err.message),
  });

  const revokeShare = useMutation({
    mutationFn: (sid: number) => api.del(`/shares/${sid}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["shares-out"] }),
  });

  const exportProfile = useMutation({
    mutationFn: () => api.get(`/agents/${agent.id}/export`),
    onSuccess: (data) => {
      // Download as JSON
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `agent-${agent.name}-profile.json`;
      a.click();
      URL.revokeObjectURL(url);
    },
  });

  return (
    <div className="sharing-editor">
      <h3>{t("sharing.visibility")}</h3>
      <p className="help">
        {t("sharing.visibilityHelp")}
      </p>

      <div className="vis-options">
        {(["private", "user_list", "org_wide"] as const).map((k) => (
          <label key={k} className={visibility === k ? "active" : ""}>
            <input
              type="radio"
              name="visibility"
              value={k}
              checked={visibility === k}
              onChange={() => {
                setVisibility(k);
                updateVisibility.mutate({ vis: k, ids: visibleUserIds });
              }}
            />
            <div>
              <div className="vis-name">{t(`sharing.${k}`)}</div>
              <div className="vis-desc">
                {t(`sharing.${k}Desc`)}
              </div>
            </div>
          </label>
        ))}
      </div>

      {visibility === "user_list" && (
        <div className="user-list-picker" data-testid="user-list-picker">
          <div className="ulp-label">{t("sharing.userListLabel")}</div>
          <div className="ulp-tags">
            {visibleUserIds.length === 0 && (
              <div className="ulp-empty">{t("sharing.userListEmpty")}</div>
            )}
            {visibleUserIds.map((id) => {
              const u = users.find((x) => x.id === id);
              return (
                <span key={id} className="ulp-tag">
                  {u ? (u.display_name || u.username) : `user #${id}`}
                  <button
                    type="button"
                    data-testid={`user-list-remove-${id}`}
                    onClick={() => {
                      const next = visibleUserIds.filter((x) => x !== id);
                      setVisibleUserIds(next);
                      updateVisibility.mutate({ vis: "user_list", ids: next });
                    }}
                  >×</button>
                </span>
              );
            })}
          </div>
          <select
            className="ulp-select"
            data-testid="user-list-add"
            value=""
            onChange={(e) => {
              const id = Number(e.target.value);
              if (!id || visibleUserIds.includes(id)) return;
              const next = [...visibleUserIds, id];
              setVisibleUserIds(next);
              updateVisibility.mutate({ vis: "user_list", ids: next });
            }}
          >
            <option value="">{t("sharing.shareWith")}…</option>
            {users
              .filter((u) => !visibleUserIds.includes(u.id))
              .map((u) => (
                <option key={u.id} value={u.id}>
                  {u.display_name || u.username}（{u.username}）
                </option>
              ))}
          </select>
        </div>
      )}

      <h3 style={{ marginTop: 28 }}>{t("sharing.explicitGrant")}</h3>
      <p className="help">
        {t("sharing.explicitGrantHelp")}
      </p>

      <div className="add-share-row">
        <select
          data-testid="share-user-select"
          value={borrowerUsername}
          onChange={(e) => setBorrowerUsername(e.target.value)}
        >
          <option value="">{t("sharing.selectUser")}</option>
          {users
            .filter((u) =>
              !sharesOut.some((s) => s.agent_id === agent.id && s.borrower_username === u.username)
            )
            .map((u) => (
              <option key={u.id} value={u.username}>
                {u.display_name || u.username}（{u.username}）
              </option>
            ))}
        </select>
        <button
          className="add-share-btn"
          data-testid="add-share-btn"
          disabled={!borrowerUsername || createShare.isPending}
          onClick={() => borrowerUsername && createShare.mutate(borrowerUsername)}
        >
          {createShare.isPending ? t("sharing.adding") : t("sharing.addUser")}
        </button>
      </div>
      {shareError && (
        <div className="share-error" data-testid="share-error">{shareError}</div>
      )}

      {sharesOut.filter((s) => s.agent_id === agent.id).length === 0 ? (
        <div className="empty" style={{ marginTop: 10 }}>{t("sharing.noShares")}</div>
      ) : (
        <div className="share-list">
          {sharesOut.filter((s) => s.agent_id === agent.id).map((s) => (
            <div key={s.id} className="share-row" data-testid={`share-row-${s.id}`}>
              <div style={{ flex: 1 }}>
                <div className="share-borrower">{t("sharing.lentTo", { name: s.borrower_username })}</div>
                <div className="share-meta">
                  scope: {s.scope} · {new Date(s.created_at).toLocaleDateString()}
                </div>
              </div>
              <button
                className="revoke-btn"
                data-testid={`revoke-share-${s.id}`}
                onClick={() => revokeShare.mutate(s.id)}
                disabled={revokeShare.isPending}
              >
                {t("sharing.revoke")}
              </button>
            </div>
          ))}
        </div>
      )}

      <h3 style={{ marginTop: 28 }}>{t("sharing.exportTitle")}</h3>
      <p className="help">{t("sharing.exportHelp")}</p>
      <button
        className="export-btn"
        onClick={() => exportProfile.mutate()}
        disabled={exportProfile.isPending}
      >
        {exportProfile.isPending ? t("sharing.exporting") : t("sharing.downloadJson")}
      </button>

      <style>{`
        .sharing-editor h3 { font-size: 13px; font-weight: 800; margin-bottom: 6px; }
        .sharing-editor .help { font-size: 12px; color: var(--ink-3); margin-bottom: 14px; line-height: 1.6; }
        .sharing-editor .empty { font-size: 12px; color: var(--ink-4); padding: 18px; text-align: center; background: var(--surface-2); border-radius: 10px; }
        .vis-options {
          display: flex;
          flex-direction: column;
          gap: 8px;
        }
        .vis-options label {
          display: flex;
          align-items: start;
          gap: 12px;
          padding: 14px 16px;
          background: var(--surface-2);
          border: 1.5px solid var(--border);
          border-radius: 12px;
          cursor: pointer;
        }
        .vis-options label.active {
          background: var(--accent-soft);
          border-color: var(--accent);
        }
        .vis-options label input[type="radio"] { margin-top: 4px; }
        .vis-name { font-size: 13px; font-weight: 800; color: var(--ink); }
        .vis-desc { font-size: 11px; color: var(--ink-3); margin-top: 2px; }
        .share-list { display: flex; flex-direction: column; gap: 8px; }
        .share-row {
          padding: 10px 14px;
          background: var(--surface-2);
          border: 1px solid var(--border);
          border-radius: 10px;
          display: flex;
          align-items: center;
          gap: 10px;
        }
        .share-borrower { font-size: 13px; font-weight: 700; }
        .share-meta { font-size: 10px; color: var(--ink-3); margin-top: 2px; }
        .add-share-row {
          display: flex;
          gap: 8px;
          margin-bottom: 10px;
        }
        .add-share-row select {
          flex: 1;
          padding: 9px 12px;
          border-radius: 10px;
          border: 1px solid var(--border);
          background: var(--surface);
          font: inherit;
          font-size: 12px;
          color: var(--ink);
        }
        .add-share-btn {
          padding: 9px 16px;
          background: var(--accent);
          color: white;
          border: 1px solid var(--accent);
          border-radius: 10px;
          font-size: 12px;
          font-weight: 700;
          cursor: pointer;
        }
        .add-share-btn:disabled { opacity: 0.5; cursor: not-allowed; }
        .revoke-btn {
          padding: 5px 12px;
          background: white;
          color: var(--danger);
          border: 1px solid rgba(232, 100, 80, 0.4);
          border-radius: 8px;
          font-size: 11px;
          font-weight: 700;
          cursor: pointer;
        }
        .share-error {
          color: var(--danger);
          font-size: 11px;
          margin-bottom: 10px;
        }
        .user-list-picker {
          margin-top: 14px;
          padding: 14px 16px;
          background: var(--accent-soft);
          border: 1px solid var(--accent-line);
          border-radius: 12px;
        }
        .ulp-label {
          font-size: 10px;
          font-weight: 800;
          letter-spacing: 1px;
          text-transform: uppercase;
          color: var(--accent);
          margin-bottom: 10px;
        }
        .ulp-tags {
          display: flex;
          flex-wrap: wrap;
          gap: 6px;
          margin-bottom: 10px;
          min-height: 28px;
          align-items: center;
        }
        .ulp-empty {
          font-size: 11px;
          color: var(--ink-4);
          font-style: italic;
        }
        .ulp-tag {
          display: inline-flex;
          align-items: center;
          gap: 4px;
          padding: 4px 8px 4px 12px;
          background: white;
          border: 1px solid var(--accent-line);
          border-radius: 999px;
          font-size: 12px;
          font-weight: 700;
          color: var(--ink);
        }
        .ulp-tag button {
          width: 18px;
          height: 18px;
          border-radius: 50%;
          border: none;
          background: transparent;
          color: var(--ink-4);
          font-size: 14px;
          line-height: 1;
          cursor: pointer;
          display: flex;
          align-items: center;
          justify-content: center;
        }
        .ulp-tag button:hover {
          background: var(--danger-soft);
          color: var(--danger);
        }
        .ulp-select {
          width: 100%;
          padding: 8px 12px;
          border-radius: 8px;
          border: 1px solid var(--accent-line);
          background: white;
          font: inherit;
          font-size: 12px;
          color: var(--ink);
        }
        .export-btn {
          padding: 10px 18px;
          background: var(--surface);
          border: 1px solid var(--border);
          border-radius: 10px;
          font-size: 12px;
          font-weight: 700;
          color: var(--ink-2);
        }
        .export-btn:hover { background: var(--accent-soft); color: var(--accent); border-color: var(--accent-line); }
      `}</style>
    </div>
  );
}
