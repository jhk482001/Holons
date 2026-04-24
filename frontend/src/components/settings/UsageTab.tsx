import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { api } from "../../api/client";

interface UsageSummary {
  calls: number;
  in_tok: number;
  out_tok: number;
  cost_usd: number;
  errors: number;
}
interface UsageTopUser {
  user_id: number;
  username: string | null;
  display_name: string | null;
  calls: number;
  cost_usd: number;
  tokens: number;
}
interface UsageTopModel {
  model_id: string;
  provider: string | null;
  calls: number;
  cost_usd: number;
  tokens: number;
}
interface UsageKindBreakdown {
  kind: string;
  calls: number;
  cost_usd: number;
  tokens: number;
}
interface UsageSeriesRow {
  day: string;
  user_id: number;
  username: string | null;
  cost_usd: number;
  tokens: number;
  calls: number;
}
interface UsageRecord {
  id: number;
  user_id: number;
  username: string | null;
  display_name: string | null;
  agent_id: number | null;
  agent_name: string | null;
  run_id: number | null;
  thread_id: string | null;
  model_client_id: number | null;
  model_client_name: string | null;
  model_id: string | null;
  provider: string | null;
  kind: string;
  input_tokens: number;
  output_tokens: number;
  cost_usd: number;
  duration_ms: number | null;
  error: string | null;
  created_at: string;
}
interface UsageResponse {
  window_days: number;
  summary: UsageSummary;
  top_users: UsageTopUser[];
  top_models: UsageTopModel[];
  kind_breakdown: UsageKindBreakdown[];
  series: UsageSeriesRow[];
  records: UsageRecord[];
}

// A single-tone palette (orange variants) keyed on user_id so stacked
// bars stay stable across refreshes. Good enough for ~10 users; wraps
// if more.
const USER_COLOURS = [
  "#ff8a65", "#ffb091", "#d96a44", "#b54a20", "#e89a75",
  "#ffcdb0", "#c8774d", "#9e4f30", "#f5bfa4", "#874020",
];
function userColour(userId: number): string {
  return USER_COLOURS[userId % USER_COLOURS.length];
}

function fmtUsd(n: number): string {
  if (!n) return "$0.00";
  if (n < 0.01) return `$${n.toFixed(4)}`;
  return `$${n.toFixed(2)}`;
}
function fmtTok(n: number): string {
  if (n < 1000) return `${n}`;
  if (n < 1_000_000) return `${(n / 1000).toFixed(1)}k`;
  return `${(n / 1_000_000).toFixed(2)}M`;
}
function fmtDate(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleString();
}

