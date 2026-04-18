import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { AuthAPI, ApiError } from "../api/client";

export default function Login() {
  const { t } = useTranslation();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const qc = useQueryClient();

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setErr(null);
    try {
      await AuthAPI.login(username, password);
      qc.invalidateQueries({ queryKey: ["me"] });
      window.location.href = "/";
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : t("login.submit"));
    }
  }

  return (
    <div style={{
      minHeight: "100vh",
      display: "flex",
      alignItems: "center",
      justifyContent: "center",
      padding: 40,
    }}>
      <form onSubmit={submit} style={{
        background: "var(--surface)",
        border: "1px solid var(--border)",
        borderRadius: "var(--radius-lg)",
        padding: "36px 40px",
        width: 380,
        boxShadow: "var(--shadow-md)",
      }}>
        <h1 style={{ fontSize: 24, marginBottom: 4, fontWeight: 800 }}>
          <span style={{
            display: "inline-block",
            width: 10, height: 10, borderRadius: "50%",
            background: "var(--accent)", marginRight: 10,
          }}></span>
          {t("login.title")}
        </h1>
        <div style={{ fontSize: 12, color: "var(--ink-3)", marginBottom: 28 }}>
          {t("login.subtitle")}
        </div>

        <label style={{ display: "block", fontSize: 11, fontWeight: 700, color: "var(--ink-3)", textTransform: "uppercase", letterSpacing: 1, marginBottom: 6 }}>
          {t("login.username")}
        </label>
        <input
          type="text"
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          style={{
            width: "100%", padding: "10px 14px",
            border: "1px solid var(--border)", borderRadius: 10,
            background: "var(--surface)", color: "var(--ink)",
            marginBottom: 18,
          }}
        />

        <label style={{ display: "block", fontSize: 11, fontWeight: 700, color: "var(--ink-3)", textTransform: "uppercase", letterSpacing: 1, marginBottom: 6 }}>
          {t("login.password")}
        </label>
        <input
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          style={{
            width: "100%", padding: "10px 14px",
            border: "1px solid var(--border)", borderRadius: 10,
            background: "var(--surface)", color: "var(--ink)",
            marginBottom: 18,
          }}
        />

        {err && (
          <div style={{
            padding: "9px 12px",
            background: "var(--danger-soft)",
            color: "var(--danger)",
            borderRadius: 8,
            fontSize: 12,
            fontWeight: 600,
            marginBottom: 14,
          }}>{err}</div>
        )}

        <button type="submit" style={{
          width: "100%",
          padding: "11px 16px",
          background: "var(--accent)",
          color: "white",
          border: "none",
          borderRadius: 12,
          fontWeight: 700,
          fontSize: 14,
        }}>
          {t("login.submit")}
        </button>

        <div style={{ fontSize: 10, color: "var(--ink-4)", marginTop: 18, textAlign: "center" }}>
          {t("login.demo")}
        </div>
      </form>
    </div>
  );
}
