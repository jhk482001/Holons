import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Agent, api } from "../api/client";

/**
 * Simple daily + monthly token / cost caps stored on the agents row
 * (columns: daily_token_quota, daily_cost_quota, monthly_token_quota,
 * monthly_cost_quota). These are "hard caps" — the quotas service blocks
 * any further step once usage >= cap. Leave a field empty for "no cap".
 */
export default function AgentBudgetEditor({ agent }: { agent: Agent }) {
  const { t } = useTranslation();
  const qc = useQueryClient();

  const [dailyCost, setDailyCost] = useState<string>(agent.daily_cost_quota?.toString() ?? "");
  const [dailyTokens, setDailyTokens] = useState<string>(agent.daily_token_quota?.toString() ?? "");
  const [monthlyCost, setMonthlyCost] = useState<string>(agent.monthly_cost_quota?.toString() ?? "");
  const [monthlyTokens, setMonthlyTokens] = useState<string>(agent.monthly_token_quota?.toString() ?? "");
  const [queueLimit, setQueueLimit] = useState<string>(agent.max_queue_depth?.toString() ?? "");

  const { data: usage } = useQuery({
    queryKey: ["agent-usage", agent.id],
    queryFn: () => api.get<{
      today: { cost: number; tokens: number };
      month: { cost: number; tokens: number };
    }>(`/agents/${agent.id}/usage`),
    refetchInterval: 30_000,
  });

  useEffect(() => {
    setDailyCost(agent.daily_cost_quota?.toString() ?? "");
    setDailyTokens(agent.daily_token_quota?.toString() ?? "");
    setMonthlyCost(agent.monthly_cost_quota?.toString() ?? "");
    setMonthlyTokens(agent.monthly_token_quota?.toString() ?? "");
    setQueueLimit(agent.max_queue_depth?.toString() ?? "");
  }, [agent.id]);

  const dirty =
    dailyCost !== (agent.daily_cost_quota?.toString() ?? "") ||
    dailyTokens !== (agent.daily_token_quota?.toString() ?? "") ||
    monthlyCost !== (agent.monthly_cost_quota?.toString() ?? "") ||
    monthlyTokens !== (agent.monthly_token_quota?.toString() ?? "") ||
    queueLimit !== (agent.max_queue_depth?.toString() ?? "");

  const toNullable = (s: string) => s.trim() === "" ? null : Number(s);

  const save = useMutation({
    mutationFn: () => api.put(`/agents/${agent.id}`, {
      daily_cost_quota: toNullable(dailyCost),
      daily_token_quota: toNullable(dailyTokens),
      monthly_cost_quota: toNullable(monthlyCost),
      monthly_token_quota: toNullable(monthlyTokens),
      max_queue_depth: queueLimit.trim() === "" ? null : Math.max(1, parseInt(queueLimit, 10) || 1),
    }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["agent", agent.id] });
      qc.invalidateQueries({ queryKey: ["agents"] });
    },
  });

  return (
    <div style={{
      background: "var(--surface)", border: "1px solid var(--border)",
      borderRadius: 12, padding: 16, marginBottom: 20,
    }}>
      <h3 style={{ margin: 0, marginBottom: 8, fontSize: 14, fontWeight: 800 }}>
        {t("agentBudget.title")}
      </h3>
      <div style={{ fontSize: 11, color: "var(--ink-3)", marginBottom: 14 }}>
        {t("agentBudget.hint")}
      </div>

      {/* Today usage bars */}
      <UsageBar
        label={t("agentBudget.todayCost")}
        used={usage?.today.cost ?? 0}
        cap={toNullable(dailyCost)}
        format={(v) => `$${v.toFixed(2)}`}
      />
      <UsageBar
        label={t("agentBudget.todayTokens")}
        used={usage?.today.tokens ?? 0}
        cap={toNullable(dailyTokens)}
        format={(v) => v.toLocaleString()}
      />

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10, marginTop: 12 }}>
        <NumberField label={t("agentBudget.dailyCost")} value={dailyCost} setValue={setDailyCost} placeholder="e.g. 2" />
        <NumberField label={t("agentBudget.dailyTokens")} value={dailyTokens} setValue={setDailyTokens} placeholder="e.g. 100000" />
        <NumberField label={t("agentBudget.monthlyCost")} value={monthlyCost} setValue={setMonthlyCost} placeholder="e.g. 50" />
        <NumberField label={t("agentBudget.monthlyTokens")} value={monthlyTokens} setValue={setMonthlyTokens} placeholder="e.g. 3000000" />
      </div>

      <div style={{ marginTop: 14, paddingTop: 14, borderTop: "1px dashed var(--border)" }}>
        <NumberField
          label={t("agentBudget.queueLimit")}
          value={queueLimit}
          setValue={setQueueLimit}
          placeholder="e.g. 1440"
        />
        <div style={{ fontSize: 10, color: "var(--ink-4)", marginTop: 4, lineHeight: 1.5 }}>
          {t("agentBudget.queueLimitHint")}
        </div>
      </div>

      <div style={{ marginTop: 12, display: "flex", justifyContent: "flex-end" }}>
        <button className="mbtn primary" disabled={!dirty || save.isPending} onClick={() => save.mutate()}>
          {save.isPending ? t("btn.saving") : t("btn.save")}
        </button>
      </div>
    </div>
  );
}


function NumberField({ label, value, setValue, placeholder }: {
  label: string; value: string; setValue: (s: string) => void; placeholder?: string;
}) {
  return (
    <label style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: 11, color: "var(--ink-3)" }}>
      {label}
      <input
        type="number"
        min={0}
        step="any"
        value={value}
        onChange={(e) => setValue(e.target.value)}
        placeholder={placeholder}
        style={{ padding: "6px 10px", border: "1px solid var(--border)", borderRadius: 6, fontSize: 13 }}
      />
    </label>
  );
}


function UsageBar({ label, used, cap, format }: {
  label: string; used: number; cap: number | null; format: (v: number) => string;
}) {
  const pct = cap ? Math.min(100, (used / cap) * 100) : 0;
  const color = pct >= 100 ? "#e05555" : pct >= 80 ? "#e2a838" : "#6ec2a5";
  return (
    <div style={{ marginBottom: 8 }}>
      <div style={{ display: "flex", justifyContent: "space-between", fontSize: 11, marginBottom: 2 }}>
        <span style={{ color: "var(--ink-3)" }}>{label}</span>
        <span style={{ color: "var(--ink-2)" }}>
          {format(used)}{cap ? ` / ${format(cap)}` : ""}
        </span>
      </div>
      <div style={{
        height: 6, background: "var(--surface-2)",
        borderRadius: 999, overflow: "hidden",
      }}>
        {cap && (
          <div style={{ height: "100%", width: `${pct}%`, background: color, transition: "width .2s" }} />
        )}
      </div>
    </div>
  );
}
