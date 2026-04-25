import { useState } from "react";
import { useTranslation } from "react-i18next";
import LangSwitcher from "./LangSwitcher";

export default function DesktopLogin({
  onLogin,
}: {
  onLogin: (token: string) => void;
}) {
  const { t } = useTranslation();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      console.log("[login] POST /api/login/desktop start");
      const resp = await fetch("/api/login/desktop", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username, password }),
      });
      console.log("[login] response", resp.status, resp.headers.get("content-type"));
      const ct = resp.headers.get("content-type") || "";
      if (!ct.includes("application/json")) {
        const txt = await resp.text();
        throw new Error(
          t("login.unexpectedResponse", {
            status: resp.status,
            ct: ct || "no content-type",
            body: txt.slice(0, 120),
          }),
        );
      }
      if (!resp.ok) {
        const data = await resp.json().catch(() => ({}));
        throw new Error(data.error || `${t("login.genericError")} (${resp.status})`);
      }
      const data = await resp.json();
      if (!data.token) {
        throw new Error(t("login.noToken"));
      }
      console.log("[login] ok, token len=", (data.token || "").length);
      onLogin(data.token);
    } catch (err: any) {
      console.error("[login] failed:", err);
      setError(err?.message || String(err) || t("login.genericError"));
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="desktop-login-overlay" data-interactive>
      <form
        className="desktop-login-card"
        onSubmit={handleSubmit}
        onKeyDown={(e) => {
          // Eat Enter while user is in IME composition (zhuyin / pinyin
          // / kana etc.) so picking a candidate doesn't accidentally
          // submit the login form. Native browser submit fires here too
          // — without this guard each candidate-Enter logs in
          // prematurely with whatever's typed so far.
          if (e.key === "Enter" && (e.nativeEvent as any).isComposing) {
            e.preventDefault();
            e.stopPropagation();
          }
        }}
      >
        <LangSwitcher />
        <div className="desktop-login-title">{t("login.title")}</div>
        <div className="desktop-login-sub">{t("login.subtitle")}</div>
        <input
          type="text"
          placeholder={t("login.username")}
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          autoFocus
        />
        <input
          type="password"
          placeholder={t("login.password")}
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />
        {error && <div className="desktop-login-error">{error}</div>}
        <button type="submit" disabled={loading || !username || !password}>
          {loading ? t("login.submitting") : t("login.submit")}
        </button>
      </form>
    </div>
  );
}
