import { useState } from "react";
import { useTranslation } from "react-i18next";
import { invoke } from "@tauri-apps/api/core";
import { setConnectionConfig, AppMode } from "./api-adapter";
import LangSwitcher from "./LangSwitcher";
import holonsLogo from "./assets/holons-logo.png";
import "./desktop.css";

function BrandHeader() {
  return (
    <div className="setup-brand">
      <img src={holonsLogo} alt="Holons" className="setup-brand-logo" draggable={false} />
      <div className="setup-brand-name">Holons</div>
    </div>
  );
}

export default function ConnectionSetup({
  onComplete,
}: {
  onComplete: (mode: AppMode) => void;
}) {
  const { t } = useTranslation();
  const [step, setStep] = useState<"choose" | "enterprise" | "personal">("choose");

  return (
    <div className="desktop-login-overlay" data-interactive>
      {step === "choose" && (
        <div className="setup-card">
          <LangSwitcher />
          <BrandHeader />
          <div className="setup-sub">{t("setup.subtitle")}</div>

          <button
            className="setup-option"
            onClick={() => setStep("enterprise")}
          >
            <div className="setup-option-icon">🏢</div>
            <div className="setup-option-text">
              <strong>{t("setup.enterprise")}</strong>
              <span>{t("setup.enterpriseDesc")}</span>
            </div>
          </button>

          <button
            className="setup-option"
            onClick={() => setStep("personal")}
          >
            <div className="setup-option-icon">💻</div>
            <div className="setup-option-text">
              <strong>{t("setup.personal")}</strong>
              <span>{t("setup.personalDesc")}</span>
            </div>
          </button>
        </div>
      )}

      {step === "enterprise" && (
        <EnterpriseSetup
          onBack={() => setStep("choose")}
          onComplete={() => onComplete("enterprise")}
        />
      )}

      {step === "personal" && (
        <PersonalSetup
          onBack={() => setStep("choose")}
          onComplete={() => onComplete("personal")}
        />
      )}
    </div>
  );
}


function EnterpriseSetup({
  onBack,
  onComplete,
}: {
  onBack: () => void;
  onComplete: () => void;
}) {
  const { t } = useTranslation();
  const [url, setUrl] = useState("https://");
  const [testing, setTesting] = useState(false);
  const [error, setError] = useState("");

  async function testAndSave() {
    setError("");
    setTesting(true);
    const cleanUrl = url.replace(/\/+$/, "");
    try {
      const resp = await fetch(`${cleanUrl}/api/health`, {
        signal: AbortSignal.timeout(10_000),
      });
      if (!resp.ok) throw new Error(t("setup.errServerStatus", { status: resp.status }));
      await setConnectionConfig("enterprise", cleanUrl);
      onComplete();
    } catch (e: any) {
      setError(
        e.name === "TimeoutError"
          ? t("setup.errTimeout")
          : t("setup.errCannotConnect", { message: e.message }),
      );
    } finally {
      setTesting(false);
    }
  }

  return (
    <div className="setup-card">
      <LangSwitcher />
      <BrandHeader />
      <div className="setup-title">{t("setup.enterpriseTitle")}</div>
      <div className="setup-sub">{t("setup.enterpriseHint")}</div>

      <input
        type="url"
        value={url}
        onChange={(e) => setUrl(e.target.value)}
        placeholder="https://agent.company.com"
        autoFocus
        style={{
          width: "100%",
          padding: "10px 14px",
          background: "rgba(255,255,255,0.06)",
          border: "1px solid var(--desktop-border)",
          borderRadius: 10,
          color: "var(--desktop-text)",
          fontSize: 14,
        }}
      />

      {error && <div className="desktop-login-error">{error}</div>}

      <div style={{ display: "flex", gap: 8, marginTop: 12 }}>
        <button className="setup-btn secondary" onClick={onBack}>
          {t("setup.back")}
        </button>
        <button
          className="setup-btn primary"
          onClick={testAndSave}
          disabled={testing || !url || url === "https://"}
        >
          {testing ? t("setup.testing") : t("setup.connect")}
        </button>
      </div>
    </div>
  );
}


interface PreflightResult {
  status: "ok" | "upgrade_needed" | "error";
  mode?: string;
  db_path?: string;
  db_size_bytes?: number;
  exists?: boolean;
  missing_tables?: string[];
  missing_columns?: string[];
  error?: string;
  note?: string;
}

function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

