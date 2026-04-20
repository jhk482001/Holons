import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { api, ApiError } from "../../api/client";

/**
 * IM channel bindings. One row per (user, platform). Each platform
 * card is the same shape — only the token instructions differ — so
 * adding another channel is a matter of adding a PLATFORMS entry plus
 * the backend adapter.
 */

interface ImBinding {
  id: number;
  platform: string;
  external_id: string | null;
  display_name: string | null;
  enabled: boolean;
  transport: string;
  metadata: Record<string, unknown>;
  created_at: string;
}

type PlatformKey = "telegram" | "slack" | "line";

interface PlatformSpec {
  key: PlatformKey;
  label: string;
  icon: string;
  supportsPolling: boolean;
  // i18n keys for the three setup steps
  step1Key: string;
  step2Key: string;
  step3Key: string;
  tokenPlaceholder: string;
  descKey: string;
}

const PLATFORMS: PlatformSpec[] = [
  {
    key: "telegram", label: "Telegram", icon: "✈️", supportsPolling: true,
    step1Key: "channels.telegram.step1", step2Key: "channels.telegram.step2",
    step3Key: "channels.telegram.step3",
    tokenPlaceholder: "1234567890:AA...",
    descKey: "channels.telegram.desc",
  },
  {
    key: "slack", label: "Slack", icon: "#", supportsPolling: false,
    step1Key: "channels.slack.step1", step2Key: "channels.slack.step2",
    step3Key: "channels.slack.step3",
    tokenPlaceholder: "xoxb-...",
    descKey: "channels.slack.desc",
  },
  {
    key: "line", label: "LINE", icon: "💬", supportsPolling: false,
    step1Key: "channels.line.step1", step2Key: "channels.line.step2",
    step3Key: "channels.line.step3",
    tokenPlaceholder: "<channel access token>",
    descKey: "channels.line.desc",
  },
];


export default function ChannelsTab() {
  const { t } = useTranslation();
  const { data: bindings = [], isLoading } = useQuery({
    queryKey: ["im-bindings"],
    queryFn: () => api.get<ImBinding[]>("/im/bindings"),
  });

  return (
    <div style={{ maxWidth: 760 }}>
      <h2 style={{ fontSize: 16, marginTop: 0 }}>{t("channels.title")}</h2>
      <p style={{ color: "var(--ink-3)", fontSize: 13, marginTop: 4 }}>
        {t("channels.subtitle")}
      </p>
      {PLATFORMS.map((p) => (
        <PlatformCard key={p.key} spec={p}
          binding={bindings.find((b) => b.platform === p.key)} />
      ))}
      {isLoading && <div style={{ color: "var(--ink-4)", fontSize: 12, marginTop: 12 }}>
        {t("btn.loading")}
      </div>}
    </div>
  );
}


