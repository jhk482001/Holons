import { useTranslation } from "react-i18next";
import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";

interface GanttTask {
  id: number;
  status: string;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  priority: string;
  run_id: number;
  label: string | null;
}

interface GanttAgent {
  id: number;
  name: string;
  role_title: string;
  status: string;
  tasks: GanttTask[];
}

interface GanttData {
  window_hours: number;
  start_ts: string;
  end_ts: string;
  agents: GanttAgent[];
}

// Preset ranges shown in the picker. Hours-based so the axis math stays
// identical between 1h / 6h / 24h / week.
const RANGE_PRESETS: { key: string; labelKey: string; hours: number }[] = [
  { key: "1h", labelKey: "gantt.range.1h", hours: 1 },
  { key: "6h", labelKey: "gantt.range.6h", hours: 6 },
  { key: "24h", labelKey: "gantt.range.24h", hours: 24 },
  { key: "week", labelKey: "gantt.range.week", hours: 24 * 7 },
];

const STATUS_COLOR: Record<string, { bg: string; fg: string; border: string }> = {
  running: { bg: "#55a96d", fg: "white", border: "#55a96d" },
  done: { bg: "var(--info-soft)", fg: "#3c6f95", border: "#c4d9e8" },
  queued: { bg: "var(--surface-3)", fg: "var(--ink-3)", border: "var(--border-strong)" },
  paused: { bg: "var(--warn-soft)", fg: "var(--warn)", border: "#ecd9a8" },
  failed: { bg: "var(--danger-soft)", fg: "var(--danger)", border: "#f2c4ba" },
  cancelled: { bg: "var(--surface-3)", fg: "var(--ink-4)", border: "var(--border)" },
};

