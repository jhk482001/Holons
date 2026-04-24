import { useTranslation } from "react-i18next";
import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AdminUsersAPI, AdminUserRow, UserRole, UserQuotasAPI, UserQuotaRow } from "../../api/client";
import Modal from "../Modal";

export default function UserManagementTab() {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const { data: users = [], isLoading } = useQuery({
    queryKey: ["admin-users"],
    queryFn: AdminUsersAPI.list,
  });

  const [createOpen, setCreateOpen] = useState(false);
  const [editing, setEditing] = useState<AdminUserRow | null>(null);
  const [pwTarget, setPwTarget] = useState<AdminUserRow | null>(null);
  const [toDelete, setToDelete] = useState<AdminUserRow | null>(null);
  const [quotaTarget, setQuotaTarget] = useState<AdminUserRow | null>(null);
  const [err, setErr] = useState("");

  const deleteUser = useMutation({
    mutationFn: (id: number) => AdminUsersAPI.remove(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["admin-users"] });
      setToDelete(null);
      setErr("");
    },
    onError: (e: Error) => setErr(e.message),
  });

  const changeRole = useMutation({
    mutationFn: ({ id, role }: { id: number; role: UserRole }) =>
      AdminUsersAPI.update(id, { role }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["admin-users"] }),
    onError: (e: Error) => setErr(e.message),
  });

  return (
    <div data-testid="settings-users-tab">
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          marginBottom: 16,
        }}
      >
        <h3 style={{ fontSize: 15, fontWeight: 800 }}>{t("users.title")}</h3>
        <button
          className="mbtn primary"
          data-testid="create-user-btn"
          onClick={() => setCreateOpen(true)}
        >
          {t("users.create")}
        </button>
      </div>

      {err && (
        <div
          data-testid="users-error"
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
        <div style={{ padding: 30, color: "var(--ink-4)", textAlign: "center" }}>{t("btn.loading")}</div>
      ) : (
        <div
          style={{
            background: "var(--surface)",
            border: "1px solid var(--border)",
            borderRadius: 14,
            overflow: "hidden",
          }}
        >
          <table
            style={{
              width: "100%",
              borderCollapse: "collapse",
              fontSize: 13,
            }}
            data-testid="users-table"
          >
            <thead>
              <tr style={{ background: "var(--surface-2)", textAlign: "left" }}>
                <th style={{ padding: "12px 16px", fontWeight: 700, color: "var(--ink-3)" }}>
                  {t("users.table.user")}
                </th>
                <th style={{ padding: "12px 16px", fontWeight: 700, color: "var(--ink-3)" }}>
                  {t("users.table.role")}
                </th>
                <th style={{ padding: "12px 16px", fontWeight: 700, color: "var(--ink-3)" }}>
                  {t("users.table.lastSeen")}
                </th>
                <th style={{ padding: "12px 16px", fontWeight: 700, color: "var(--ink-3)", textAlign: "right" }}>
                  {t("users.table.actions")}
                </th>
              </tr>
            </thead>
            <tbody>
              {users.map((u) => (
                <tr
                  key={u.id}
                  data-testid={`user-row-${u.id}`}
                  style={{ borderTop: "1px solid var(--border)" }}
                >
                  <td style={{ padding: "14px 16px" }}>
                    <div style={{ fontWeight: 700 }}>{u.display_name || u.username}</div>
                    <div style={{ fontSize: 11, color: "var(--ink-4)" }}>@{u.username}</div>
                  </td>
                  <td style={{ padding: "14px 16px" }}>
                    <select
                      data-testid={`user-role-${u.id}`}
                      value={u.role}
                      onChange={(e) =>
                        changeRole.mutate({ id: u.id, role: e.target.value as UserRole })
                      }
                      style={{
                        padding: "4px 8px",
                        border: "1px solid var(--border)",
                        borderRadius: 8,
                        fontSize: 12,
                        background: "var(--surface)",
                      }}
                    >
                      <option value="admin">{t("users.admin")}</option>
                      <option value="user">{t("users.regular")}</option>
                    </select>
                  </td>
                  <td style={{ padding: "14px 16px", fontSize: 11, color: "var(--ink-3)" }}>
                    {u.last_seen_at
                      ? new Date(u.last_seen_at).toLocaleString("zh-TW")
                      : t("users.never")}
                  </td>
                  <td style={{ padding: "14px 16px", textAlign: "right" }}>
                    <button
                      className="mbtn"
                      data-testid={`quota-${u.id}`}
                      onClick={() => setQuotaTarget(u)}
                      style={{ marginRight: 6, fontSize: 11 }}
                    >
                      {t("users.quota")}
                    </button>
                    <button
                      className="mbtn"
                      data-testid={`reset-pw-${u.id}`}
                      onClick={() => setPwTarget(u)}
                      style={{ marginRight: 6, fontSize: 11 }}
                    >
                      {t("users.reset")}
                    </button>
                    <button
                      className="mbtn danger"
                      data-testid={`delete-user-${u.id}`}
                      onClick={() => setToDelete(u)}
                      style={{ fontSize: 11 }}
                    >
                      {t("users.delete")}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {createOpen && (
        <CreateUserModal
          onClose={() => {
            setCreateOpen(false);
            setErr("");
          }}
          onError={setErr}
        />
      )}
      {pwTarget && (
        <ResetPasswordModal
          user={pwTarget}
          onClose={() => setPwTarget(null)}
          onError={setErr}
        />
      )}
      {quotaTarget && (
        <UserQuotaModal
          user={quotaTarget}
          onClose={() => setQuotaTarget(null)}
          onError={setErr}
        />
      )}
      {toDelete && (
        <Modal open={true} title={t("users.deleteTitle")} onClose={() => setToDelete(null)}>
          <p style={{ fontSize: 13, marginBottom: 16 }}>
            {t("users.deleteConfirm")}
          </p>
          <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
            <button className="mbtn" onClick={() => setToDelete(null)}>
              {t("btn.cancel")}
            </button>
            <button
              className="mbtn danger"
              data-testid="confirm-delete-user"
              onClick={() => deleteUser.mutate(toDelete.id)}
              disabled={deleteUser.isPending}
            >
              {deleteUser.isPending ? t("agentDetail.deleting") : t("agentDetail.deleteSubmit")}
            </button>
          </div>
        </Modal>
      )}
    </div>
  );
}

function CreateUserModal({
  onClose,
  onError,
}: {
  onClose: () => void;
  onError: (msg: string) => void;
}) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const [username, setUsername] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState<UserRole>("user");

  const create = useMutation({
    mutationFn: () =>
      AdminUsersAPI.create({
        username: username.trim(),
        password,
        display_name: displayName.trim() || undefined,
        role,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["admin-users"] });
      onClose();
    },
    onError: (e: Error) => onError(e.message),
  });

  const valid = username.trim().length >= 2 && password.length >= 6;

  return (
    <Modal open={true} title={t("users.createTitle")} onClose={onClose}>
      <div className="modal-field">
        <label>{t("users.usernameLabel")}</label>
        <input
          data-testid="new-user-username"
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          placeholder={t("users.usernamePlaceholder")}
        />
      </div>
      <div className="modal-field">
        <label>{t("users.displayNameLabel")}</label>
        <input
          data-testid="new-user-display-name"
          value={displayName}
          onChange={(e) => setDisplayName(e.target.value)}
        />
      </div>
      <div className="modal-field">
        <label>{t("users.initialPassword")}</label>
        <input
          type="password"
          data-testid="new-user-password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />
      </div>
      <div className="modal-field">
        <label>{t("users.roleLabel")}</label>
        <select
          data-testid="new-user-role"
          value={role}
          onChange={(e) => setRole(e.target.value as UserRole)}
        >
          <option value="user">{t("users.regular")}</option>
          <option value="admin">{t("users.admin")}</option>
        </select>
      </div>
      <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
        <button className="mbtn" onClick={onClose}>
          {t("btn.cancel")}
        </button>
        <button
          className="mbtn primary"
          data-testid="confirm-create-user"
          onClick={() => create.mutate()}
          disabled={!valid || create.isPending}
        >
          {create.isPending ? t("btn.saving") : t("library.createSubmit")}
        </button>
      </div>
    </Modal>
  );
}

function ResetPasswordModal({
  user,
  onClose,
  onError,
}: {
  user: AdminUserRow;
  onClose: () => void;
  onError: (msg: string) => void;
}) {
  const { t } = useTranslation();
  const [pw, setPw] = useState("");
  const reset = useMutation({
    mutationFn: () => AdminUsersAPI.resetPassword(user.id, pw),
    onSuccess: () => onClose(),
    onError: (e: Error) => onError(e.message),
  });

  return (
    <Modal open={true} title={t("users.resetTitle", { name: user.username })} onClose={onClose}>
      <div className="modal-field">
        <label>{t("users.newPasswordLabel")}</label>
        <input
          type="password"
          data-testid="reset-pw-input"
          value={pw}
          onChange={(e) => setPw(e.target.value)}
        />
      </div>
      <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
        <button className="mbtn" onClick={onClose}>
          {t("btn.cancel")}
        </button>
        <button
          className="mbtn primary"
          data-testid="confirm-reset-pw"
          onClick={() => reset.mutate()}
          disabled={pw.length < 6 || reset.isPending}
        >
          {reset.isPending ? t("users.resetting") : t("users.reset")}
        </button>
      </div>
    </Modal>
  );
}


// Admin quota editor — raw limits plus the soft-warning thresholds. Null
// means "no limit on this axis". Warn pct is clamped to 10–95 server-side.
function UserQuotaModal({
  user,
  onClose,
  onError,
}: {
  user: AdminUserRow;
  onClose: () => void;
  onError: (msg: string) => void;
}) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const { data: summary } = useQuery({
    queryKey: ["admin-user-quota", user.id],
    queryFn: () => UserQuotasAPI.getFor(user.id),
  });

  const [form, setForm] = useState<UserQuotaRow>({
    daily_token_limit: null,
    daily_cost_limit_usd: null,
    monthly_token_limit: null,
    monthly_cost_limit_usd: null,
    daily_warn_pct: 80,
    monthly_warn_pct: 80,
  });
  useEffect(() => {
    if (summary?.quota) {
      setForm({
        daily_token_limit: summary.quota.daily_token_limit ?? null,
        daily_cost_limit_usd: summary.quota.daily_cost_limit_usd ?? null,
        monthly_token_limit: summary.quota.monthly_token_limit ?? null,
        monthly_cost_limit_usd: summary.quota.monthly_cost_limit_usd ?? null,
        daily_warn_pct: summary.quota.daily_warn_pct ?? 80,
        monthly_warn_pct: summary.quota.monthly_warn_pct ?? 80,
      });
    }
  }, [summary?.quota]);

  const save = useMutation({
    mutationFn: () => UserQuotasAPI.setFor(user.id, form),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["admin-user-quota", user.id] });
      qc.invalidateQueries({ queryKey: ["my-user-quota"] });
      onClose();
    },
    onError: (e: Error) => onError(e.message),
  });

  function num(v: string): number | null {
    const s = v.trim();
    if (!s) return null;
    const n = Number(s);
    return isNaN(n) ? null : n;
  }

  return (
    <Modal open={true} title={t("users.quotaTitle", { name: user.username })} onClose={onClose} size="md">
      {summary && (
        <div style={{
          fontSize: 12, color: "var(--ink-3)",
          marginBottom: 14, padding: 10,
          background: "var(--surface-2)", borderRadius: 8,
        }}>
          <div>{t("users.quotaToday")}: ${summary.daily.cost_usd.toFixed(2)} · {summary.daily.tokens.toLocaleString()} tok</div>
          <div>{t("users.quotaMonth")}: ${summary.monthly.cost_usd.toFixed(2)} · {summary.monthly.tokens.toLocaleString()} tok</div>
        </div>
      )}

      <div className="modal-field">
        <label>{t("users.dailyCostLimit")}</label>
        <input
          type="number" step="0.01" min="0"
          value={form.daily_cost_limit_usd ?? ""}
          onChange={(e) => setForm({ ...form, daily_cost_limit_usd: num(e.target.value) })}
          placeholder={t("users.noLimit")}
          data-testid="quota-daily-cost"
        />
      </div>
      <div className="modal-field">
        <label>{t("users.monthlyCostLimit")}</label>
        <input
          type="number" step="0.01" min="0"
          value={form.monthly_cost_limit_usd ?? ""}
          onChange={(e) => setForm({ ...form, monthly_cost_limit_usd: num(e.target.value) })}
          placeholder={t("users.noLimit")}
          data-testid="quota-monthly-cost"
        />
      </div>
      <div className="modal-field">
        <label>{t("users.dailyTokenLimit")}</label>
        <input
          type="number" step="1000" min="0"
          value={form.daily_token_limit ?? ""}
          onChange={(e) => setForm({ ...form, daily_token_limit: num(e.target.value) })}
          placeholder={t("users.noLimit")}
          data-testid="quota-daily-tokens"
        />
      </div>
      <div className="modal-field">
        <label>{t("users.monthlyTokenLimit")}</label>
        <input
          type="number" step="1000" min="0"
          value={form.monthly_token_limit ?? ""}
          onChange={(e) => setForm({ ...form, monthly_token_limit: num(e.target.value) })}
          placeholder={t("users.noLimit")}
          data-testid="quota-monthly-tokens"
        />
      </div>
      <div className="modal-field">
        <label>{t("users.warnPct")}</label>
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <input
            type="number" min="10" max="95"
            value={form.daily_warn_pct ?? 80}
            onChange={(e) => setForm({ ...form, daily_warn_pct: Number(e.target.value) })}
            style={{ width: 80 }}
            data-testid="quota-daily-warn"
          />
          <span style={{ fontSize: 12, color: "var(--ink-3)" }}>% {t("users.warnDaily")}</span>
          <input
            type="number" min="10" max="95"
            value={form.monthly_warn_pct ?? 80}
            onChange={(e) => setForm({ ...form, monthly_warn_pct: Number(e.target.value) })}
            style={{ width: 80, marginLeft: 10 }}
            data-testid="quota-monthly-warn"
          />
          <span style={{ fontSize: 12, color: "var(--ink-3)" }}>% {t("users.warnMonthly")}</span>
        </div>
        <div className="hint">{t("users.warnHint")}</div>
      </div>
      <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
        <button className="mbtn" onClick={onClose}>{t("btn.cancel")}</button>
        <button
          className="mbtn primary"
          data-testid="quota-save"
          onClick={() => save.mutate()}
          disabled={save.isPending}
        >
          {save.isPending ? t("btn.saving") : t("btn.save")}
        </button>
      </div>
    </Modal>
  );
}