function PlatformCard({ spec, binding }: {
  spec: PlatformSpec;
  binding: ImBinding | undefined;
}) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const [showSetup, setShowSetup] = useState(false);
  const [token, setToken] = useState("");
  const [err, setErr] = useState("");
  const [showWebhook, setShowWebhook] = useState(false);
  const [publicUrl, setPublicUrl] = useState("");

  const save = useMutation({
    mutationFn: () =>
      api.post<{ id: number; display_name: string; transport: string }>(
        "/im/bindings", { platform: spec.key, token }),
    onSuccess: () => {
      setShowSetup(false); setToken(""); setErr("");
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
  const switchTransport = useMutation({
    mutationFn: (body: { transport: string; public_url?: string }) =>
      api.post<{ transport: string; webhook_url: string | null }>(
        `/im/bindings/${binding!.id}/transport`, body),
    onSuccess: () => {
      setShowWebhook(false); setPublicUrl("");
      qc.invalidateQueries({ queryKey: ["im-bindings"] });
    },
    onError: (e) => setErr(e instanceof ApiError ? e.message : String(e)),
  });

  const webhookUrl = (binding?.metadata?.webhook_public_url as string) || "";
  const webhookSecret = (binding?.metadata?.webhook_secret as string) || "";
  const fullWebhookPath = webhookUrl
    ? `${webhookUrl.replace(/\/$/, "")}/api/im/webhook/${spec.key}/${webhookSecret}`
    : "";

  return (
    <div style={{
      marginTop: 20, padding: 16, border: "1px solid var(--border)",
      borderRadius: 10, background: "var(--surface)",
    }}>
      <div style={{ display: "flex", alignItems: "flex-start", gap: 14 }}>
        <div style={{ fontSize: 22 }}>{spec.icon}</div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontWeight: 700, fontSize: 14 }}>{spec.label}</div>
          <div style={{ color: "var(--ink-3)", fontSize: 12, marginTop: 4 }}>
            {t(spec.descKey)}
          </div>

          {binding ? (
            <div style={{ marginTop: 10, fontSize: 13 }}>
              <div><strong>{binding.display_name}</strong></div>
              <div style={{ color: "var(--ink-3)", fontSize: 12, marginTop: 4 }}>
                {binding.external_id
                  ? t("channels.bound", { id: binding.external_id })
                  : (binding.transport === "webhook"
                      ? t("channels.webhookSetPrompt")
                      : t("channels.awaitStart"))}
              </div>
              <div style={{ color: "var(--ink-4)", fontSize: 11, marginTop: 4 }}>
                {t("channels.transport")}:{" "}
                <strong>{binding.transport}</strong>{" · "}
                {t("channels.status")}:{" "}
                <span style={{ color: binding.enabled ? "var(--good)" : "var(--ink-4)" }}>
                  {binding.enabled ? t("channels.on") : t("channels.off")}
                </span>
              </div>
              {binding.transport === "webhook" && fullWebhookPath && (
                <div style={{
                  marginTop: 6, padding: "6px 10px", background: "var(--surface-2)",
                  borderRadius: 4, fontSize: 11, fontFamily: "monospace",
                  color: "var(--ink-2)", wordBreak: "break-all",
                }}>
                  {fullWebhookPath}
                </div>
              )}
              <div style={{ display: "flex", gap: 8, marginTop: 10, flexWrap: "wrap" }}>
                <button className="mbtn" style={{ fontSize: 12 }}
                  onClick={() => toggle.mutate(binding.id)} disabled={toggle.isPending}>
                  {binding.enabled ? t("channels.disable") : t("channels.enable")}
                </button>
                {spec.supportsPolling && (
                  <button className="mbtn" style={{ fontSize: 12 }}
                    onClick={() => {
                      if (binding.transport === "polling") {
                        setShowWebhook(true); setErr("");
                      } else {
                        switchTransport.mutate({ transport: "polling" });
                      }
                    }}>
                    {binding.transport === "polling"
                      ? t("channels.switchToWebhook")
                      : t("channels.switchToPolling")}
                  </button>
                )}
                {!spec.supportsPolling && binding.transport === "webhook" && (
                  <button className="mbtn" style={{ fontSize: 12 }}
                    onClick={() => { setShowWebhook(true); setErr(""); }}>
                    {t("channels.updateWebhookUrl")}
                  </button>
                )}
                <button className="mbtn" style={{ fontSize: 12 }}
                  onClick={() => { setToken(""); setErr(""); setShowSetup(true); }}>
                  {t("channels.replaceToken")}
                </button>
                <button className="mbtn" style={{ fontSize: 12, color: "var(--danger)" }}
                  onClick={() => { if (confirm(t("channels.confirmUnbind"))) del.mutate(binding.id); }}
                  disabled={del.isPending}>
                  {t("channels.unbind")}
                </button>
              </div>
            </div>
          ) : !showSetup ? (
            <button className="mbtn primary" style={{ marginTop: 10, fontSize: 12 }}
              onClick={() => { setShowSetup(true); setErr(""); }}>
              {t("channels.connect", { platform: spec.label })}
            </button>
          ) : null}
        </div>
      </div>

      {showSetup && (
        <div style={{
          marginTop: 14, paddingTop: 14, borderTop: "1px solid var(--border)",
          fontSize: 13,
        }}>
          <ol style={{ margin: "0 0 10px 18px", color: "var(--ink-2)", lineHeight: 1.6 }}>
            <li>{t(spec.step1Key)}</li>
            <li>{t(spec.step2Key)}</li>
            <li>{t(spec.step3Key)}</li>
          </ol>
          <label style={{ fontSize: 12, fontWeight: 600 }}>{t("channels.tokenLabel")}</label>
          <input
            type="password"
            value={token}
            onChange={(e) => setToken(e.target.value)}
            placeholder={spec.tokenPlaceholder}
            style={{ width: "100%", padding: 8, marginTop: 6, fontSize: 13,
                    border: "1px solid var(--border)", borderRadius: 6, fontFamily: "monospace" }}
          />
          {err && <div style={{ color: "var(--danger)", fontSize: 12, marginTop: 8 }}>{err}</div>}
          <div style={{ display: "flex", gap: 8, marginTop: 12 }}>
            <button className="mbtn" onClick={() => { setShowSetup(false); setToken(""); setErr(""); }}>
              {t("btn.cancel")}
            </button>
            <button className="mbtn primary" onClick={() => save.mutate()}
              disabled={!token.trim() || save.isPending}>
              {save.isPending ? t("btn.saving") : t("channels.saveAndVerify")}
            </button>
          </div>
        </div>
      )}

      {showWebhook && binding && (
        <div style={{
          marginTop: 14, paddingTop: 14, borderTop: "1px solid var(--border)",
          fontSize: 13,
        }}>
          <div style={{ color: "var(--ink-2)", marginBottom: 8 }}>
            {t("channels.webhookSetupHint")}
          </div>
          <label style={{ fontSize: 12, fontWeight: 600 }}>{t("channels.publicUrlLabel")}</label>
          <input
            value={publicUrl}
            onChange={(e) => setPublicUrl(e.target.value)}
            placeholder="https://your-holons.example.com"
            style={{ width: "100%", padding: 8, marginTop: 6, fontSize: 13,
                    border: "1px solid var(--border)", borderRadius: 6, fontFamily: "monospace" }}
          />
          {err && <div style={{ color: "var(--danger)", fontSize: 12, marginTop: 8 }}>{err}</div>}
          <div style={{ display: "flex", gap: 8, marginTop: 12 }}>
            <button className="mbtn" onClick={() => { setShowWebhook(false); setPublicUrl(""); setErr(""); }}>
              {t("btn.cancel")}
            </button>
            <button className="mbtn primary" onClick={() =>
              switchTransport.mutate({ transport: "webhook", public_url: publicUrl.trim() })}
              disabled={!publicUrl.trim().startsWith("https://") || switchTransport.isPending}>
              {switchTransport.isPending ? t("btn.saving") : t("channels.saveWebhook")}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
