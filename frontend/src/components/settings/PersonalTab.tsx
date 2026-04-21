import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { AuthAPI, api } from "../../api/client";
import { useMe } from "../../auth";

export default function PersonalTab() {
  const { t, i18n } = useTranslation();
  const qc = useQueryClient();
  const { data: me } = useMe();

  const [displayName, setDisplayName] = useState("");
  const [language, setLanguage] = useState("en");
  const [maxSteps, setMaxSteps] = useState(10);
  const [maxTokens, setMaxTokens] = useState(50000);
  useEffect(() => {
    if (me?.display_name) setDisplayName(me.display_name);
    if ((me as any)?.language) setLanguage((me as any).language);
    if ((me as any)?.lead_max_steps) setMaxSteps((me as any).lead_max_steps);
    if ((me as any)?.lead_max_tokens) setMaxTokens((me as any).lead_max_tokens);
  }, [me?.display_name, (me as any)?.language, (me as any)?.lead_max_steps, (me as any)?.lead_max_tokens]);

  const [savedProfile, setSavedProfile] = useState(false);
  const saveProfile = useMutation({
    mutationFn: async () => {
      await api.put("/me", {
        display_name: displayName.trim(),
        language,
        lead_max_steps: maxSteps,
        lead_max_tokens: maxTokens,
      });
    },
    onSuccess: () => {
      i18n.changeLanguage(language);
      qc.invalidateQueries({ queryKey: ["me"] });
      setSavedProfile(true);
      setTimeout(() => setSavedProfile(false), 2000);
    },
  });

  const [oldPw, setOldPw] = useState("");
  const [newPw, setNewPw] = useState("");
  const [newPw2, setNewPw2] = useState("");
  const [pwError, setPwError] = useState("");
  const [pwSaved, setPwSaved] = useState(false);

  const changePw = useMutation({
    mutationFn: () => AuthAPI.updatePassword(oldPw, newPw),
    onSuccess: () => {
      setOldPw("");
      setNewPw("");
      setNewPw2("");
      setPwError("");
      setPwSaved(true);
      setTimeout(() => setPwSaved(false), 2500);
    },
    onError: (err: Error) => setPwError(err.message),
  });

  function submitPasswordChange() {
    setPwError("");
    if (newPw !== newPw2) {
      setPwError(t("personal.passwordMismatch"));
      return;
    }
    if (newPw.length < 4) {
      setPwError(t("personal.passwordTooShort"));
      return;
    }
    changePw.mutate();
  }

  const profileDirty =
    (displayName.trim() !== (me?.display_name || "") && displayName.trim().length > 0) ||
    language !== ((me as any)?.language || "en") ||
    maxSteps !== ((me as any)?.lead_max_steps || 10) ||
    maxTokens !== ((me as any)?.lead_max_tokens || 50000);

  return (
    <div data-testid="settings-personal-tab">
      <section style={{ marginTop: 8, marginBottom: 32 }}>
        <h3 style={{ fontSize: 15, fontWeight: 800, marginBottom: 12 }}>{t("personal.accountInfo")}</h3>
        <div
          style={{
            background: "var(--surface)",
            border: "1px solid var(--border)",
            borderRadius: 16,
            padding: 20,
          }}
        >
          <div className="modal-field">
            <label>{t("personal.username")}</label>
            <input value={me?.username || ""} disabled />
            <div className="hint">{t("personal.usernameHint")}</div>
          </div>
          <div className="modal-field">
            <label>{t("personal.displayName")}</label>
            <input
              data-testid="display-name-input"
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
            />
          </div>
          <div className="modal-field">
            <label>{t("personal.role")}</label>
            <input value={me?.role === "admin" ? t("personal.roleAdmin") : t("personal.roleUser")} disabled />
          </div>
          <div className="modal-field">
            <label>{t("personal.language")}</label>
            <select
              value={language}
              onChange={(e) => setLanguage(e.target.value)}
              style={{
                padding: "8px 12px",
                border: "1px solid var(--border)",
                borderRadius: 8,
                fontSize: 13,
                background: "var(--surface)",
              }}
            >
              <option value="en">English</option>
              <option value="zh-TW">繁體中文 (Traditional Chinese)</option>
            </select>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 10, marginTop: 6 }}>
            <button
              className="mbtn primary"
              data-testid="save-profile-btn"
              onClick={() => saveProfile.mutate()}
              disabled={!profileDirty || saveProfile.isPending}
            >
              {saveProfile.isPending ? t("personal.saving") : t("personal.save")}
            </button>
            {savedProfile && (
              <span
                data-testid="profile-saved"
                style={{ fontSize: 11, color: "var(--good)", fontWeight: 700 }}
              >
                {t("personal.saved")}
              </span>
            )}
          </div>
        </div>
      </section>

      <section>
        <h3 style={{ fontSize: 15, fontWeight: 800, marginBottom: 12 }}>{t("personal.passwordSection")}</h3>
        <div
          style={{
            background: "var(--surface)",
            border: "1px solid var(--border)",
            borderRadius: 16,
            padding: 20,
          }}
        >
          <div className="modal-field">
            <label>{t("personal.oldPassword")}</label>
            <input
              type="password"
              data-testid="old-password-input"
              value={oldPw}
              onChange={(e) => setOldPw(e.target.value)}
              autoComplete="current-password"
            />
          </div>
          <div className="modal-field">
            <label>{t("personal.newPassword")}</label>
            <input
              type="password"
              data-testid="new-password-input"
              value={newPw}
              onChange={(e) => setNewPw(e.target.value)}
              autoComplete="new-password"
            />
          </div>
          <div className="modal-field">
            <label>{t("personal.confirmPassword")}</label>
            <input
              type="password"
              data-testid="new-password-confirm"
              value={newPw2}
              onChange={(e) => setNewPw2(e.target.value)}
              autoComplete="new-password"
            />
          </div>
          {pwError && (
            <div
              data-testid="password-error"
              style={{ color: "var(--danger)", fontSize: 12, marginBottom: 10 }}
            >
              {pwError}
            </div>
          )}
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <button
              className="mbtn primary"
              data-testid="change-password-btn"
              onClick={submitPasswordChange}
              disabled={!oldPw || !newPw || !newPw2 || changePw.isPending}
            >
              {changePw.isPending ? t("personal.changingPassword") : t("personal.changePassword")}
            </button>
            {pwSaved && (
              <span
                data-testid="password-saved"
                style={{ fontSize: 11, color: "var(--good)", fontWeight: 700 }}
              >
                {t("personal.passwordChanged")}
              </span>
            )}
          </div>
        </div>
      </section>

      <section style={{ marginTop: 32 }}>
        <h3 style={{ fontSize: 15, fontWeight: 800, marginBottom: 12 }}>{t("personal.leadWorkflowSettings")}</h3>
        <div style={{ fontSize: 12, color: "var(--ink-3)", marginBottom: 16, lineHeight: 1.6 }}>
          {t("personal.leadWorkflowDesc")}
        </div>
        <div
          style={{
            background: "var(--surface)",
            border: "1px solid var(--border)",
            borderRadius: 16,
            padding: 20,
          }}
        >
          <div className="modal-field">
            <label>{t("personal.leadMaxSteps")}</label>
            <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
              <input
                type="range"
                min={1}
                max={200}
                value={maxSteps}
                onChange={(e) => setMaxSteps(Number(e.target.value))}
                style={{ flex: 1 }}
              />
              <input
                type="number"
                value={maxSteps}
                onChange={(e) => setMaxSteps(Math.max(1, Number(e.target.value)))}
                style={{ width: 70, textAlign: "center", padding: "6px 8px", border: "1px solid var(--border)", borderRadius: 8 }}
              />
            </div>
            <div className="hint">{t("personal.leadMaxStepsHint")}</div>
          </div>
          <div className="modal-field">
            <label>{t("personal.leadMaxTokens")}</label>
            <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
              <input
                type="range"
                min={5000}
                max={500000}
                step={5000}
                value={maxTokens}
                onChange={(e) => setMaxTokens(Number(e.target.value))}
                style={{ flex: 1 }}
              />
              <input
                type="number"
                value={maxTokens}
                onChange={(e) => setMaxTokens(Math.max(1000, Number(e.target.value)))}
                style={{ width: 100, textAlign: "center", padding: "6px 8px", border: "1px solid var(--border)", borderRadius: 8 }}
              />
            </div>
            <div className="hint">{t("personal.leadMaxTokensHint")}</div>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 10, marginTop: 8 }}>
            <button
              className="mbtn primary"
              onClick={() => saveProfile.mutate()}
              disabled={!profileDirty || saveProfile.isPending}
            >
              {saveProfile.isPending ? t("personal.saving") : t("personal.save")}
            </button>
            {savedProfile && (
              <span style={{ fontSize: 11, color: "var(--good)", fontWeight: 700 }}>
                {t("personal.saved")}
              </span>
            )}
          </div>
        </div>

        {/* How Lead works — explanation */}
        <details style={{ marginTop: 16 }}>
          <summary style={{ fontSize: 13, fontWeight: 700, cursor: "pointer", color: "var(--ink-2)" }}>
            {t("personal.leadHowItWorks")}
          </summary>
          <div
            style={{
              fontSize: 12,
              color: "var(--ink-3)",
              lineHeight: 1.8,
              marginTop: 10,
              padding: "12px 16px",
              background: "var(--surface-2)",
              borderRadius: 12,
              whiteSpace: "pre-line",
            }}
          >
            {t("personal.leadHowItWorksContent")}
          </div>
        </details>
      </section>

      <AutoTopupSection />
      <BackupSection />
    </div>
  );
}


