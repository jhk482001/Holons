import { useTranslation } from "react-i18next";
import { useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  NotificationsAPI,
  LeadProxyAPI,
  AuditAPI,
  ProxyResponseRow,
} from "../api/client";
import Runs from "./Runs";
import Escalations from "./Escalations";
import "./Records.css";

type TabKey = "runs" | "escalations" | "notifications" | "proxy" | "audit";

const TABS: { key: TabKey; label: string }[] = [
  { key: "runs", label: "records.tab.runs" },
  { key: "escalations", label: "records.tab.escalations" },
  { key: "notifications", label: "records.tab.notifications" },
  { key: "proxy", label: "records.tab.proxy" },
  { key: "audit", label: "records.tab.audit" },
];

export default function Records() {
  const { t } = useTranslation();
  const [params, setParams] = useSearchParams();
  const raw = params.get("tab") as TabKey | null;
  const tab: TabKey = (raw && TABS.some((tb) => tb.key === raw)) ? raw : "runs";

  function setTab(k: TabKey) {
    const next = new URLSearchParams(params);
    next.set("tab", k);
    setParams(next, { replace: true });
  }

  return (
    <div className="page records-page">
      <h1>{t("records.title")}</h1>
      <div className="subtitle">{t("records.subtitle")}</div>

      <nav className="page-tabs" data-testid="records-tabs">
        {TABS.map((tb) => (
          <button
            key={tb.key}
            className={`page-tab ${tab === tb.key ? "active" : ""}`}
            data-testid={`records-tab-${tb.key}`}
            onClick={() => setTab(tb.key)}
          >
            {t(tb.label)}
          </button>
        ))}
      </nav>

      <div className="records-tab-body">
        {tab === "runs" && <Runs />}
        {tab === "escalations" && <Escalations />}
        {tab === "notifications" && <NotificationsTab />}
        {tab === "proxy" && <ProxyResponsesTab />}
        {tab === "audit" && <AuditLogTab />}
      </div>
    </div>
  );
}

