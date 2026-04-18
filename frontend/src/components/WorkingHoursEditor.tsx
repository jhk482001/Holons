import { useTranslation } from "react-i18next";
import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api, Agent } from "../api/client";

const DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"] as const;
const DAY_LABEL_KEYS = ["hours.mon", "hours.tue", "hours.wed", "hours.thu", "hours.fri", "hours.sat", "hours.sun"];

type DaySchedule = Array<[string, string]>;
type WeekSchedule = Record<string, DaySchedule>;

export default function WorkingHoursEditor({ agent }: { agent: Agent }) {
  const { t } = useTranslation();
  const qc = useQueryClient();

  // hours[day] is an array of [start, end] ranges (24-hour HH:MM)
  const initial: WeekSchedule = (agent as any).working_hours || {};
  const [hours, setHours] = useState<WeekSchedule>(initial);
  const [hasChanges, setHasChanges] = useState(false);

  // Simplified: each day is either "24/7", "9-6", "off", or custom
  const presetFor = (day: string): string => {
    const ranges = hours[day];
    if (!ranges || ranges.length === 0) return "off";
    if (ranges.length === 1) {
      const [s, e] = ranges[0];
      if (s === "00:00" && e === "23:59") return "24h";
      if (s === "09:00" && e === "18:00") return "9-6";
      if (s === "08:00" && e === "17:00") return "8-5";
    }
    return "custom";
  };

  const applyPreset = (day: string, preset: string) => {
    const next = { ...hours };
    if (preset === "off") next[day] = [];
    else if (preset === "24h") next[day] = [["00:00", "23:59"]];
    else if (preset === "9-6") next[day] = [["09:00", "18:00"]];
    else if (preset === "8-5") next[day] = [["08:00", "17:00"]];
    setHours(next);
    setHasChanges(true);
  };

  const save = useMutation({
    mutationFn: () => api.put(`/agents/${agent.id}`, { working_hours: hours }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["agent", agent.id] });
      setHasChanges(false);
    },
  });

  const applyAll = (preset: string) => {
    const next: WeekSchedule = {};
    DAYS.forEach((d) => {
      if (preset === "weekdays" || preset === "weekends") {
        const isWeekend = d === "sat" || d === "sun";
        if ((preset === "weekdays" && !isWeekend) || (preset === "weekends" && isWeekend)) {
          next[d] = [["09:00", "18:00"]];
        } else {
          next[d] = [];
        }
      } else if (preset === "24/7") {
        next[d] = [["00:00", "23:59"]];
      } else if (preset === "off") {
        next[d] = [];
      }
    });
    setHours(next);
    setHasChanges(true);
  };

  return (
    <div className="hours-editor">
      <h3>{t("hours.title")}</h3>
      <p className="help">
        {t("hours.help")}
      </p>

      <div className="preset-row">
        <button onClick={() => applyAll("24/7")}>{t("hours.preset24h")}</button>
        <button onClick={() => applyAll("weekdays")}>{t("hours.presetWeekdays")}</button>
        <button onClick={() => applyAll("weekends")}>{t("hours.presetWeekends")}</button>
        <button onClick={() => applyAll("off")}>{t("hours.presetOff")}</button>
      </div>

      <div className="week-grid">
        {DAYS.map((d, i) => {
          const preset = presetFor(d);
          return (
            <div key={d} className="day-row">
              <div className="day-label">{t(DAY_LABEL_KEYS[i])}</div>
              <div className="day-presets">
                {["24h", "9-6", "8-5", "off"].map((p) => (
                  <button
                    key={p}
                    className={preset === p ? "active" : ""}
                    onClick={() => applyPreset(d, p)}
                  >
                    {p === "24h" ? t("hours.allDay") : p === "off" ? t("hours.off") : p}
                  </button>
                ))}
              </div>
              <div className="day-display">
                {hours[d]?.length
                  ? hours[d].map(([s, e]) => `${s}–${e}`).join(", ")
                  : <span className="off-label">{t("hours.off")}</span>}
              </div>
            </div>
          );
        })}
      </div>

      {hasChanges && (
        <div className="save-bar">
          <span>{t("hours.unsaved")}</span>
          <button onClick={() => save.mutate()} disabled={save.isPending}>
            {save.isPending ? t("hours.saving") : t("hours.save")}
          </button>
        </div>
      )}

      <style>{`
        .hours-editor h3 { font-size: 13px; font-weight: 800; margin-bottom: 6px; }
        .hours-editor .help { font-size: 12px; color: var(--ink-3); margin-bottom: 18px; }
        .preset-row {
          display: flex;
          gap: 6px;
          margin-bottom: 18px;
          flex-wrap: wrap;
        }
        .preset-row button {
          padding: 7px 14px;
          background: var(--surface-2);
          border: 1px solid var(--border);
          border-radius: 8px;
          font-size: 11px;
          font-weight: 700;
          color: var(--ink-2);
        }
        .preset-row button:hover { background: var(--accent-soft); color: var(--accent); border-color: var(--accent-line); }

        .week-grid {
          display: flex;
          flex-direction: column;
          gap: 6px;
          background: var(--surface-2);
          border: 1px solid var(--border);
          border-radius: 12px;
          padding: 12px;
        }
        .day-row {
          display: grid;
          grid-template-columns: 60px 1fr 140px;
          gap: 12px;
          align-items: center;
          padding: 8px 6px;
          border-radius: 8px;
        }
        .day-row:hover { background: white; }
        .day-label {
          font-size: 12px;
          font-weight: 800;
          color: var(--ink);
        }
        .day-presets {
          display: flex;
          gap: 4px;
        }
        .day-presets button {
          padding: 5px 10px;
          background: white;
          border: 1px solid var(--border);
          border-radius: 6px;
          font-size: 10px;
          font-weight: 700;
          color: var(--ink-2);
        }
        .day-presets button:hover { color: var(--ink); }
        .day-presets button.active {
          background: var(--accent);
          color: white;
          border-color: var(--accent);
        }
        .day-display {
          font-size: 11px;
          color: var(--ink-3);
          font-family: var(--font-mono);
          text-align: right;
        }
        .day-display .off-label { color: var(--ink-4); }

        .save-bar {
          display: flex;
          align-items: center;
          justify-content: space-between;
          margin-top: 18px;
          padding: 12px 18px;
          background: var(--accent-soft);
          border: 1px solid var(--accent-line);
          border-radius: 10px;
          font-size: 12px;
          color: var(--accent);
          font-weight: 700;
        }
        .save-bar button {
          padding: 7px 16px;
          background: var(--accent);
          color: white;
          border: none;
          border-radius: 8px;
          font-size: 12px;
          font-weight: 700;
        }
      `}</style>
    </div>
  );
}