interface BackupInfo {
  backend: string;
  exportable: boolean;
  path?: string;
  exists?: boolean;
  size_bytes?: number;
  modified_at?: number;
  reason?: string;
}


function BackupSection() {
  const { t } = useTranslation();
  const { data, isLoading } = useQuery({
    queryKey: ["backup-info"],
    queryFn: () => api.get<BackupInfo>("/backup/info"),
  });
  const fmt = (n: number) => {
    if (n < 1024) return `${n} B`;
    if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
    return `${(n / 1024 / 1024).toFixed(2)} MB`;
  };
  return (
    <section style={{ marginTop: 28 }}>
      <h3 style={{ fontSize: 14, marginBottom: 10 }}>{t("backup.title")}</h3>
      <p style={{ fontSize: 12, color: "var(--ink-3)", marginTop: 0 }}>
        {t("backup.subtitle")}
      </p>
      {isLoading && (
        <div style={{ fontSize: 12, color: "var(--ink-4)" }}>
          {t("btn.loading")}
        </div>
      )}
      {data && data.exportable && data.exists !== false && (
        <div style={{
          padding: 12, background: "var(--surface-2)",
          borderRadius: 8, fontSize: 12, color: "var(--ink-2)",
          marginBottom: 12,
        }}>
          <div><strong>{t("backup.backend")}:</strong> {data.backend}</div>
          <div style={{ fontFamily: "monospace", fontSize: 11, marginTop: 4 }}>
            {data.path}
          </div>
          <div style={{ marginTop: 6 }}>
            <strong>{t("backup.size")}:</strong> {data.size_bytes ? fmt(data.size_bytes) : "—"}
            {data.modified_at && (
              <>{"  · "}<strong>{t("backup.modified")}:</strong>{" "}
              {new Date(data.modified_at * 1000).toLocaleString()}</>
            )}
          </div>
        </div>
      )}
      {data && !data.exportable && (
        <div style={{
          padding: 12, background: "var(--warn-soft)",
          borderRadius: 8, fontSize: 12, color: "var(--warn)",
          marginBottom: 12,
        }}>
          {data.reason}
        </div>
      )}
      {data && data.exportable && (
        <a
          href="/api/backup/download"
          className="mbtn primary"
          download
          style={{ display: "inline-block", textDecoration: "none",
            padding: "8px 16px", fontSize: 13 }}
        >
          {t("backup.downloadBtn")}
        </a>
      )}
      <details style={{ marginTop: 16, fontSize: 12 }}>
        <summary style={{ cursor: "pointer", color: "var(--ink-3)" }}>
          {t("backup.restoreHow")}
        </summary>
        <ol style={{ color: "var(--ink-2)", lineHeight: 1.7, marginTop: 8 }}>
          <li>{t("backup.restoreStep1")}</li>
          <li>{t("backup.restoreStep2")}</li>
          <li>{t("backup.restoreStep3")}</li>
          <li>{t("backup.restoreStep4")}</li>
        </ol>
      </details>
    </section>
  );
}


