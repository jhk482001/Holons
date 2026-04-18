import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { DashboardAPI, api } from "../api/client";
import Avatar from "../components/Avatar";
import Gantt from "../components/Gantt";
import LoadHeatmap from "../components/LoadHeatmap";
import UsageStackChart from "../components/UsageStackChart";
import "./Dashboard.css";

export default function Dashboard() {
  const { t } = useTranslation();
  const { data: summary } = useQuery({
    queryKey: ["dashboard-summary"],
    queryFn: DashboardAPI.summary,
    refetchInterval: 10_000,
  });
  const { data: load = [] } = useQuery({
    queryKey: ["dashboard-load"],
    queryFn: DashboardAPI.agentLoad,
    refetchInterval: 10_000,
  });
  const { data: nearQuota = [] } = useQuery({
    queryKey: ["dashboard-near-quota"],
    queryFn: () => api.get<Array<{
      id: number; name: string; role_title: string | null;
      avatar_config: Record<string,string> | null;
      today_cost: number; today_tokens: number;
      daily_cost_quota: number | null; daily_token_quota: number | null;
      pct: number;
    }>>("/dashboard/quota_overview"),
    refetchInterval: 30_000,
  });

  return (
    <div className="page dashboard">
      <h1>{t("dashboard.title")}</h1>
      <div className="subtitle">{t("dashboard.subtitle")}</div>

      <div className="summary">
        <SumCard label={t("dashboard.activeAgents")} value={`${summary?.active_agents ?? 0}`} sub={t("dashboard.activeAgentsSub")} />
        <SumCard label={t("dashboard.queueDepth")} value={`${summary?.total_queue_depth ?? 0}`} sub={t("dashboard.queueDepthSub")} />
        <SumCard label={t("dashboard.todayCost")} value={`$${(summary?.today_cost_usd ?? 0).toFixed(2)}`} sub={t("dashboard.todayCostSub")} />
        <SumCard label={t("dashboard.todayRuns")} value={`${summary?.today_runs ?? 0}`} sub={t("dashboard.todayRunsSub")} />
      </div>

      {nearQuota.length > 0 && (
        <section style={{ marginTop: 20 }}>
          <h2 className="section-title">{t("dashboard.nearQuota")}</h2>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 10 }}>
            {nearQuota.map((n) => {
              const pct = Math.round(n.pct * 100);
              const color = pct >= 100 ? "#e05555" : "#e2a838";
              return (
                <div key={n.id} style={{
                  display: "flex", alignItems: "center", gap: 10,
                  padding: 10, background: "var(--surface)",
                  border: `1px solid ${color}`, borderRadius: 10,
                  minWidth: 220,
                }}>
                  <Avatar cfg={n.avatar_config as any} size={32} title={n.name} />
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ fontSize: 12, fontWeight: 800 }}>{n.name}</div>
                    <div style={{ fontSize: 10, color: "var(--ink-3)" }}>
                      {n.daily_cost_quota && (
                        <>${n.today_cost.toFixed(2)} / ${n.daily_cost_quota.toFixed(2)}</>
                      )}
                      {!n.daily_cost_quota && n.daily_token_quota && (
                        <>{n.today_tokens.toLocaleString()} / {n.daily_token_quota.toLocaleString()} tok</>
                      )}
                    </div>
                  </div>
                  <div style={{ fontSize: 16, fontWeight: 800, color }}>
                    {pct}%
                  </div>
                </div>
              );
            })}
          </div>
        </section>
      )}

      <Gantt hours={6} />

      <LoadHeatmap />

      <h2 className="section-title">{t("dashboard.usageByProject")}</h2>
      <UsageStackChart group_by="project" days={14} title={undefined} />

      <h2 className="section-title" style={{ marginTop: 20 }}>
        {t("dashboard.usageByAgent")}
      </h2>
      <UsageStackChart group_by="agent" days={14} title={undefined} />

      <h2 className="section-title" style={{ marginTop: 20 }}>
        {t("dashboard.usageByGroup")}
      </h2>
      <UsageStackChart group_by="group" days={14} title={undefined} />

      <h2 className="section-title">{t("dashboard.agentLoad")}</h2>
      <div className="load-grid">
        {load.map((a) => {
          const depth = Number(a.queue_depth) || 0;
          const max = Number(a.max_queue_depth) || 1;
          const fill = Math.min(100, (depth / max) * 100);
          const level = fill > 80 ? "critical" : fill > 50 ? "high" : "normal";
          const cost = Number(a.today_cost) || 0;
          return (
            <div key={a.id} className={`load-card ${level === "critical" ? "danger" : level === "high" ? "warn" : ""}`}>
              <div className="card-head">
                <Avatar cfg={a.avatar_config} size={42} title={a.name} />
                <div>
                  <div className="name">{a.name}</div>
                  <div className="role">{a.role_title}</div>
                </div>
                <div className={`status-badge ${a.status}`}>{statusLabel(a.status)}</div>
              </div>
              <div className="bar"><div className={`bar-fill ${level}`} style={{ width: `${fill}%` }}></div></div>
              <div className="metrics">
                <div className="cell"><div className="num">{depth}/{max}</div>queue</div>
                <div className="cell"><div className="num">${cost.toFixed(2)}</div>cost today</div>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function SumCard({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="sum-card">
      <div className="label">{label}</div>
      <div className="value">{value}</div>
      {sub && <div className="delta">{sub}</div>}
    </div>
  );
}

function statusLabel(s: string): string {
  // i18n happens at the component level; this is a thin lookup
  // for backward compat — the caller should use t() instead.
  return s;
}
