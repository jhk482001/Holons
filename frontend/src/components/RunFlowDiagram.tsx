import { useTranslation } from "react-i18next";
import { useQuery } from "@tanstack/react-query";
import { WorkflowsAPI, WorkflowNode } from "../api/client";
import "./RunFlowDiagram.css";

/**
 * Horizontal workflow flow diagram used at the top of the run detail
 * page. Each workflow node renders as a card; cards are colored by the
 * execution status of the matching run_step (matched by node_position).
 * Clicking a card scrolls the step list below into view.
 *
 * The component is presentation-only — it takes the steps array as a
 * prop and expects the caller to have anchored step rows with
 * `id="step-row-<stepId>"`.
 */

interface StepLite {
  id: number;
  node_position?: number | null;
  agent_id: number | null;
  role_label: string | null;
  duration_ms: number;
  input_tokens: number;
  output_tokens: number;
  cost_usd: number;
  error?: string | null;
  turn?: number | null;
}

export default function RunFlowDiagram({
  workflowId,
  steps,
}: {
  workflowId: number;
  steps: StepLite[];
}) {
  const { t } = useTranslation();
  const { data: workflow, isLoading } = useQuery({
    queryKey: ["workflow", workflowId],
    queryFn: () => WorkflowsAPI.get(workflowId),
    enabled: !isNaN(workflowId),
  });

  if (isLoading) {
    return (
      <div className="run-flow-skel">
        <div className="run-flow-label">{t("runDetail.loadingDiagram")}</div>
      </div>
    );
  }
  if (!workflow || !workflow.nodes || workflow.nodes.length === 0) {
    return null; // nothing to draw
  }

  // Group steps by node_position (multiple turns can share a position)
  const stepsByPosition = new Map<number, StepLite[]>();
  for (const s of steps) {
    if (s.node_position === null || s.node_position === undefined) continue;
    const list = stepsByPosition.get(s.node_position) || [];
    list.push(s);
    stepsByPosition.set(s.node_position, list);
  }

  const sortedNodes: WorkflowNode[] = [...workflow.nodes].sort(
    (a, b) => (a.position ?? 0) - (b.position ?? 0),
  );

  function scrollToStep(stepId: number) {
    const el = document.getElementById(`step-row-${stepId}`);
    if (el) {
      el.scrollIntoView({ behavior: "smooth", block: "start" });
      // Brief highlight flash
      el.classList.add("step-row-flash");
      setTimeout(() => el.classList.remove("step-row-flash"), 1600);
    }
  }

  return (
    <section
      className="run-flow-diagram"
      data-testid="run-flow-diagram"
    >
      <h3 className="run-flow-heading">{t("runDetail.flowDiagram")}</h3>
      <div className="run-flow-row">
        {sortedNodes.map((node, idx) => {
          const nodeSteps = stepsByPosition.get(node.position) || [];
          // Merge info across all run_steps for this node (multi-turn + groups)
          const totalTokens = nodeSteps.reduce(
            (acc, s) => acc + (Number(s.input_tokens) || 0) + (Number(s.output_tokens) || 0),
            0,
          );
          const totalCost = nodeSteps.reduce(
            (acc, s) => acc + (Number(s.cost_usd) || 0),
            0,
          );
          const totalDuration = nodeSteps.reduce(
            (acc, s) => acc + (Number(s.duration_ms) || 0),
            0,
          );
          const hasError = nodeSteps.some((s) => s.error);
          const hasRun = nodeSteps.length > 0;
          const status: "done" | "error" | "pending" = hasError
            ? "error"
            : hasRun
              ? "done"
              : "pending";

          const label =
            node.label ||
            (node.node_type === "group"
              ? node.group?.name || "(group)"
              : nodeSteps[0]?.role_label || `Node ${node.position + 1}`);

          return (
            <div key={node.id} className="run-flow-node-wrap">
              <button
                type="button"
                className={`run-flow-node run-flow-node-${status}`}
                data-testid={`run-flow-node-${node.position}`}
                disabled={!hasRun}
                onClick={() => {
                  if (nodeSteps.length > 0) scrollToStep(nodeSteps[0].id);
                }}
                title={
                  hasRun
                    ? t("runDetail.clickToStep", { id: nodeSteps[0].id })
                    : t("runDetail.notExecuted")
                }
              >
                <div className="run-flow-node-pos">#{node.position + 1}</div>
                <div className="run-flow-node-label">{label}</div>
                {hasRun && (
                  <div className="run-flow-node-stats">
                    <div>
                      <span className="run-flow-stat-label">{t("runDetail.totalCost")}</span>{" "}
                      {(totalDuration / 1000).toFixed(1)}s
                    </div>
                    <div>
                      <span className="run-flow-stat-label">tokens</span>{" "}
                      {totalTokens.toLocaleString()}
                    </div>
                    <div>
                      <span className="run-flow-stat-label">cost</span> $
                      {totalCost.toFixed(4)}
                    </div>
                    {nodeSteps.length > 1 && (
                      <div className="run-flow-turns">{nodeSteps.length} turns</div>
                    )}
                  </div>
                )}
                {!hasRun && (
                  <div className="run-flow-pending">{t("runDetail.notExecuted")}</div>
                )}
              </button>
              {idx < sortedNodes.length - 1 && (
                <div className="run-flow-arrow">→</div>
              )}
            </div>
          );
        })}
      </div>
    </section>
  );
}