function AutoTopupSection() {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const [enabled, setEnabled] = useState(false);
  const [perTopup, setPerTopup] = useState(1.0);
  const [maxPerDay, setMaxPerDay] = useState(3);
  const [loaded, setLoaded] = useState(false);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    api.get<{ enabled: boolean; per_topup_cost: number; max_per_day: number }>(
      "/me/autotopup",
    ).then((r) => {
      setEnabled(r.enabled);
      setPerTopup(r.per_topup_cost);
      setMaxPerDay(r.max_per_day);
      setLoaded(true);
    }).catch(() => setLoaded(true));
  }, []);

  const save = useMutation({
    mutationFn: async () => api.put("/me/autotopup", {
      enabled, per_topup_cost: perTopup, max_per_day: maxPerDay,
    }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["me"] });
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    },
  });

  if (!loaded) return null;

  return (
    <section style={{ marginTop: 32 }}>
      <h3 style={{ fontSize: 15, fontWeight: 800, marginBottom: 12 }}>
        {t("personal.autoTopupTitle")}
      </h3>
      <div style={{
        background: "var(--surface)", border: "1px solid var(--border)",
        borderRadius: 16, padding: 20,
      }}>
        <div style={{ fontSize: 12, color: "var(--ink-3)", marginBottom: 14 }}>
          {t("personal.autoTopupDesc")}
        </div>

        <label style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 16 }}>
          <input type="checkbox" checked={enabled}
                 onChange={(e) => setEnabled(e.target.checked)} />
          <span style={{ fontSize: 13, fontWeight: 700 }}>{t("personal.autoTopupEnable")}</span>
        </label>

        <div className="modal-field">
          <label>{t("personal.autoTopupAmount")}</label>
          <input type="number" min={0.1} max={5} step={0.1}
                 value={perTopup}
                 onChange={(e) => setPerTopup(Number(e.target.value))}
                 disabled={!enabled}
                 style={{ width: 120 }} />
        </div>

        <div className="modal-field">
          <label>{t("personal.autoTopupMax")}</label>
          <input type="number" min={1} max={10}
                 value={maxPerDay}
                 onChange={(e) => setMaxPerDay(Number(e.target.value))}
                 disabled={!enabled}
                 style={{ width: 120 }} />
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: 10, marginTop: 8 }}>
          <button className="mbtn primary" onClick={() => save.mutate()}
                  disabled={save.isPending}>
            {save.isPending ? t("btn.saving") : t("btn.save")}
          </button>
          {saved && <span style={{ fontSize: 11, color: "var(--good)", fontWeight: 700 }}>{t("btn.saved")}</span>}
        </div>
      </div>
    </section>
  );
}
