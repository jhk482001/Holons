import { useTranslation } from "react-i18next";
import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { WorkflowsAPI, AgentsAPI, api, Agent } from "../api/client";
import Avatar from "./Avatar";
import "./WorkflowBubble.css";

// STATUS_LABEL is resolved via t() at render time

export default function WorkflowBubble({
  workflowId,
  threadId,
}: {
  workflowId: number;
  threadId?: string;
}) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [status, setStatus] = useState<"idle" | "saving" | "running">("idle");

  const { data: wf } = useQuery({
    queryKey: ["workflow", workflowId],
    queryFn: () => WorkflowsAPI.get(workflowId),
  });
  const { data: agents = [] } = useQuery({
    queryKey: ["agents"],
    queryFn: AgentsAPI.list,
  });

  // Persistent run history: query the backend for any past runs of this
  // workflow so refreshing/returning shows the prior execution state.
  const { data: runs = [] } = useQuery({
    queryKey: ["workflow-runs", workflowId],
    queryFn: () => WorkflowsAPI.runs(workflowId),
    refetchInterval: (query) => {
      const data = query.state.data as Array<{ status: string }> | undefined;
      // Poll faster while any run is still active
      if (data?.some((r) => ["running", "queued", "cancelling", "paused"].includes(r.status))) {
        return 3_000;
      }
      return false;
    },
  });

  const saveDraft = useMutation({
    mutationFn: async () => {
      setStatus("saving");
      await api.put<{ ok: true }>(`/workflows/${workflowId}`, { is_draft: false });
    },
    onSettled: () => {
      setStatus("idle");
      qc.invalidateQueries({ queryKey: ["workflow", workflowId] });
    },
  });

  const runMutation = useMutation({
    mutationFn: async () => {
      setStatus("running");
      const res = await WorkflowsAPI.run(
        workflowId,
        wf?.description || "Run",
        "normal",
        threadId,
      );
      return res;
    },
    onSuccess: () => {
      setStatus("idle");
      qc.invalidateQueries({ queryKey: ["workflow-runs", workflowId] });
      // If we're inside a chat thread, the dispatch also appended messages
      // (請開始{t("wfBubble.runNow")} / 好的 / run_event placeholder). Refresh the message list.
      if (threadId) {
        qc.invalidateQueries({ queryKey: ["messages", threadId] });
      }
    },
    onError: () => setStatus("idle"),
  });

  if (!wf) return null;

  const nodes = wf.nodes || [];
  const agentMap = new Map(agents.map((a: Agent) => [a.id, a]));

  // Estimate cost and tokens very roughly
  const estTokens = nodes.length * 3000;
  const estCost = estTokens * 0.000003;
  const estMinutes = Math.max(1, Math.round(nodes.length * 1.5));

  return (
    <div className="wf-bubble">
      <div className="wf-bubble-label">{t("wfBubble.suggestedWorkflow")}</div>
      <div className="wf-bubble-name">{wf.name}</div>

      <div className="wf-bubble-canvas">
        {nodes.length === 0 ? (
          <div className="wf-bubble-empty">{t("wfBubble.noNodes")}</div>
        ) : (
          nodes.map((n, i) => {
            const agent = n.agent_id ? agentMap.get(n.agent_id) : undefined;
            return (
              <div key={n.id} className="wf-bubble-chain">
                <div className={`wf-bubble-node ${n.node_type}`}>
                  {agent && <Avatar cfg={agent.avatar_config} size={24} title={agent.name} className="wf-mini" />}
                  <div className="wf-node-text">
                    <div className="wf-node-label">{n.label || t("workflows.unnamed")}</div>
                    {agent && <div className="wf-node-agent">{agent.name}</div>}
                  </div>
                </div>
                {i < nodes.length - 1 && <div className="wf-arrow">→</div>}
              </div>
            );
          })
        )}
      </div>

      <div className="wf-bubble-meta">
        <span>
          {t("wfBubble.estimate")} <strong>~{(estTokens / 1000).toFixed(1)}k tokens</strong> ·{" "}
          <strong>{estMinutes} {t("wfBubble.minutes")}</strong> ·{" "}
          <strong>${estCost.toFixed(3)}</strong>
        </span>
        <div className="wf-bubble-actions">
          <button
            className="wf-btn"
            onClick={() => navigate(`/workflows/${workflowId}`)}
            disabled={status !== "idle"}
          >
            {t("wfBubble.edit")}
          </button>
          <button
            className="wf-btn"
            onClick={() => saveDraft.mutate()}
            disabled={status !== "idle" || !wf.is_draft}
            title={wf.is_draft ? t("wfBubble.saveDraft") : t("wfBubble.saved")}
          >
            {wf.is_draft ? t("wfBubble.save") : t("wfBubble.saved")}
          </button>
          <button
            className="wf-btn primary"
            onClick={() => runMutation.mutate()}
            disabled={status !== "idle"}
          >
            {status === "running" ? t("wfBubble.running") : t("wfBubble.runNow")}
          </button>
        </div>
      </div>

      {runs.length > 0 && (
        <div className="wf-run-history">
          <div className="wf-run-history-label">{t("wfBubble.runHistory", { count: runs.length })}</div>
          {runs.slice(0, 5).map((r) => {
            const isActive = ["running", "queued", "cancelling", "paused"].includes(r.status);
            const isDone = r.status === "done";
            const isError = r.status === "error" || r.status === "failed";
            const cls = isActive ? "active" : isDone ? "done" : isError ? "error" : "neutral";
            return (
              <button
                key={r.id}
                type="button"
                className={`wf-run-row ${cls}`}
                onClick={() => navigate(`/runs/${r.id}`)}
              >
                <span className="wf-run-id">#{r.id}</span>
                <span className={`wf-run-pill ${cls}`}>
                  {t(`runs.statusLabels.${r.status}`, r.status)}
                </span>
                <span className="wf-run-time">
                  {new Date(r.started_at).toLocaleString("zh-TW", {
                    month: "numeric", day: "numeric",
                    hour: "2-digit", minute: "2-digit",
                  })}
                </span>
                <span className="wf-run-go">{t("wfBubble.viewRun")}</span>
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
