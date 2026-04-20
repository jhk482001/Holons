import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { api, ApiError } from "../../api/client";

/**
 * IM channel bindings — one row per (user, platform). Today: Telegram
 * only. Adding another platform is a matter of teaching the backend
 * router / adapter, plus listing it here.
 */

interface ImBinding {
  id: number;
  platform: string;
  external_id: string | null;
  display_name: string | null;
  enabled: boolean;
  metadata: Record<string, unknown>;
  created_at: string;
}

export default function ChannelsTab() {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const { data: bindings = [], isLoading } = useQuery({
    queryKey: ["im-bindings"],
    queryFn: () => api.get<ImBinding[]>("/im/bindings"),
  });

  const [showTg, setShowTg] = useState(false);
  const [token, setToken] = useState("");
  const [err, setErr] = useState("");

  const save = useMutation({
    mutationFn: () =>
      api.post<{ id: number; display_name: string }>("/im/bindings", {
        platform: "telegram", token,
      }),
    onSuccess: () => {
      setShowTg(false); setToken(""); setErr("");
      qc.invalidateQueries({ queryKey: ["im-bindings"] });
    },
    onError: (e) => setErr(e instanceof ApiError ? e.message : String(e)),
  });

  const toggle = useMutation({
    mutationFn: (id: number) => api.post<{ enabled: boolean }>(`/im/bindings/${id}/toggle`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["im-bindings"] }),
  });

  const del = useMutation({
    mutationFn: (id: number) => api.del(`/im/bindings/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["im-bindings"] }),
  });

  const tgBinding = bindings.find((b) => b.platform === "telegram");

  return (
    <div style={{ maxWidth: 720 }}>
      <h2 style={{ fontSize: 16, marginTop: 0 }}>{t("channels.title")}</h2>
      <p style={{ color: "var(--ink-3)", fontSize: 13, marginTop: 4 }}>
        {t("channels.subtitle")}
      </p>

      {/* -------- Telegram -------- */}
      <div style={{
        marginTop: 20, padding: 16, border: "1px solid var(--border)",
        borderRadius: 10, background: "var(--surface)",
      }}>
        <div style={{ display: "flex", alignItems: "flex-start", gap: 14 }}>
          <div style={{ fontSize: 22 }}>✈️</div>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontWeight: 700, fontSize: 14 }}>Telegram</div>
            <div style={{ color: "var(--ink-3)", fontSize: 12, marginTop: 4 }}>
              {t("channels.telegramDesc")}
            </div>
            {tgBinding ? (
              <div style={{ marginTop: 10, fontSize: 13 }}>
                <div><strong>{tgBinding.display_name}</strong></div>
                <div style={{ color: "var(--ink-3)", fontSize: 12, marginTop: 4 }}>
                  {tgBinding.external_id
                    ? t("channels.bound", { id: tgBinding.external_id })
                    : t("channels.awaitStart")}
                </div>
                <div style={{ color: "var(--ink-4)", fontSize: 11, marginTop: 4 }}>
                  {t("channels.status")}:{" "}
                  <span style={{ color: tgBinding.enabled ? "var(--good)" : "var(--ink-4)" }}>
                    {tgBinding.enabled ? t("channels.on") : t("channels.off")}
                  </span>
                </div>
                <div style={{ display: "flex", gap: 8, marginTop: 10 }}>
                  <button className="mbtn" style={{ fontSize: 12 }}
                    onClick={() => toggle.mutate(tgBinding.id)} disabled={toggle.isPending}>
                    {tgBinding.enabled ? t("channels.disable") : t("channels.enable")}
                  </button>
                  <button className="mbtn" style={{ fontSize: 12 }}
                    onClick={() => { setToken(""); setErr(""); setShowTg(true); }}>
                    {t("channels.replaceToken")}
                  </button>
                  <button className="mbtn" style={{ fontSize: 12, color: "var(--danger)" }}
                    onClick={() => { if (confirm(t("channels.confirmUnbind"))) del.mutate(tgBinding.id); }}
                    disabled={del.isPending}>
                    {t("channels.unbind")}
                  </button>
                </div>
              </div>
            ) : !showTg ? (
              <button className="mbtn primary" style={{ marginTop: 10, fontSize: 12 }}
                onClick={() => { setShowTg(true); setErr(""); }}>
                {t("channels.addTelegram")}
              </button>
            ) : null}
          </div>
        </div>

        {showTg && (
          <div style={{
            marginTop: 14, paddingTop: 14, borderTop: "1px solid var(--border)",
            fontSize: 13,
          }}>
            <ol style={{ margin: "0 0 10px 18px", color: "var(--ink-2)", lineHeight: 1.6 }}>
              <li>{t("channels.instrStep1")}</li>
              <li>{t("channels.instrStep2")}</li>
              <li>{t("channels.instrStep3")}</li>
            </ol>
            <label style={{ fontSize: 12, fontWeight: 600 }}>{t("channels.botTokenLabel")}</label>
            <input
              type="password"
              value={token}
              onChange={(e) => setToken(e.target.value)}
              placeholder="1234567890:AA..."
              style={{ width: "100%", padding: 8, marginTop: 6, fontSize: 13,
                      border: "1px solid var(--border)", borderRadius: 6, fontFamily: "monospace" }}
            />
            {err && <div style={{ color: "var(--danger)", fontSize: 12, marginTop: 8 }}>{err}</div>}
            <div style={{ display: "flex", gap: 8, marginTop: 12 }}>
              <button className="mbtn" onClick={() => { setShowTg(false); setToken(""); setErr(""); }}>
                {t("btn.cancel")}
              </button>
              <button className="mbtn primary" onClick={() => save.mutate()}
                disabled={!token.trim() || save.isPending}>
                {save.isPending ? t("btn.saving") : t("channels.saveAndVerify")}
              </button>
            </div>
          </div>
        )}
      </div>

      {isLoading && <div style={{ color: "var(--ink-4)", fontSize: 12, marginTop: 12 }}>
        {t("btn.loading")}
      </div>}
    </div>
  );
}