function PersonalSetup({
  onBack,
  onComplete,
}: {
  onBack: () => void;
  onComplete: () => void;
}) {
  const { t } = useTranslation();
  const [status, setStatus] = useState<
    "idle" | "checking" | "upgrade_prompt" | "backing_up" | "starting" | "ready" | "error"
  >("idle");
  const [error, setError] = useState("");
  const [preflight, setPreflight] = useState<PreflightResult | null>(null);
  const [backupPath, setBackupPath] = useState<string | null>(null);

  async function waitForHealth(localUrl: string): Promise<boolean> {
    for (let i = 0; i < 30; i++) {
      try {
        const r = await fetch(`${localUrl}/api/health`, {
          signal: AbortSignal.timeout(2_000),
        });
        if (r.ok) return true;
      } catch {
        // Not ready yet
      }
      await new Promise((r) => setTimeout(r, 1_000));
    }
    return false;
  }

  async function bootSidecar() {
    setStatus("starting");
    const port = await invoke<number>("start_sidecar");
    const localUrl = `http://localhost:${port}`;
    const ok = await waitForHealth(localUrl);
    if (!ok) throw new Error(t("setup.errStartTimeout"));
    await setConnectionConfig("personal", localUrl);
    setStatus("ready");
    setTimeout(onComplete, 500);
  }

  async function startLocal() {
    setStatus("checking");
    setError("");
    try {
      const pf = await invoke<PreflightResult>("check_db_upgrade");
      setPreflight(pf);
      if (pf.status === "upgrade_needed") {
        setStatus("upgrade_prompt");
        return;
      }
      // ok / error / unknown — proceed and let sidecar report back.
      await bootSidecar();
    } catch (e: any) {
      setStatus("error");
      setError(e.message || t("setup.errStartFailed"));
    }
  }

  async function upgradeWithBackup(doBackup: boolean) {
    setStatus(doBackup ? "backing_up" : "starting");
    setError("");
    try {
      if (doBackup) {
        const res = await invoke<{ path: string; size_bytes: number }>(
          "backup_personal_db",
        );
        setBackupPath(res.path);
      }
      await bootSidecar();
    } catch (e: any) {
      setStatus("error");
      setError(e.message || t("setup.errStartFailed"));
    }
  }

  return (
    <div className="setup-card">
      <LangSwitcher />
      <BrandHeader />
      <div className="setup-title">{t("setup.personalTitle")}</div>
      <div className="setup-sub">{t("setup.personalHint")}</div>

      {status === "idle" && (
        <>
          <div style={{ fontSize: 12, color: "var(--desktop-text-dim)", lineHeight: 1.6, marginBottom: 12 }}>
            {t("setup.personalBody")}
          </div>
          <div style={{ display: "flex", gap: 8 }}>
            <button className="setup-btn secondary" onClick={onBack}>
              {t("setup.back")}
            </button>
            <button className="setup-btn primary" onClick={startLocal}>
              {t("setup.start")}
            </button>
          </div>
        </>
      )}

      {status === "checking" && (
        <div style={{ textAlign: "center", padding: 20 }}>
          <div className="setup-spinner" />
          <div style={{ fontSize: 13, color: "var(--desktop-text)", marginTop: 12 }}>
            {t("setup.checking")}
          </div>
        </div>
      )}

      {status === "upgrade_prompt" && preflight && (
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          <div style={{ fontSize: 13, fontWeight: 700, color: "var(--desktop-text)" }}>
            {t("setup.upgradeNeededTitle")}
          </div>
          <div style={{ fontSize: 12, color: "var(--desktop-text-dim)", lineHeight: 1.5 }}>
            {t("setup.upgradeNeededDesc", {
              n: (preflight.missing_tables || []).length,
              m: (preflight.missing_columns || []).length,
              size: formatBytes(preflight.db_size_bytes || 0),
            })}
          </div>
          {((preflight.missing_tables && preflight.missing_tables.length > 0) ||
            (preflight.missing_columns && preflight.missing_columns.length > 0)) && (
            <div
              style={{
                fontSize: 11,
                fontFamily: "monospace",
                background: "rgba(255,255,255,0.05)",
                border: "1px solid var(--desktop-border)",
                borderRadius: 8,
                padding: "8px 10px",
                maxHeight: 120,
                overflow: "auto",
                color: "var(--desktop-text-dim)",
                lineHeight: 1.4,
              }}
            >
              {(preflight.missing_tables || []).map((t) => (
                <div key={`t-${t}`}>+ table {t}</div>
              ))}
              {(preflight.missing_columns || []).map((c) => (
                <div key={`c-${c}`}>+ column {c}</div>
              ))}
            </div>
          )}
          <div style={{ display: "flex", flexDirection: "column", gap: 6, marginTop: 4 }}>
            <button className="setup-btn primary" onClick={() => upgradeWithBackup(true)}>
              {t("setup.upgradeWithBackup")}
            </button>
            <button className="setup-btn secondary" onClick={() => upgradeWithBackup(false)}>
              {t("setup.upgradeWithoutBackup")}
            </button>
            <button className="setup-btn secondary" onClick={() => { setStatus("idle"); setPreflight(null); }}>
              {t("setup.back")}
            </button>
          </div>
        </div>
      )}

      {status === "backing_up" && (
        <div style={{ textAlign: "center", padding: 20 }}>
          <div className="setup-spinner" />
          <div style={{ fontSize: 13, color: "var(--desktop-text)", marginTop: 12 }}>
            {t("setup.backingUp")}
          </div>
        </div>
      )}

      {status === "starting" && (
        <div style={{ textAlign: "center", padding: 20 }}>
          <div className="setup-spinner" />
          <div style={{ fontSize: 13, color: "var(--desktop-text)", marginTop: 12 }}>
            {t("setup.starting")}
          </div>
          {backupPath && (
            <div style={{ fontSize: 10, color: "var(--desktop-text-dim)", marginTop: 6, fontFamily: "monospace" }}>
              {t("setup.backupSavedAt")}: {backupPath}
            </div>
          )}
        </div>
      )}

      {status === "ready" && (
        <div style={{ textAlign: "center", padding: 20, color: "#4caf50", fontWeight: 700 }}>
          ✅ {t("setup.ready")}
        </div>
      )}

      {status === "error" && (
        <>
          <div className="desktop-login-error">{error}</div>
          <div style={{ display: "flex", gap: 8, marginTop: 8 }}>
            <button className="setup-btn secondary" onClick={onBack}>{t("setup.back")}</button>
            <button className="setup-btn primary" onClick={startLocal}>{t("setup.retry")}</button>
          </div>
        </>
      )}
    </div>
  );
}
