import { useTranslation } from "react-i18next";
import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";

interface Escalation {
  id: number;
  task_id: number;
  run_id: number;
  raising_agent_id: number;
  agent_name: string;
  task_owner_id: number;
  uncertainty: string;
  context: any;
  route: string | null;
  consulted_agent_id: number | null;
  status: string;
  resolution: string | null;
  created_at: string;
  resolved_at: string | null;
}

export default function Escalations() {
  const { t } = useTranslation();
  const { data: items = [] } = useQuery({
    queryKey: ["escalations"],
    queryFn: () => api.get<Escalation[]>("/escalations"),
    refetchInterval: 10_000,
  });
  const qc = useQueryClient();

  const [resolvingId, setResolvingId] = useState<number | null>(null);
  const [resolution, setResolution] = useState("");

  const resolveMutation = useMutation({
    mutationFn: (id: number) => api.post(`/escalations/${id}/resolve`, { resolution }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["escalations"] });
      setResolvingId(null);
      setResolution("");
    },
  });

  const pending = items.filter((e) => e.status === "pending");
  const resolved = items.filter((e) => e.status !== "pending");

  return (
    <div className="page">
      <h1>{t("escalation.title")}</h1>
      <div className="subtitle">{t("escalation.subtitle")}</div>

      {pending.length === 0 ? (
        <div style={{
          padding: 60,
          textAlign: "center",
          color: "var(--ink-4)",
          background: "var(--surface)",
          border: "1px solid var(--border)",
          borderRadius: 16,
          marginBottom: 30,
        }}>
          {t("escalation.empty")}
        </div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 12, marginBottom: 30 }}>
          <h3 style={{ fontSize: 13, color: "var(--ink-3)", textTransform: "uppercase", letterSpacing: 1, fontWeight: 800 }}>
            {t("escalation.pending", { count: pending.length })}
          </h3>
          {pending.map((e) => (
            <div key={e.id} style={{
              background: "var(--warn-soft)",
              border: "1.5px solid #ecd9a8",
              borderRadius: 14,
              padding: "16px 20px",
            }}>
              <div style={{ display: "flex", alignItems: "baseline", gap: 10, marginBottom: 8 }}>
                <strong style={{ fontSize: 13 }}>{e.agent_name || `Agent #${e.raising_agent_id}`}</strong>
                <span style={{ fontSize: 10, color: "var(--ink-3)" }}>
                  Run #{e.run_id} · {new Date(e.created_at).toLocaleString("zh-TW")}
                </span>
                {e.route && (
                  <span style={{
                    fontSize: 9,
                    padding: "2px 8px",
                    background: "var(--accent-soft)",
                    color: "var(--accent)",
                    borderRadius: 999,
                    fontWeight: 800,
                    textTransform: "uppercase",
                  }}>
                    {e.route}
                  </span>
                )}
              </div>
              <div style={{ fontSize: 13, color: "var(--ink)", marginBottom: 12, lineHeight: 1.6 }}>
                {e.uncertainty}
              </div>

              {resolvingId === e.id ? (
                <div>
                  <textarea
                    value={resolution}
                    onChange={(ev) => setResolution(ev.target.value)}
                    placeholder={t("escalation.tellAgent")}
                    style={{
                      width: "100%",
                      minHeight: 80,
                      padding: 10,
                      border: "1px solid var(--border)",
                      borderRadius: 8,
                      fontSize: 13,
                      fontFamily: "inherit",
                      marginBottom: 10,
                    }}
                  />
                  <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
                    <button
                      onClick={() => setResolvingId(null)}
                      style={{
                        padding: "6px 14px",
                        background: "white",
                        border: "1px solid var(--border)",
                        borderRadius: 8,
                        fontSize: 12,
                        fontWeight: 700,
                      }}
                    >{t("btn.cancel")}</button>
                    <button
                      onClick={() => resolveMutation.mutate(e.id)}
                      disabled={!resolution.trim()}
                      style={{
                        padding: "6px 14px",
                        background: "var(--accent)",
                        color: "white",
                        border: "none",
                        borderRadius: 8,
                        fontSize: 12,
                        fontWeight: 700,
                      }}
                    >{t("escalation.submitResolve")}</button>
                  </div>
                </div>
              ) : (
                <button
                  onClick={() => setResolvingId(e.id)}
                  style={{
                    padding: "6px 14px",
                    background: "var(--accent)",
                    color: "white",
                    border: "none",
                    borderRadius: 8,
                    fontSize: 12,
                    fontWeight: 700,
                  }}
                >
                  {t("escalation.replyResolve")}
                </button>
              )}
            </div>
          ))}
        </div>
      )}

      {resolved.length > 0 && (
        <div>
          <h3 style={{ fontSize: 13, color: "var(--ink-3)", textTransform: "uppercase", letterSpacing: 1, fontWeight: 800, marginBottom: 12 }}>
            {t("escalation.resolved", { count: resolved.length })}
          </h3>
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            {resolved.slice(0, 20).map((e) => (
              <div key={e.id} style={{
                background: "var(--surface)",
                border: "1px solid var(--border)",
                borderRadius: 10,
                padding: "10px 14px",
                fontSize: 12,
                color: "var(--ink-2)",
              }}>
                <div style={{ display: "flex", gap: 10, alignItems: "baseline" }}>
                  <strong>{e.agent_name}</strong>
                  <span style={{ fontSize: 10, color: "var(--ink-4)" }}>
                    {new Date(e.created_at).toLocaleDateString("zh-TW")}
                  </span>
                </div>
                <div style={{ marginTop: 4, opacity: 0.7 }}>{e.uncertainty.slice(0, 100)}</div>
                {e.resolution && (
                  <div style={{ marginTop: 6, fontSize: 11, color: "var(--good)", fontWeight: 600 }}>
                    → {e.resolution}
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
