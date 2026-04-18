import { useTranslation } from "react-i18next";
import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";

interface Quota {
  id: number;
  name: string;
  window_type: string;
  max_tokens: number | null;
  max_cost_usd: number | null;
  current_tokens: number;
  current_cost_usd: number;
  hard_limit: boolean;
  enabled: boolean;
}

function useWindowLabels(): Record<string, string> {
  const { t } = useTranslation();
  return {
    hourly: t("quota.hourly"),
    daily: t("quota.daily"),
    weekly: t("quota.weekly"),
    monthly: t("quota.monthly"),
    project: t("quota.project"),
    lifetime: t("quota.lifetime"),
  };
}

export default function QuotaEditor({ agentId }: { agentId: number }) {
  const { t } = useTranslation();
  const WINDOW_LABELS = useWindowLabels();
  const qc = useQueryClient();
  const { data: quotas = [] } = useQuery({
    queryKey: ["quotas", agentId],
    queryFn: () => api.get<Quota[]>(`/agents/${agentId}/quotas`),
  });

  const [adding, setAdding] = useState(false);
  const [form, setForm] = useState({
    name: "",
    window_type: "monthly",
    max_cost_usd: "10",
    max_tokens: "",
    hard_limit: true,
  });

  const createMutation = useMutation({
    mutationFn: (data: any) => api.post(`/agents/${agentId}/quotas`, data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["quotas", agentId] });
      setAdding(false);
      setForm({ name: "", window_type: "monthly", max_cost_usd: "10", max_tokens: "", hard_limit: true });
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (qid: number) => api.del(`/quotas/${qid}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["quotas", agentId] }),
  });

  return (
    <div className="quota-editor">
      <div className="section-head">
        <h3>{t("quota.title")}</h3>
        {!adding && <button className="add-btn" onClick={() => setAdding(true)}>{t("quota.add")}</button>}
      </div>

      {adding && (
        <div className="quota-form">
          <div className="field">
            <label>{t("quota.name")}</label>
            <input
              type="text"
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
              placeholder={t("quota.namePlaceholder")}
            />
          </div>
          <div className="field-row">
            <div className="field">
              <label>{t("quota.window")}</label>
              <select
                value={form.window_type}
                onChange={(e) => setForm({ ...form, window_type: e.target.value })}
              >
                {Object.entries(WINDOW_LABELS).map(([k, v]) => (
                  <option key={k} value={k}>{v}</option>
                ))}
              </select>
            </div>
            <div className="field">
              <label>{t("quota.maxCost")}</label>
              <input
                type="number"
                step="0.01"
                value={form.max_cost_usd}
                onChange={(e) => setForm({ ...form, max_cost_usd: e.target.value })}
              />
            </div>
            <div className="field">
              <label>{t("quota.maxTokens")}</label>
              <input
                type="number"
                value={form.max_tokens}
                onChange={(e) => setForm({ ...form, max_tokens: e.target.value })}
                placeholder={t("quota.optional")}
              />
            </div>
          </div>
          <div className="field">
            <label>
              <input
                type="checkbox"
                checked={form.hard_limit}
                onChange={(e) => setForm({ ...form, hard_limit: e.target.checked })}
              />
              {t("quota.hardLimit")}
            </label>
          </div>
          <div className="form-actions">
            <button className="cancel" onClick={() => setAdding(false)}>{t("btn.cancel")}</button>
            <button
              className="save"
              onClick={() => createMutation.mutate({
                name: form.name || t("quota.newBudget"),
                window_type: form.window_type,
                max_cost_usd: parseFloat(form.max_cost_usd) || null,
                max_tokens: form.max_tokens ? parseInt(form.max_tokens) : null,
                hard_limit: form.hard_limit,
              })}
            >
              {t("quota.addBtn")}
            </button>
          </div>
        </div>
      )}

      {quotas.length === 0 && !adding && (
        <div className="empty">{t("quota.empty")}</div>
      )}

      <div className="quota-list">
        {quotas.map((q) => {
          const costPct = q.max_cost_usd ? (q.current_cost_usd / q.max_cost_usd) * 100 : 0;
          const tokenPct = q.max_tokens ? (q.current_tokens / q.max_tokens) * 100 : 0;
          const maxPct = Math.max(costPct, tokenPct);
          const level = maxPct > 90 ? "danger" : maxPct > 70 ? "warn" : "ok";
          return (
            <div key={q.id} className={`quota-card ${level}`}>
              <div className="quota-head">
                <div>
                  <div className="quota-name">{q.name}</div>
                  <div className="quota-window">{WINDOW_LABELS[q.window_type]}</div>
                </div>
                <button className="del-btn" onClick={() => deleteMutation.mutate(q.id)}>{t("quota.delete")}</button>
              </div>
              <div className="quota-bar">
                <div className={`quota-bar-fill ${level}`} style={{ width: `${Math.min(100, maxPct)}%` }}></div>
              </div>
              <div className="quota-metrics">
                {q.max_cost_usd && (
                  <div>
                    <strong>${Number(q.current_cost_usd).toFixed(3)}</strong> / ${Number(q.max_cost_usd).toFixed(2)}
                    <span className="small"> ({costPct.toFixed(0)}%)</span>
                  </div>
                )}
                {q.max_tokens && (
                  <div>
                    <strong>{q.current_tokens.toLocaleString()}</strong> / {q.max_tokens.toLocaleString()} tokens
                  </div>
                )}
                {q.hard_limit && <div className="chip">hard limit</div>}
              </div>
            </div>
          );
        })}
      </div>

      <style>{`
        .quota-editor .section-head {
          display: flex;
          align-items: center;
          justify-content: space-between;
          margin-bottom: 16px;
        }
        .quota-editor h3 {
          font-size: 13px;
          font-weight: 800;
          color: var(--ink);
        }
        .quota-editor .add-btn {
          padding: 6px 14px;
          background: var(--accent-soft);
          color: var(--accent);
          border: 1px solid var(--accent-line);
          border-radius: 8px;
          font-size: 12px;
          font-weight: 700;
        }
        .quota-editor .empty {
          color: var(--ink-4);
          font-size: 12px;
          padding: 20px;
          text-align: center;
        }
        .quota-form {
          background: var(--surface-2);
          border: 1px solid var(--border);
          border-radius: 12px;
          padding: 18px;
          margin-bottom: 16px;
        }
        .quota-form .field { margin-bottom: 12px; }
        .quota-form .field-row {
          display: grid;
          grid-template-columns: 1fr 1fr 1fr;
          gap: 10px;
          margin-bottom: 12px;
        }
        .quota-form .field-row .field { margin-bottom: 0; }
        .quota-form label {
          display: block;
          font-size: 10px;
          font-weight: 800;
          color: var(--ink-3);
          text-transform: uppercase;
          letter-spacing: 0.8px;
          margin-bottom: 5px;
        }
        .quota-form input, .quota-form select {
          width: 100%;
          padding: 8px 10px;
          border-radius: 8px;
          border: 1px solid var(--border);
          background: white;
          font-size: 13px;
          color: var(--ink);
          font-family: inherit;
        }
        .quota-form input[type="checkbox"] { width: auto; margin-right: 6px; }
        .form-actions {
          display: flex;
          gap: 8px;
          justify-content: flex-end;
          margin-top: 10px;
        }
        .form-actions button {
          padding: 7px 14px;
          border-radius: 8px;
          font-size: 12px;
          font-weight: 700;
          border: 1px solid var(--border);
          background: white;
          color: var(--ink-2);
        }
        .form-actions .save {
          background: var(--accent);
          color: white;
          border-color: var(--accent);
        }
        .quota-list { display: flex; flex-direction: column; gap: 10px; }
        .quota-card {
          background: var(--surface-2);
          border: 1px solid var(--border);
          border-radius: 12px;
          padding: 14px 16px;
        }
        .quota-card.warn { border-color: #edc87f; background: #fffbf3; }
        .quota-card.danger { border-color: #e8a899; background: var(--danger-soft); }
        .quota-head {
          display: flex;
          align-items: start;
          justify-content: space-between;
          margin-bottom: 10px;
        }
        .quota-name { font-size: 13px; font-weight: 800; color: var(--ink); }
        .quota-window { font-size: 10px; color: var(--ink-3); margin-top: 2px; }
        .del-btn {
          padding: 3px 8px;
          font-size: 10px;
          background: transparent;
          color: var(--ink-4);
          border: 1px solid var(--border);
          border-radius: 6px;
        }
        .del-btn:hover { color: var(--danger); border-color: var(--danger); }
        .quota-bar {
          height: 6px;
          background: var(--surface);
          border-radius: 3px;
          overflow: hidden;
          margin-bottom: 8px;
        }
        .quota-bar-fill {
          height: 100%;
          background: var(--good);
        }
        .quota-bar-fill.warn { background: var(--warn); }
        .quota-bar-fill.danger { background: var(--danger); }
        .quota-metrics {
          display: flex;
          gap: 16px;
          font-size: 11px;
          color: var(--ink-2);
          flex-wrap: wrap;
        }
        .quota-metrics strong { color: var(--ink); font-weight: 800; }
        .quota-metrics .small { color: var(--ink-4); font-size: 10px; }
        .quota-metrics .chip {
          padding: 2px 8px;
          background: var(--accent-soft);
          color: var(--accent);
          border-radius: 999px;
          font-size: 9px;
          font-weight: 800;
          text-transform: uppercase;
        }
      `}</style>
    </div>
  );
}