export default function Gantt({ hours = 6 }: { hours?: number }) {
  const { t } = useTranslation();
  // Internal state for the current time window. `rangeHours` controls the
  // span; `anchorEnd` is the right edge of the visible window (null = live,
  // i.e. always NOW()). Pan buttons shift anchorEnd by half the window.
  const [rangeHours, setRangeHours] = useState<number>(hours);
  const [anchorEnd, setAnchorEnd] = useState<number | null>(null);
  const isLive = anchorEnd === null;

  // Query key intentionally omits "now" when in live mode so it stays
  // stable across renders — otherwise the key would change every paint
  // and react-query would refetch in a hot loop, blocking networkidle.
  const { data, isLoading } = useQuery({
    queryKey: ["gantt", rangeHours, isLive ? "live" : anchorEnd],
    queryFn: () => {
      if (isLive) {
        return api.get<GanttData>(
          `/dashboard/gantt?hours=${rangeHours}`,
        );
      }
      const endMsLocked = anchorEnd!;
      const startMsLocked = endMsLocked - rangeHours * 3600 * 1000;
      return api.get<GanttData>(
        `/dashboard/gantt?start_ts=${startMsLocked}&end_ts=${endMsLocked}`,
      );
    },
    refetchInterval: isLive ? 15_000 : false,
  });

  // Resolve the displayed window from the response when possible (live mode
  // doesn't know the server's NOW() until after the fetch). Falls back to
  // the anchor math for the very first render before data arrives.
  const endMs = data ? new Date(data.end_ts).getTime() : (anchorEnd ?? Date.now());
  const startMs = data ? new Date(data.start_ts).getTime() : endMs - rangeHours * 3600 * 1000;

  const now = Date.now();
  const windowMs = rangeHours * 3600 * 1000;
  const start = startMs;
  const end = endMs;

  // Build time-axis labels — ~6 ticks across the window regardless of span
  const axis = useMemo<number[]>(() => {
    const TICKS = 6;
    const arr: number[] = [];
    for (let i = 0; i <= TICKS; i++) {
      arr.push(start + (i * windowMs) / TICKS);
    }
    return arr;
  }, [start, windowMs]);

  const fmtTick = (ms: number) => {
    const d = new Date(ms);
    if (rangeHours >= 48) {
      return `${d.getMonth() + 1}/${d.getDate()}`;
    }
    if (rangeHours >= 12) {
      return `${d.getMonth() + 1}/${d.getDate()} ${d.getHours().toString().padStart(2, "0")}`;
    }
    return `${d.getHours().toString().padStart(2, "0")}:${d.getMinutes().toString().padStart(2, "0")}`;
  };

  const getPos = (iso: string | null) => {
    if (!iso) return 0;
    const t = new Date(iso).getTime();
    return Math.max(0, Math.min(100, ((t - start) / windowMs) * 100));
  };

  function pan(deltaFrac: number) {
    // Step by half of the current window in each click
    setAnchorEnd((prev) => (prev ?? Date.now()) + deltaFrac * windowMs);
  }

  if (isLoading && !data) return <div style={{ padding: 20, color: "var(--ink-4)" }}>{t("btn.loading")}</div>;
  if (!data) return null;

  return (
    <div className="gantt-panel">
      <div className="gantt-header">
        <h2>Agent Timeline</h2>
        <div className="gantt-controls" data-testid="gantt-controls">
          <button
            className="gantt-nav"
            onClick={() => pan(-0.5)}
            data-testid="gantt-pan-back"
            title="Back"
          >
            ←
          </button>
          <div className="gantt-range-group">
            {RANGE_PRESETS.map((p) => (
              <button
                key={p.key}
                className={`gantt-range ${rangeHours === p.hours ? "active" : ""}`}
                onClick={() => setRangeHours(p.hours)}
                data-testid={`gantt-range-${p.key}`}
              >
                {t(p.labelKey)}
              </button>
            ))}
          </div>
          <button
            className="gantt-nav"
            onClick={() => pan(0.5)}
            data-testid="gantt-pan-forward"
            disabled={isLive}
            title="Forward"
          >
            →
          </button>
          <button
            className={`gantt-nav gantt-live ${isLive ? "active" : ""}`}
            onClick={() => setAnchorEnd(null)}
            data-testid="gantt-live-btn"
            title={t("gantt.realtime")}
          >
            {t("gantt.realtime")}
          </button>
          <div className="hint">
            {new Date(start).toLocaleString("zh-TW")} —{" "}
            {new Date(end).toLocaleString("zh-TW")}
          </div>
        </div>
      </div>

      <div className="gantt-grid">
        <div className="gantt-time-axis">
          {axis.map((t, i) => (
            <span key={i}>{fmtTick(t)}</span>
          ))}
        </div>

        {data.agents.length === 0 && (
          <div className="empty">{t("gantt.noAgents")}</div>
        )}

        {data.agents.map((a) => (
          <div key={a.id} className="gantt-row">
            <div className="agent-col">
              <div className={`dot ${a.status === "active" ? "online" : "off"}`}></div>
              <span className="name">{a.name}</span>
            </div>
            <div className="timeline">
              {a.tasks.map((t) => {
                const startPct = getPos(t.started_at || t.created_at);
                const endPct = t.finished_at
                  ? getPos(t.finished_at)
                  : t.status === "running"
                  ? ((now - start) / windowMs) * 100
                  : startPct + 1;
                const widthPct = Math.max(0.5, endPct - startPct);
                const color = STATUS_COLOR[t.status] || STATUS_COLOR.done;
                return (
                  <div
                    key={t.id}
                    className={`bar ${t.status}`}
                    style={{
                      left: `${startPct}%`,
                      width: `${widthPct}%`,
                      background: color.bg,
                      color: color.fg,
                      border: `1px solid ${color.border}`,
                    }}
                    title={`${t.label || `Task ${t.id}`} · ${t.status} · priority ${t.priority}`}
                  >
                    {t.label || `#${t.id}`}
                  </div>
                );
              })}
            </div>
          </div>
        ))}
      </div>

      <div className="gantt-legend">
        <div className="item"><span className="chip" style={{ background: "#55a96d" }}></span>{t("gantt.legend.running")}</div>
        <div className="item"><span className="chip" style={{ background: "var(--info-soft)", border: "1px solid #c4d9e8" }}></span>{t("gantt.legend.done")}</div>
        <div className="item"><span className="chip" style={{ background: "var(--surface-3)", border: "1.5px dashed var(--border-strong)" }}></span>{t("gantt.legend.queued")}</div>
        <div className="item"><span className="chip" style={{ background: "var(--warn-soft)", border: "1px solid #ecd9a8" }}></span>{t("gantt.legend.paused")}</div>
        <div className="item"><span className="chip" style={{ background: "var(--danger-soft)", border: "1px solid #f2c4ba" }}></span>{t("gantt.legend.failed")}</div>
      </div>

      <style>{`
        .gantt-panel {
          background: var(--surface);
          border: 1px solid var(--border);
          border-radius: var(--radius-lg);
          padding: 26px;
          box-shadow: var(--shadow-sm);
          margin-bottom: 24px;
        }
        .gantt-header {
          display: flex;
          justify-content: space-between;
          align-items: baseline;
          margin-bottom: 22px;
        }
        .gantt-header h2 {
          font-size: 17px;
          font-weight: 800;
          letter-spacing: -0.2px;
        }
        .gantt-header .hint { font-size: 11px; color: var(--ink-4); }

        .gantt-grid {
          display: grid;
          grid-template-columns: 110px 1fr;
          gap: 0 16px;
        }
        .gantt-time-axis {
          grid-column: 2;
          display: flex;
          justify-content: space-between;
          color: var(--ink-3);
          font-weight: 700;
          padding: 0 4px 12px;
          border-bottom: 1px solid var(--border);
          margin-bottom: 12px;
          font-size: 10px;
        }
        .gantt-row {
          display: contents;
        }
        .agent-col {
          display: flex;
          align-items: center;
          gap: 7px;
          padding: 13px 0;
        }
        .agent-col .dot {
          width: 7px;
          height: 7px;
          border-radius: 50%;
        }
        .agent-col .dot.online { background: var(--good); }
        .agent-col .dot.off { background: var(--ink-4); }
        .agent-col .name {
          font-size: 12px;
          font-weight: 700;
          color: var(--ink);
        }
        .timeline {
          position: relative;
          height: 34px;
          background: var(--surface-2);
          border-radius: 7px;
          margin: 6px 0;
          overflow: hidden;
        }
        .bar {
          position: absolute;
          top: 4px;
          bottom: 4px;
          border-radius: 5px;
          display: flex;
          align-items: center;
          padding: 0 8px;
          font-size: 9px;
          font-weight: 700;
          white-space: nowrap;
          overflow: hidden;
          text-overflow: ellipsis;
        }
        .bar.running {
          animation: pulse 2s ease-in-out infinite;
        }
        @keyframes pulse {
          0%, 100% { opacity: 1; }
          50% { opacity: 0.82; }
        }
        .empty {
          grid-column: 1 / -1;
          text-align: center;
          color: var(--ink-4);
          padding: 30px;
          font-size: 13px;
        }

        .gantt-legend {
          display: flex;
          gap: 16px;
          margin-top: 22px;
          padding-top: 18px;
          border-top: 1px solid var(--border);
          font-size: 11px;
          color: var(--ink-3);
          flex-wrap: wrap;
        }
        .gantt-legend .item {
          display: flex;
          align-items: center;
          gap: 6px;
        }
        .gantt-legend .chip {
          width: 14px;
          height: 10px;
          border-radius: 3px;
          display: inline-block;
        }
        /* Range picker + pan controls */
        .gantt-controls {
          display: flex;
          align-items: center;
          gap: 8px;
          flex-wrap: wrap;
        }
        .gantt-controls .hint {
          font-size: 10px;
          color: var(--ink-4);
          margin-left: 6px;
        }
        .gantt-nav {
          background: var(--surface);
          border: 1px solid var(--border);
          border-radius: 8px;
          padding: 6px 10px;
          font-size: 12px;
          font-weight: 700;
          color: var(--ink-2);
          cursor: pointer;
          transition: background 0.15s, border-color 0.15s;
        }
        .gantt-nav:hover:not(:disabled) {
          background: var(--surface-2);
          border-color: var(--accent);
        }
        .gantt-nav:disabled {
          opacity: 0.4;
          cursor: not-allowed;
        }
        .gantt-live.active {
          background: var(--accent-soft);
          color: var(--accent);
          border-color: var(--accent);
        }
        .gantt-range-group {
          display: flex;
          gap: 2px;
          padding: 2px;
          background: var(--surface-2);
          border-radius: 10px;
          border: 1px solid var(--border);
        }
        .gantt-range {
          background: transparent;
          border: none;
          padding: 5px 12px;
          font-size: 11px;
          font-weight: 700;
          color: var(--ink-3);
          border-radius: 8px;
          cursor: pointer;
          transition: background 0.15s, color 0.15s;
        }
        .gantt-range:hover { color: var(--ink-2); }
        .gantt-range.active {
          background: var(--surface);
          color: var(--accent);
          box-shadow: 0 1px 3px rgba(60, 45, 20, 0.08);
        }
      `}</style>
    </div>
  );
}
