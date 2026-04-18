import { useTranslation } from "react-i18next";
import { useQuery } from "@tanstack/react-query";
import { DashboardAPI } from "../api/client";

/**
 * Condensed agent loading widget for the dashboard.
 *
 * One row per agent, one 24-cell colored bar per row showing hourly
 * task counts over the past 24 hours. Intensity is purely visual — no
 * numeric labels, no legends. At a glance you can see which agent is
 * hot and when.
 *
 * Busyness scale uses a 0-10+ linear ramp via HSL lightness/saturation.
 */
export default function LoadHeatmap() {
  const { t } = useTranslation();
  const { data, isLoading } = useQuery({
    queryKey: ["load-heatmap"],
    queryFn: () => DashboardAPI.loadHeatmap(24),
    refetchInterval: 60_000,
  });

  if (isLoading || !data) {
    return (
      <div style={{ padding: 20, color: "var(--ink-4)", fontSize: 12 }}>
        {t("btn.loading")}
      </div>
    );
  }

  // Normalize intensity across all agents so relative colors are stable.
  const maxVal = Math.max(
    1,
    ...data.agents.flatMap((a) => a.values),
  );

  return (
    <div className="heatmap-panel" data-testid="load-heatmap">
      <div className="heatmap-head">
        <h2>{t("heatmap.title")}</h2>
        <div className="heatmap-hint">{t("heatmap.hint")}</div>
      </div>
      <div className="heatmap-body">
        {data.agents.length === 0 ? (
          <div className="heatmap-empty">{t("heatmap.noAgents")}</div>
        ) : (
          data.agents.map((a) => (
            <div
              key={a.id}
              className="heatmap-row"
              data-testid={`heatmap-row-${a.id}`}
            >
              <div className="heatmap-name">
                {a.name}
                {a.is_lead && <span className="heatmap-lead">LEAD</span>}
              </div>
              <div className="heatmap-cells">
                {a.values.map((v, i) => {
                  const intensity = v === 0 ? 0 : Math.min(1, v / maxVal);
                  return (
                    <div
                      key={i}
                      className="heatmap-cell"
                      data-value={v}
                      style={{
                        background: cellColor(intensity),
                      }}
                      title={`${23 - i}h ago · ${v} tasks`}
                    />
                  );
                })}
              </div>
            </div>
          ))
        )}
      </div>
      <style>{`
        .heatmap-panel {
          background: var(--surface);
          border: 1px solid var(--border);
          border-radius: 14px;
          padding: 18px 22px;
          box-shadow: var(--shadow-sm);
          margin-bottom: 24px;
        }
        .heatmap-head {
          display: flex;
          justify-content: space-between;
          align-items: baseline;
          margin-bottom: 14px;
        }
        .heatmap-head h2 {
          font-size: 15px;
          font-weight: 800;
          letter-spacing: -0.2px;
        }
        .heatmap-hint {
          font-size: 10px;
          color: var(--ink-4);
        }
        .heatmap-body {
          display: flex;
          flex-direction: column;
          gap: 6px;
        }
        .heatmap-empty {
          padding: 30px;
          text-align: center;
          color: var(--ink-4);
          font-size: 12px;
        }
        .heatmap-row {
          display: grid;
          grid-template-columns: 110px 1fr;
          gap: 10px;
          align-items: center;
        }
        .heatmap-name {
          font-size: 11px;
          font-weight: 700;
          color: var(--ink-2);
          white-space: nowrap;
          overflow: hidden;
          text-overflow: ellipsis;
          display: flex;
          align-items: center;
          gap: 6px;
        }
        .heatmap-lead {
          font-size: 8px;
          font-weight: 800;
          color: var(--accent);
          background: var(--accent-soft);
          padding: 1px 5px;
          border-radius: 4px;
          letter-spacing: 0.3px;
        }
        .heatmap-cells {
          display: grid;
          grid-template-columns: repeat(24, 1fr);
          gap: 2px;
          height: 16px;
        }
        .heatmap-cell {
          border-radius: 2px;
          transition: transform 0.1s;
        }
        .heatmap-cell:hover {
          transform: scale(1.25);
        }
      `}</style>
    </div>
  );
}

function cellColor(t: number): string {
  // 0 → near-surface gray; 1 → accent saturated
  if (t === 0) return "var(--surface-3, #ede8dc)";
  // Lerp from very soft accent to strong accent
  // Using HSL with fixed hue so light → dark looks like a temperature ramp.
  const alpha = 0.15 + 0.85 * t;
  return `rgba(232, 100, 80, ${alpha.toFixed(3)})`;
}