function NotificationsTab() {
  const { t } = useTranslation();
  const { data: notifs = [] } = useQuery({
    queryKey: ["notifications"],
    queryFn: () => NotificationsAPI.list(),
    refetchInterval: 15_000,
  });

  return (
    <div className="page">
      <h1>{t("notifications.title")}</h1>
      <div className="subtitle">{t("notifications.count", { n: notifs.length })}</div>
      {notifs.length === 0 ? (
        <div
          style={{
            background: "var(--surface)",
            border: "1px solid var(--border)",
            borderRadius: 16,
            padding: "40px 20px",
            textAlign: "center",
            color: "var(--ink-4)",
            fontSize: 13,
          }}
          data-testid="notifications-empty"
        >
          {t("notifications.empty")}
        </div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          {notifs.map((n) => (
            <div
              key={n.id}
              data-testid={`notifications-row-${n.id}`}
              style={{
                background: "var(--surface)",
                border: "1px solid var(--border)",
                borderLeft: "3px solid " + (
                  n.severity === "critical" || n.severity === "error" ? "var(--danger)" :
                    n.severity === "warn" ? "var(--warn, #c98930)" :
                      "var(--ink-3)"
                ),
                borderRadius: 10,
                padding: "12px 16px",
                display: "flex",
                alignItems: "center",
                gap: 12,
              }}
            >
              <div style={{ flex: 1 }}>
                <div style={{ fontSize: 13, fontWeight: 700 }}>{n.title}</div>
                {n.body && (
                  <div style={{ fontSize: 11, color: "var(--ink-3)", marginTop: 2 }}>{n.body}</div>
                )}
                <div style={{ fontSize: 10, color: "var(--ink-4)", marginTop: 4 }}>
                  {new Date(n.created_at).toLocaleString("zh-TW")}
                </div>
              </div>
              <div
                style={{
                  fontSize: 9,
                  fontWeight: 800,
                  padding: "3px 8px",
                  background: n.status === "unread" ? "var(--accent-soft)" : "var(--surface-2)",
                  color: n.status === "unread" ? "var(--accent)" : "var(--ink-3)",
                  borderRadius: 6,
                  textTransform: "uppercase",
                  letterSpacing: 0.5,
                }}
              >
                {n.status}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}


function ProxyResponsesTab() {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const { data: rows = [], isLoading } = useQuery({
    queryKey: ["lead-proxy-responses"],
    queryFn: LeadProxyAPI.list,
    refetchInterval: 30_000,
  });
  const retract = useMutation({
    mutationFn: (id: number) => LeadProxyAPI.retract(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["lead-proxy-responses"] }),
  });

  return (
    <div className="page">
      <h1>{t("proxy.title")}</h1>
      <div className="subtitle">{t("proxy.subtitle", { count: rows.length })}</div>
      <div
        style={{
          fontSize: 11,
          color: "var(--ink-3)",
          padding: "10px 14px",
          background: "var(--accent-soft)",
          borderRadius: 10,
          marginBottom: 14,
        }}
      >
        {t("proxy.helpText")}
      </div>
      {isLoading ? (
        <div style={{ padding: 30, textAlign: "center", color: "var(--ink-4)" }}>
          {t("btn.loading")}
        </div>
      ) : rows.length === 0 ? (
        <div
          data-testid="proxy-empty"
          style={{
            background: "var(--surface)",
            border: "1px solid var(--border)",
            borderRadius: 14,
            padding: "50px 20px",
            textAlign: "center",
            color: "var(--ink-4)",
            fontSize: 13,
          }}
        >
          {t("proxy.empty")}
        </div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          {rows.map((r) => (
            <ProxyRow
              key={r.id}
              row={r}
              onRetract={() => retract.mutate(r.id)}
              retracting={retract.isPending}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function ProxyRow({
  row,
  onRetract,
  retracting,
}: {
  row: ProxyResponseRow;
  onRetract: () => void;
  retracting: boolean;
}) {
  const { t } = useTranslation();
  const meta = row.metadata || {};
  const reason = (meta.reason as string) || "";
  const isWithdrawn = row.cancelled;
  return (
    <div
      data-testid={`proxy-row-${row.id}`}
      style={{
        background: isWithdrawn ? "var(--surface-2)" : "#fff4e6",
        border: `1px solid ${isWithdrawn ? "var(--border)" : "#f5c592"}`,
        borderLeft: `4px solid ${isWithdrawn ? "var(--border-strong)" : "#e38f4a"}`,
        borderRadius: 12,
        padding: "14px 18px",
        opacity: isWithdrawn ? 0.6 : 1,
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          gap: 12,
          marginBottom: 8,
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <span
            style={{
              fontSize: 10,
              fontWeight: 800,
              padding: "3px 8px",
              background: isWithdrawn ? "var(--surface-3)" : "#e38f4a",
              color: isWithdrawn ? "var(--ink-3)" : "white",
              borderRadius: 6,
              letterSpacing: 0.5,
            }}
          >
            {isWithdrawn ? t("proxy.withdrawn") : t("proxy.leadProxy")}
          </span>
          {reason && (
            <span style={{ fontSize: 11, color: "var(--ink-3)" }}>
              {t("proxy.reason")}{reason}
            </span>
          )}
        </div>
        {!isWithdrawn && (
          <button
            className="mbtn"
            onClick={onRetract}
            disabled={retracting}
            data-testid={`proxy-retract-${row.id}`}
            style={{ fontSize: 10 }}
          >
            {retracting ? t("proxy.retracting") : t("proxy.retract")}
          </button>
        )}
      </div>
      <div style={{ fontSize: 13, color: "var(--ink-2)", lineHeight: 1.6 }}>
        {row.content}
      </div>
      <div
        style={{
          fontSize: 10,
          color: "var(--ink-4)",
          marginTop: 8,
          display: "flex",
          gap: 14,
        }}
      >
        <span>{t("records.threadLabel")} {row.thread_id.slice(0, 8)}…</span>
        <span>{new Date(row.created_at).toLocaleString()}</span>
      </div>
    </div>
  );
}


function AuditLogTab() {
  const { t } = useTranslation();
  const { data, isLoading } = useQuery({
    queryKey: ["audit-log"],
    queryFn: () => AuditAPI.list({ limit: 100 }),
  });

  return (
    <div className="page">
      <h1>{t("audit.title")}</h1>
      <div className="subtitle">
        {t("audit.subtitle", { count: data?.entries.length ?? 0 })}
      </div>
      {isLoading ? (
        <div style={{ padding: 30, textAlign: "center", color: "var(--ink-4)" }}>
          {t("btn.loading")}
        </div>
      ) : !data || data.entries.length === 0 ? (
        <div
          data-testid="audit-empty"
          style={{
            background: "var(--surface)",
            border: "1px solid var(--border)",
            borderRadius: 14,
            padding: "50px 20px",
            textAlign: "center",
            color: "var(--ink-4)",
            fontSize: 13,
          }}
        >
          {t("audit.empty")}
        </div>
      ) : (
        <div
          style={{
            background: "var(--surface)",
            border: "1px solid var(--border)",
            borderRadius: 14,
            overflow: "hidden",
          }}
          data-testid="audit-table"
        >
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
            <thead>
              <tr style={{ background: "var(--surface-2)", textAlign: "left" }}>
                <th style={{ padding: "10px 14px", fontWeight: 700 }}>{t("audit.time")}</th>
                <th style={{ padding: "10px 14px", fontWeight: 700 }}>{t("audit.user")}</th>
                <th style={{ padding: "10px 14px", fontWeight: 700 }}>Method</th>
                <th style={{ padding: "10px 14px", fontWeight: 700 }}>Path</th>
                <th style={{ padding: "10px 14px", fontWeight: 700, textAlign: "right" }}>
                  Status
                </th>
              </tr>
            </thead>
            <tbody>
              {data.entries.map((e) => (
                <tr
                  key={e.id}
                  data-testid={`audit-row-${e.id}`}
                  style={{ borderTop: "1px solid var(--border)" }}
                >
                  <td style={{ padding: "8px 14px", color: "var(--ink-3)" }}>
                    {new Date(e.created_at).toLocaleString("zh-TW")}
                  </td>
                  <td style={{ padding: "8px 14px" }}>{e.username || "—"}</td>
                  <td style={{ padding: "8px 14px" }}>
                    <span
                      style={{
                        fontSize: 10,
                        fontWeight: 800,
                        padding: "2px 8px",
                        background: methodColor(e.method).bg,
                        color: methodColor(e.method).fg,
                        borderRadius: 6,
                      }}
                    >
                      {e.method}
                    </span>
                  </td>
                  <td
                    style={{
                      padding: "8px 14px",
                      fontFamily: "ui-monospace, SFMono-Regular, monospace",
                      color: "var(--ink-2)",
                    }}
                  >
                    {e.path}
                  </td>
                  <td
                    style={{
                      padding: "8px 14px",
                      textAlign: "right",
                      color:
                        e.status_code && e.status_code >= 400
                          ? "var(--danger)"
                          : "var(--good)",
                      fontWeight: 700,
                    }}
                  >
                    {e.status_code || "—"}
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

function methodColor(m: string): { bg: string; fg: string } {
  return (
    {
      POST: { bg: "var(--good-soft)", fg: "var(--good)" },
      PUT: { bg: "var(--warn-soft, #fdf0d4)", fg: "var(--warn, #c98930)" },
      PATCH: { bg: "var(--warn-soft, #fdf0d4)", fg: "var(--warn, #c98930)" },
      DELETE: { bg: "var(--danger-soft)", fg: "var(--danger)" },
    }[m] || { bg: "var(--surface-2)", fg: "var(--ink-3)" }
  );
}
