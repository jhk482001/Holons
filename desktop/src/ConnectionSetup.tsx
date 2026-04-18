import { useState } from "react";
import { useTranslation } from "react-i18next";
import { invoke } from "@tauri-apps/api/core";
import { setConnectionConfig, AppMode } from "./api-adapter";
import LangSwitcher from "./LangSwitcher";
import "./desktop.css";

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
          <div className="setup-title">{t("setup.title")}</div>
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


function PersonalSetup({
  onBack,
  onComplete,
}: {
  onBack: () => void;
  onComplete: () => void;
}) {
  const { t } = useTranslation();
  const [status, setStatus] = useState<"idle" | "starting" | "ready" | "error">("idle");
  const [error, setError] = useState("");

  async function startLocal() {
    setStatus("starting");
    setError("");
    try {
      // Ask Tauri to start the sidecar backend
      const port = await invoke<number>("start_sidecar");
      const localUrl = `http://localhost:${port}`;

      // Wait for health check
      for (let i = 0; i < 30; i++) {
        try {
          const r = await fetch(`${localUrl}/api/health`, {
            signal: AbortSignal.timeout(2_000),
          });
          if (r.ok) {
            await setConnectionConfig("personal", localUrl);
            setStatus("ready");
            // Small delay so user sees "Ready"
            setTimeout(onComplete, 500);
            return;
          }
        } catch {
          // Not ready yet
        }
        await new Promise((r) => setTimeout(r, 1_000));
      }
      throw new Error(t("setup.errStartTimeout"));
    } catch (e: any) {
      setStatus("error");
      setError(e.message || t("setup.errStartFailed"));
    }
  }

  return (
    <div className="setup-card">
      <LangSwitcher />
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

      {status === "starting" && (
        <div style={{ textAlign: "center", padding: 20 }}>
          <div className="setup-spinner" />
          <div style={{ fontSize: 13, color: "var(--desktop-text)", marginTop: 12 }}>
            {t("setup.starting")}
          </div>
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
