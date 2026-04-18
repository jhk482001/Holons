import { useTranslation } from "react-i18next";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, WorkflowsAPI } from "../api/client";
import Modal from "../components/Modal";

interface Schedule {
  id: number;
  name: string;
  workflow_id: number;
  trigger_type: string;
  cron_expression: string | null;
  interval_seconds: number | null;
  default_input: string | null;
  priority: string;
  enabled: boolean;
  next_run_at: string | null;
  last_run_at: string | null;
}

const INTERVAL_PRESETS = [
  { labelKey: "schedules.every5min", value: 300 },
  { labelKey: "schedules.every30min", value: 1800 },
  { labelKey: "schedules.everyHour", value: 3600 },
  { labelKey: "schedules.every6h", value: 21600 },
  { labelKey: "schedules.everyDay", value: 86400 },
];

export default function Schedules() {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const { data: schedules = [] } = useQuery({
    queryKey: ["schedules"],
    queryFn: () => api.get<Schedule[]>("/schedules"),
    refetchInterval: 10_000,
  });
  const [createOpen, setCreateOpen] = useState(false);
  const [toDelete, setToDelete] = useState<Schedule | null>(null);

  const toggle = useMutation({
    mutationFn: ({ id, enabled }: { id: number; enabled: boolean }) =>
      api.post(`/schedules/${id}/toggle`, { enabled }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["schedules"] }),
  });

  const del = useMutation({
    mutationFn: (id: number) => api.del(`/schedules/${id}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["schedules"] });
      setToDelete(null);
    },
  });

  return (
    <div className="page">
      <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", marginBottom: 4 }}>
        <div>
          <h1>{t("schedules.title")}</h1>
          <div className="subtitle">{t("schedules.subtitle")}</div>
        </div>
        <button
          data-testid="new-schedule-btn"
          onClick={() => setCreateOpen(true)}
          style={{
            padding: "10px 18px",
            background: "var(--accent)",
            color: "white",
            border: "1px solid var(--accent)",
            borderRadius: 10,
            fontSize: 13,
            fontWeight: 800,
            cursor: "pointer",
          }}
        >
          {t("schedules.createNew")}
        </button>
      </div>

      <div style={{ display: "flex", flexDirection: "column", gap: 10, marginTop: 20 }}>
        {schedules.map((s) => (
          <div
            key={s.id}
            data-testid={`schedule-row-${s.id}`}
            style={{
              background: "var(--surface)",
              border: "1px solid var(--border)",
              borderRadius: 14,
              padding: "16px 20px",
              display: "flex",
              alignItems: "center",
              gap: 16,
            }}
          >
            <div style={{ flex: 1 }}>
              <div style={{ fontSize: 14, fontWeight: 700 }}>{s.name || `Schedule #${s.id}`}</div>
              <div style={{ fontSize: 11, color: "var(--ink-3)", marginTop: 2 }}>
                {s.trigger_type === "interval"
                  ? `${formatInterval(s.interval_seconds || 0)}`
                  : s.trigger_type === "cron"
                    ? `cron: ${s.cron_expression}`
                    : s.trigger_type}
                {" · "}{t("schedules.priority")}{" "}{s.priority}
                {" · workflow #"}{s.workflow_id}
              </div>
            </div>
            <div
              data-testid={`schedule-status-${s.id}`}
              style={{
                fontSize: 11, fontWeight: 700,
                padding: "4px 10px",
                borderRadius: 999,
                background: s.enabled ? "var(--good-soft)" : "var(--surface-2)",
                color: s.enabled ? "var(--good)" : "var(--ink-3)",
              }}
            >
              {s.enabled ? t("schedules.enabled") : t("schedules.disabled")}
            </div>
            <div style={{ fontSize: 10, color: "var(--ink-4)", minWidth: 150, textAlign: "right" }}>
              {s.next_run_at && <>{t("schedules.nextRun")}: {new Date(s.next_run_at).toLocaleString()}</>}
            </div>
            <button
              data-testid={`toggle-schedule-${s.id}`}
              onClick={() => toggle.mutate({ id: s.id, enabled: !s.enabled })}
              className="mbtn"
              style={{ padding: "6px 12px", fontSize: 11 }}
            >
              {s.enabled ? t("schedules.disable") : t("schedules.enable")}
            </button>
            <button
              data-testid={`delete-schedule-${s.id}`}
              onClick={() => setToDelete(s)}
              className="mbtn danger"
              style={{ padding: "6px 12px", fontSize: 11 }}
            >{t("btn.delete")}</button>
          </div>
        ))}
        {schedules.length === 0 && (
          <div style={{ textAlign: "center", color: "var(--ink-4)", padding: 60 }}>
            {t("schedules.empty")}
          </div>
        )}
      </div>

      <CreateScheduleModal
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        onCreated={() => setCreateOpen(false)}
      />

      <Modal
        open={!!toDelete}
        onClose={() => setToDelete(null)}
        title={t("schedules.deleteTitle")}
        subtitle={toDelete?.name || ""}
        size="sm"
        footer={
          <>
            <button className="mbtn" onClick={() => setToDelete(null)} disabled={del.isPending}>{t("btn.cancel")}</button>
            <button
              className="mbtn danger"
              data-testid="confirm-delete-schedule"
              onClick={() => toDelete && del.mutate(toDelete.id)}
              disabled={del.isPending}
            >
              {del.isPending ? t("workflows.deleting") : t("agentDetail.deleteSubmit")}
            </button>
          </>
        }
      >
        <div style={{ fontSize: 13, color: "var(--ink-2)", lineHeight: 1.6 }}>
          {t("schedules.deleteDesc")}
        </div>
      </Modal>
    </div>
  );
}