export default function UsageTab() {
  const { t } = useTranslation();
  const [days, setDays] = useState<number>(14);
  const [userFilter, setUserFilter] = useState<number | "">("");
  const [kindFilter, setKindFilter] = useState<string>("");

  const { data, isLoading, refetch } = useQuery<UsageResponse>({
    queryKey: ["admin-usage", days, userFilter, kindFilter],
    queryFn: () => {
      const qs = new URLSearchParams();
      qs.set("from_days", String(days));
      qs.set("limit", "200");
      if (userFilter) qs.set("user_id", String(userFilter));
      if (kindFilter) qs.set("kind", kindFilter);
      return api.get(`/admin/usage?${qs.toString()}`);
    },
    refetchInterval: 30_000,
  });

  const chartData = useMemo(() => {
    if (!data) return { days: [] as string[], users: [] as { id: number; label: string }[], matrix: {} as Record<string, Record<number, number>> };
    const daySet = new Set<string>();
    const userSet = new Map<number, string>();
    const matrix: Record<string, Record<number, number>> = {};
    for (const r of data.series) {
      daySet.add(r.day);
      userSet.set(r.user_id, r.username || `u${r.user_id}`);
      matrix[r.day] = matrix[r.day] || {};
      matrix[r.day][r.user_id] = (matrix[r.day][r.user_id] || 0) + r.cost_usd;
    }
    const dayList = Array.from(daySet).sort();
    const users = Array.from(userSet.entries()).map(([id, label]) => ({ id, label }));
    return { days: dayList, users, matrix };
  }, [data]);

  const chartMaxCost = useMemo(() => {
    let m = 0;
    for (const d of chartData.days) {
      let sum = 0;
      for (const u of chartData.users) sum += chartData.matrix[d]?.[u.id] || 0;
      if (sum > m) m = sum;
    }
    return m || 0.0001; // avoid /0
  }, [chartData]);

  return (
    <div data-testid="settings-usage-tab" style={{ paddingTop: 8 }}>
      <div style={{ fontSize: 12, color: "var(--ink-3)", marginBottom: 16, lineHeight: 1.6 }}>
        {t("usage.description")}
      </div>

      {/* Filters */}
      <div style={{
        display: "flex", flexWrap: "wrap", gap: 12, alignItems: "center",
        marginBottom: 18, padding: 14,
        background: "var(--surface)",
        border: "1px solid var(--border)", borderRadius: 12,
      }}>
        <label style={{ fontSize: 12, color: "var(--ink-3)", fontWeight: 700 }}>
          {t("usage.windowDays")}
          <select
            value={days}
            onChange={(e) => setDays(Number(e.target.value))}
            data-testid="usage-window-days"
            style={{ marginLeft: 8, padding: "4px 8px", borderRadius: 6,
              border: "1px solid var(--border)", background: "var(--surface)" }}
          >
            <option value={1}>1</option>
            <option value={7}>7</option>
            <option value={14}>14</option>
            <option value={30}>30</option>
            <option value={90}>90</option>
          </select>
        </label>
        <label style={{ fontSize: 12, color: "var(--ink-3)", fontWeight: 700 }}>
          {t("usage.filterUser")}
          <select
            value={userFilter}
            onChange={(e) => setUserFilter(e.target.value === "" ? "" : Number(e.target.value))}
            data-testid="usage-filter-user"
            style={{ marginLeft: 8, padding: "4px 8px", borderRadius: 6,
              border: "1px solid var(--border)", background: "var(--surface)" }}
          >
            <option value="">{t("usage.allUsers")}</option>
            {(data?.top_users || []).map((u) => (
              <option key={u.user_id} value={u.user_id}>
                {u.username || `u${u.user_id}`}
              </option>
            ))}
          </select>
        </label>
        <label style={{ fontSize: 12, color: "var(--ink-3)", fontWeight: 700 }}>
          {t("usage.filterKind")}
          <select
            value={kindFilter}
            onChange={(e) => setKindFilter(e.target.value)}
            data-testid="usage-filter-kind"
            style={{ marginLeft: 8, padding: "4px 8px", borderRadius: 6,
              border: "1px solid var(--border)", background: "var(--surface)" }}
          >
            <option value="">{t("usage.allKinds")}</option>
            <option value="agent">agent</option>
            <option value="lead">lead</option>
            <option value="lead_proxy">lead_proxy</option>
            <option value="skill_extract">skill_extract</option>
            <option value="project_report">project_report</option>
            <option value="client_test">client_test</option>
            <option value="system">system</option>
          </select>
        </label>
        <button
          onClick={() => refetch()}
          className="mbtn"
          style={{ marginLeft: "auto", fontSize: 12 }}
        >
          {t("usage.refresh")}
        </button>
      </div>

      {isLoading && <div style={{ fontSize: 12, color: "var(--ink-4)" }}>{t("btn.loading")}</div>}

      {data && (
        <>
          {/* Widgets */}
          <div style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))",
            gap: 12, marginBottom: 20,
          }}>
            <Widget label={t("usage.totalCost")}    value={fmtUsd(data.summary?.cost_usd || 0)} />
            <Widget label={t("usage.totalCalls")}   value={(data.summary?.calls || 0).toLocaleString()} />
            <Widget label={t("usage.totalTokens")}  value={fmtTok((data.summary?.in_tok || 0) + (data.summary?.out_tok || 0))} />
            <Widget label={t("usage.errorCount")}   value={(data.summary?.errors || 0).toLocaleString()}
                     tone={(data.summary?.errors || 0) > 0 ? "warn" : "ok"} />
            <Widget label={t("usage.topUser")}
                     value={data.top_users[0]?.username || "—"}
                     sub={data.top_users[0] ? fmtUsd(data.top_users[0].cost_usd) : ""} />
            <Widget label={t("usage.topModel")}
                     value={data.top_models[0]?.model_id ?
                       (data.top_models[0].model_id.length > 28
                         ? data.top_models[0].model_id.slice(-28)
                         : data.top_models[0].model_id)
                       : "—"}
                     sub={data.top_models[0] ? fmtUsd(data.top_models[0].cost_usd) : ""} />
          </div>

          {/* Chart */}
          <section style={{ marginBottom: 24 }}>
            <h3 style={{ fontSize: 14, fontWeight: 800, marginBottom: 10 }}>
              {t("usage.chartTitle")}
            </h3>
            <div style={{
              background: "var(--surface)", border: "1px solid var(--border)",
              borderRadius: 12, padding: 14,
            }}>
              {chartData.days.length === 0 ? (
                <div style={{ fontSize: 12, color: "var(--ink-4)", textAlign: "center", padding: 30 }}>
                  {t("usage.noData")}
                </div>
              ) : (
                <div style={{ display: "flex", gap: 4, alignItems: "flex-end", height: 180 }}>
                  {chartData.days.map((day) => {
                    const usersInDay = chartData.users.filter(
                      (u) => (chartData.matrix[day]?.[u.id] || 0) > 0,
                    );
                    const totalForDay = usersInDay.reduce(
                      (s, u) => s + (chartData.matrix[day][u.id] || 0), 0,
                    );
                    const heightPct = (totalForDay / chartMaxCost) * 100;
                    return (
                      <div key={day} style={{
                        flex: 1, display: "flex", flexDirection: "column",
                        alignItems: "center", height: "100%",
                      }}>
                        <div
                          title={`${day} · ${fmtUsd(totalForDay)}`}
                          style={{
                            width: "100%", maxWidth: 36,
                            height: `${Math.max(heightPct, totalForDay > 0 ? 2 : 0)}%`,
                            display: "flex", flexDirection: "column-reverse",
                            borderRadius: "4px 4px 0 0", overflow: "hidden",
                            background: totalForDay > 0 ? "transparent" : "rgba(0,0,0,0.05)",
                          }}
                        >
                          {usersInDay.map((u) => {
                            const v = chartData.matrix[day][u.id] || 0;
                            const segPct = (v / totalForDay) * 100;
                            return (
                              <div
                                key={u.id}
                                title={`${u.label}: ${fmtUsd(v)}`}
                                style={{ height: `${segPct}%`, background: userColour(u.id) }}
                              />
                            );
                          })}
                        </div>
                        <div style={{
                          fontSize: 9, color: "var(--ink-4)", marginTop: 4,
                          whiteSpace: "nowrap", transform: "rotate(-40deg)",
                          transformOrigin: "left top",
                        }}>
                          {day.slice(5)}
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}
              {/* Legend */}
              <div style={{
                display: "flex", flexWrap: "wrap", gap: 10,
                marginTop: 30, paddingTop: 10, borderTop: "1px solid var(--border)",
                fontSize: 11, color: "var(--ink-3)",
              }}>
                {chartData.users.map((u) => (
                  <div key={u.id} style={{ display: "flex", alignItems: "center", gap: 4 }}>
                    <span style={{
                      display: "inline-block", width: 10, height: 10,
                      background: userColour(u.id), borderRadius: 2,
                    }} />
                    {u.label}
                  </div>
                ))}
              </div>
            </div>
          </section>

          {/* Top users + top models side by side */}
          <div style={{
            display: "grid", gridTemplateColumns: "1fr 1fr",
            gap: 14, marginBottom: 24,
          }}>
            <section>
              <h3 style={{ fontSize: 13, fontWeight: 800, marginBottom: 8 }}>
                {t("usage.byUser")}
              </h3>
              <TableList rows={data.top_users.map((u) => ({
                key: String(u.user_id),
                main: u.username || `u${u.user_id}`,
                sub: u.display_name || "",
                cost: u.cost_usd,
                calls: u.calls,
                tokens: u.tokens,
              }))} />
            </section>
            <section>
              <h3 style={{ fontSize: 13, fontWeight: 800, marginBottom: 8 }}>
                {t("usage.byModel")}
              </h3>
              <TableList rows={data.top_models.map((m) => ({
                key: m.model_id,
                main: m.model_id,
                sub: m.provider || "",
                cost: m.cost_usd,
                calls: m.calls,
                tokens: m.tokens,
              }))} />
            </section>
          </div>

          {/* Kind breakdown */}
          <section style={{ marginBottom: 24 }}>
            <h3 style={{ fontSize: 13, fontWeight: 800, marginBottom: 8 }}>
              {t("usage.byKind")}
            </h3>
            <TableList rows={data.kind_breakdown.map((k) => ({
              key: k.kind,
              main: k.kind,
              sub: "",
              cost: k.cost_usd,
              calls: k.calls,
              tokens: k.tokens,
            }))} />
          </section>

          {/* Records */}
          <section>
            <h3 style={{ fontSize: 13, fontWeight: 800, marginBottom: 8 }}>
              {t("usage.records")} · {data.records.length}
            </h3>
            <div style={{
              background: "var(--surface)", border: "1px solid var(--border)",
              borderRadius: 12, overflow: "hidden",
            }}>
              <div style={{
                display: "grid",
                gridTemplateColumns: "auto 1fr 1fr 80px auto auto auto",
                gap: 8, padding: "10px 14px",
                background: "var(--surface-2)", fontSize: 11, fontWeight: 700,
                color: "var(--ink-3)", borderBottom: "1px solid var(--border)",
              }}>
                <div>{t("usage.col.when")}</div>
                <div>{t("usage.col.user")}</div>
                <div>{t("usage.col.agent")}</div>
                <div>{t("usage.col.kind")}</div>
                <div>{t("usage.col.tokens")}</div>
                <div>{t("usage.col.cost")}</div>
                <div>{t("usage.col.error")}</div>
              </div>
              {data.records.map((r) => (
                <div key={r.id} style={{
                  display: "grid",
                  gridTemplateColumns: "auto 1fr 1fr 80px auto auto auto",
                  gap: 8, padding: "8px 14px",
                  fontSize: 11, borderBottom: "1px solid var(--border)",
                  alignItems: "center",
                }}>
                  <div style={{ color: "var(--ink-3)", whiteSpace: "nowrap" }}>
                    {fmtDate(r.created_at)}
                  </div>
                  <div>
                    <div style={{ fontWeight: 700 }}>{r.username || `u${r.user_id}`}</div>
                    {r.display_name && (
                      <div style={{ fontSize: 10, color: "var(--ink-4)" }}>{r.display_name}</div>
                    )}
                  </div>
                  <div>
                    {r.agent_name && <div>{r.agent_name}</div>}
                    {r.model_id && (
                      <div style={{ fontSize: 10, color: "var(--ink-4)", fontFamily: "monospace" }}>
                        {r.model_id}
                      </div>
                    )}
                  </div>
                  <div>
                    <span style={{
                      fontSize: 10, padding: "2px 6px", borderRadius: 10,
                      background: "var(--surface-2)", color: "var(--ink-2)",
                    }}>
                      {r.kind}
                    </span>
                  </div>
                  <div style={{ color: "var(--ink-3)", whiteSpace: "nowrap" }}>
                    {fmtTok(r.input_tokens + r.output_tokens)}
                  </div>
                  <div style={{ fontWeight: 700, whiteSpace: "nowrap" }}>
                    {fmtUsd(r.cost_usd)}
                  </div>
                  <div style={{ color: r.error ? "var(--danger)" : "var(--ink-4)" }}>
                    {r.error ? "✗" : "✓"}
                  </div>
                </div>
              ))}
              {data.records.length === 0 && (
                <div style={{ padding: 30, textAlign: "center", fontSize: 12, color: "var(--ink-4)" }}>
                  {t("usage.noRecords")}
                </div>
              )}
            </div>
          </section>
        </>
      )}
    </div>
  );
}


interface WidgetProps {
  label: string;
  value: string;
  sub?: string;
  tone?: "ok" | "warn";
}
function Widget({ label, value, sub, tone }: WidgetProps) {
  const color = tone === "warn" ? "var(--warn)" : "var(--ink-1)";
  return (
    <div style={{
      background: "var(--surface)", border: "1px solid var(--border)",
      borderRadius: 12, padding: 14,
    }}>
      <div style={{ fontSize: 10, color: "var(--ink-4)", fontWeight: 700,
        textTransform: "uppercase", letterSpacing: 0.5 }}>
        {label}
      </div>
      <div style={{ fontSize: 22, fontWeight: 800, color, marginTop: 4 }}>
        {value}
      </div>
      {sub && (
        <div style={{ fontSize: 11, color: "var(--ink-3)", marginTop: 2 }}>
          {sub}
        </div>
      )}
    </div>
  );
}

interface TableListRow {
  key: string;
  main: string;
  sub: string;
  cost: number;
  calls: number;
  tokens: number;
}
function TableList({ rows }: { rows: TableListRow[] }) {
  return (
    <div style={{
      background: "var(--surface)", border: "1px solid var(--border)",
      borderRadius: 12, overflow: "hidden",
    }}>
      {rows.length === 0 && (
        <div style={{ padding: 20, textAlign: "center", fontSize: 12, color: "var(--ink-4)" }}>
          —
        </div>
      )}
      {rows.map((r) => (
        <div key={r.key} style={{
          display: "grid",
          gridTemplateColumns: "1fr auto auto auto",
          gap: 10, padding: "8px 14px", fontSize: 11,
          borderBottom: "1px solid var(--border)",
          alignItems: "center",
        }}>
          <div>
            <div style={{ fontWeight: 700, fontSize: 12 }}>{r.main}</div>
            {r.sub && (
              <div style={{ fontSize: 10, color: "var(--ink-4)" }}>{r.sub}</div>
            )}
          </div>
          <div style={{ color: "var(--ink-3)", whiteSpace: "nowrap" }}>
            {r.calls.toLocaleString()} calls
          </div>
          <div style={{ color: "var(--ink-3)", whiteSpace: "nowrap" }}>
            {fmtTok(r.tokens)}
          </div>
          <div style={{ fontWeight: 700, whiteSpace: "nowrap", minWidth: 70, textAlign: "right" }}>
            {fmtUsd(r.cost)}
          </div>
        </div>
      ))}
    </div>
  );
}
