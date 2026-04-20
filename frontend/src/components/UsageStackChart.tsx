import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { UsageAPI, UsageDailyRow } from "../api/client";

/**
 * Daily stacked bar chart of token/cost usage.
 *
 * Wraps /api/usage/daily. Pass group_by + optional scope filters; the
 * chart fetches, aggregates, and renders. Purely SVG — no chart library.
 */
export default function UsageStackChart({
  group_by,
  days = 14,
  project_id,
  agent_id,
  workflow_id,
  title,
  height = 200,
  emptyLabel,
}: {
  group_by: "project" | "agent" | "group" | "workflow" | "model_client";
  days?: number;
  project_id?: number | "null";
  agent_id?: number;
  workflow_id?: number;
  title?: string;
  height?: number;
  emptyLabel?: string;
}) {
  const { t } = useTranslation();
  const { data, isLoading } = useQuery({
    queryKey: ["usage-daily", group_by, days, project_id, agent_id, workflow_id],
    queryFn: () => UsageAPI.daily({ group_by, days, project_id, agent_id, workflow_id }),
  });

  const rows: UsageDailyRow[] = data?.rows ?? [];
  const empty = emptyLabel ?? t("chart.noUsageData");

  return (
    <div style={{
      background: "var(--surface)", border: "1px solid var(--border)",
      borderRadius: 10, padding: 16,
    }}>
      {title && (
        <div style={{ fontSize: 11, textTransform: "uppercase",
                     color: "var(--ink-3)", letterSpacing: 1,
                     fontWeight: 800, marginBottom: 10 }}>
          {title}
        </div>
      )}
      {isLoading ? (
        <div style={{ color: "var(--ink-4)", fontSize: 12 }}>{t("btn.loading")}</div>
      ) : (
        <ChartBody rows={rows} height={height} emptyLabel={empty} />
      )}
    </div>
  );
}


function ChartBody({ rows, height, emptyLabel }: {
  rows: UsageDailyRow[]; height: number; emptyLabel: string;
}) {
  const { days, labels, color, matrix, dailyTotals, maxDaily } = useMemo(() => {
    const days = Array.from(new Set(rows.map((r) => r.date))).sort();
    const labelSet = new Set<string>();
    rows.forEach((r) => labelSet.add(r.label));
    const labels = Array.from(labelSet).sort();
    const palette = [
      "#d97757", "#4f7acb", "#6ec2a5", "#e2a838", "#a676c5",
      "#e0688d", "#7fa83b", "#5ac3d0", "#b26640", "#8a8d95",
    ];
    const color: Record<string, string> = {};
    labels.forEach((l, i) => { color[l] = palette[i % palette.length]; });

    const matrix: Record<string, Record<string, number>> = {};
    days.forEach((d) => {
      matrix[d] = {};
      labels.forEach((l) => (matrix[d][l] = 0));
    });
    rows.forEach((r) => { matrix[r.date][r.label] = r.cost; });

    const dailyTotals = days.map((d) =>
      labels.reduce((s, l) => s + (matrix[d][l] || 0), 0)
    );
    const maxDaily = Math.max(0.01, ...dailyTotals);
    return { days, labels, color, matrix, dailyTotals, maxDaily };
  }, [rows]);

  if (!days.length) {
    return <div style={{ color: "var(--ink-4)", fontSize: 12 }}>{emptyLabel}</div>;
  }

  const barW = 36, gap = 6, padTop = 10, padBottom = 28;

  return (
    <>
      <div style={{ overflowX: "auto" }}>
        <svg width={days.length * (barW + gap) + 30}
             height={height + padTop + padBottom}
             style={{ display: "block" }}>
          {days.map((d, i) => {
            const total = dailyTotals[i];
            let y = height + padTop;
            return (
              <g key={d} transform={`translate(${i * (barW + gap) + 15}, 0)`}>
                {labels.map((l) => {
                  const v = matrix[d][l];
                  if (!v) return null;
                  const h = (v / maxDaily) * height;
                  y -= h;
                  return <rect key={l} x={0} y={y} width={barW} height={h} fill={color[l]} />;
                })}
                <text x={barW / 2} y={height + padTop + 12} fontSize={10}
                      fill="var(--ink-3)" textAnchor="middle">
                  {d.slice(5)}
                </text>
                <text x={barW / 2} y={height + padTop + 24} fontSize={9}
                      fill="var(--ink-4)" textAnchor="middle">
                  ${total.toFixed(2)}
                </text>
              </g>
            );
          })}
        </svg>
      </div>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 10, marginTop: 10, fontSize: 11 }}>
        {labels.map((l) => (
          <div key={l} style={{ display: "flex", alignItems: "center", gap: 6 }}>
            <span style={{ width: 10, height: 10, background: color[l], borderRadius: 2 }} />
            <span style={{ color: "var(--ink-2)" }}>{l}</span>
          </div>
        ))}
      </div>
    </>
  );
}