function formatInterval(seconds: number): string {
  if (seconds >= 86400) return `${(seconds / 86400).toFixed(0)} days`;
  if (seconds >= 3600) return `${(seconds / 3600).toFixed(0)} hours`;
  if (seconds >= 60) return `${(seconds / 60).toFixed(0)} min`;
  return `${seconds}s`;
}

function CreateScheduleModal({
  open,
  onClose,
  onCreated,
}: {
  open: boolean;
  onClose: () => void;
  onCreated: () => void;
}) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const { data: workflows = [] } = useQuery({
    queryKey: ["workflows"],
    queryFn: () => WorkflowsAPI.list(),
    enabled: open,
  });

  const [name, setName] = useState("");
  const [workflowId, setWorkflowId] = useState<number | null>(null);
  const [triggerType, setTriggerType] = useState<"interval" | "cron">("interval");
  const [intervalSeconds, setIntervalSeconds] = useState(3600);
  const [cronExpression, setCronExpression] = useState("0 * * * *");
  const [priority, setPriority] = useState("normal");
  const [defaultInput, setDefaultInput] = useState("");

  const create = useMutation({
    mutationFn: () =>
      api.post<{ id: number }>("/schedules", {
        workflow_id: workflowId,
        name,
        trigger_type: triggerType,
        interval_seconds: triggerType === "interval" ? intervalSeconds : null,
        cron_expression: triggerType === "cron" ? cronExpression : null,
        priority,
        default_input: defaultInput || null,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["schedules"] });
      setName("");
      setWorkflowId(null);
      setTriggerType("interval");
      setIntervalSeconds(3600);
      setCronExpression("0 * * * *");
      setPriority("normal");
      setDefaultInput("");
      onCreated();
    },
  });

  const canSubmit = !!workflowId && name.trim().length > 0 && !create.isPending;

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={t("schedules.createTitle")}
      subtitle={t("schedules.createSubtitle")}
      size="md"
      footer={
        <>
          <button className="mbtn" onClick={onClose} disabled={create.isPending}>{t("btn.cancel")}</button>
          <button
            className="mbtn primary"
            data-testid="create-schedule-submit"
            onClick={() => create.mutate()}
            disabled={!canSubmit}
          >
            {create.isPending ? t("schedules.creating") : t("schedules.createSubmit")}
          </button>
        </>
      }
    >
      <div className="modal-field">
        <label>{t("schedules.nameLabel")}</label>
        <input
          data-testid="new-schedule-name"
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder={t("schedules.namePlaceholder")}
          autoFocus
        />
      </div>
      <div className="modal-field">
        <label>{t("schedules.workflowLabel")}</label>
        <select
          data-testid="new-schedule-workflow"
          value={workflowId ?? ""}
          onChange={(e) => setWorkflowId(e.target.value ? Number(e.target.value) : null)}
        >
          <option value="">{t("schedules.selectWorkflow")}</option>
          {workflows.map((w) => (
            <option key={w.id} value={w.id}>{w.name || `#${w.id}`}</option>
          ))}
        </select>
      </div>
      <div className="modal-field">
        <label>{t("schedules.triggerType")}</label>
        <div style={{ display: "flex", gap: 8 }}>
          <button
            type="button"
            className={`mbtn ${triggerType === "interval" ? "primary" : ""}`}
            onClick={() => setTriggerType("interval")}
            style={{ flex: 1 }}
          >
            {t("schedules.interval")}
          </button>
          <button
            type="button"
            className={`mbtn ${triggerType === "cron" ? "primary" : ""}`}
            onClick={() => setTriggerType("cron")}
            style={{ flex: 1 }}
          >
            Cron
          </button>
        </div>
      </div>
      {triggerType === "interval" ? (
        <div className="modal-field">
          <label>{t("schedules.intervalLabel")}</label>
          <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginBottom: 6 }}>
            {INTERVAL_PRESETS.map((p) => (
              <button
                key={p.value}
                type="button"
                onClick={() => setIntervalSeconds(p.value)}
                className={`mbtn ${intervalSeconds === p.value ? "primary" : ""}`}
                style={{ padding: "4px 10px", fontSize: 10 }}
              >
                {t(p.labelKey)}
              </button>
            ))}
          </div>
          <input
            type="number"
            min={60}
            value={intervalSeconds}
            onChange={(e) => setIntervalSeconds(Number(e.target.value))}
          />
          <div className="hint">{t("schedules.secondsHint")}</div>
        </div>
      ) : (
        <div className="modal-field">
          <label>{t("schedules.cronLabel")}</label>
          <input
            value={cronExpression}
            onChange={(e) => setCronExpression(e.target.value)}
            placeholder="0 * * * *"
            style={{ fontFamily: "var(--font-mono)" }}
          />
          <div className="hint">{t("schedules.cronHint")}</div>
        </div>
      )}
      <div className="modal-field">
        <label>{t("schedules.priorityLabel")}</label>
        <select value={priority} onChange={(e) => setPriority(e.target.value)}>
          <option value="low">{t("schedules.priorityLow")}</option>
          <option value="normal">{t("schedules.priorityNormal")}</option>
          <option value="high">{t("schedules.priorityHigh")}</option>
          <option value="critical">{t("schedules.priorityCritical")}</option>
        </select>
      </div>
      <div className="modal-field">
        <label>{t("schedules.defaultInput")}</label>
        <textarea
          value={defaultInput}
          onChange={(e) => setDefaultInput(e.target.value)}
          placeholder={t("schedules.defaultInputPlaceholder")}
        />
      </div>
    </Modal>
  );
}
