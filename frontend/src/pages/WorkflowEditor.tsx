import { useTranslation } from "react-i18next";
import { useEffect, useMemo, useRef, useState, useCallback } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, WorkflowsAPI, AgentsAPI, Agent, WorkflowNode } from "../api/client";
import Avatar from "../components/Avatar";
import Modal from "../components/Modal";
import "./WorkflowEditor.css";

export default function WorkflowEditor() {
  const { t } = useTranslation();
  const { id } = useParams<{ id: string }>();
  const wfId = Number(id);
  const navigate = useNavigate();
  const qc = useQueryClient();

  const { data: wf } = useQuery({
    queryKey: ["workflow", wfId],
    queryFn: () => WorkflowsAPI.get(wfId),
    enabled: !isNaN(wfId),
  });
  const { data: agents = [] } = useQuery({ queryKey: ["agents"], queryFn: AgentsAPI.list });

  const [nameDraft, setNameDraft] = useState("");
  const [loopDraft, setLoopDraft] = useState(false);
  const [maxLoopsDraft, setMaxLoopsDraft] = useState(1);
  const [dirty, setDirty] = useState(false);

  useEffect(() => {
    if (wf) {
      setNameDraft(wf.name);
      setLoopDraft(wf.loop_enabled);
      setMaxLoopsDraft(wf.max_loops);
      setDirty(false);
    }
  }, [wf?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  const save = useMutation({
    mutationFn: () =>
      api.put(`/workflows/${wfId}`, {
        name: nameDraft,
        loop_enabled: loopDraft,
        max_loops: maxLoopsDraft,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["workflow", wfId] });
      qc.invalidateQueries({ queryKey: ["workflows"] });
      setDirty(false);
    },
  });

  const toggleTemplate = useMutation({
    mutationFn: (isTemplate: boolean) =>
      api.put(`/workflows/${wfId}`, { is_template: isTemplate }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["workflow", wfId] });
      qc.invalidateQueries({ queryKey: ["workflows"] });
    },
  });

  const [addNodeOpen, setAddNodeOpen] = useState(false);
  const [runOpen, setRunOpen] = useState(false);
  const [runInput, setRunInput] = useState("");

  const runWorkflow = useMutation({
    mutationFn: (input: string) => WorkflowsAPI.run(wfId, input),
    onSuccess: ({ run_id }) => {
      setRunOpen(false);
      setRunInput("");
      navigate(`/runs/${run_id}`);
    },
  });

  const addNode = useMutation({
    mutationFn: (agentId: number) => {
      const agent = agents.find((a) => a.id === agentId);
      return api.post<{ id: number }>(`/workflows/${wfId}/nodes`, {
        node_type: "agent",
        agent_id: agentId,
        label: agent?.name || `Agent ${agentId}`,
        prompt_template: "",
      });
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["workflow", wfId] });
      setAddNodeOpen(false);
    },
  });

  const deleteNode = useMutation({
    mutationFn: (nid: number) => api.del(`/workflows/${wfId}/nodes/${nid}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["workflow", wfId] });
      setSelectedId(null);
    },
  });

  const updateNode = useMutation({
    mutationFn: ({ nid, patch }: { nid: number; patch: Partial<WorkflowNode> }) =>
      api.put(`/workflows/${wfId}/nodes/${nid}`, patch),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["workflow", wfId] }),
  });

  const autoLayout = useMutation({
    mutationFn: async () => {
      // Sort by current position, re-space them in a horizontal row
      const sorted = [...rawNodes].sort((a, b) => a.position - b.position);
      for (let i = 0; i < sorted.length; i++) {
        const n = sorted[i];
        const targetX = 120 + i * 320;
        const targetY = 200;
        if (n.pos_x !== targetX || n.pos_y !== targetY) {
          await api.put(`/workflows/${wfId}/nodes/${n.id}`, {
            pos_x: targetX,
            pos_y: targetY,
          });
        }
      }
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["workflow", wfId] });
      setTimeout(fitToContent, 100);
    },
  });

  const rawNodes = wf?.nodes || [];
  const [selectedId, setSelectedId] = useState<number | null>(null);

  // Local position overrides while dragging (by node id)
  const [localPos, setLocalPos] = useState<Record<number, { x: number; y: number }>>({});
  const nodes = rawNodes.map((n) => {
    const l = localPos[n.id];
    return l ? { ...n, pos_x: l.x, pos_y: l.y } : n;
  });

  const reorder = useMutation({
    mutationFn: (nodeIds: number[]) =>
      api.post(`/workflows/${wfId}/nodes/reorder`, { node_ids: nodeIds }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["workflow", wfId] }),
  });

  // Node drag state
  const [draggingId, setDraggingId] = useState<number | null>(null);
  const dragStart = useRef<{
    clientX: number;
    clientY: number;
    origX: number;
    origY: number;
    moved: boolean;
  } | null>(null);

  // Pan / zoom state
  const wrapRef = useRef<HTMLDivElement>(null);
  const [transform, setTransform] = useState({ x: 0, y: 0, k: 0.8 });
  const [isPanning, setIsPanning] = useState(false);
  const panStart = useRef<{ x: number; y: number; tx: number; ty: number } | null>(null);

  const onNodeMouseDown = (nid: number, e: React.MouseEvent) => {
    e.stopPropagation();
    if (e.button !== 0) return;
    const node = rawNodes.find((n) => n.id === nid);
    if (!node) return;
    setDraggingId(nid);
    dragStart.current = {
      clientX: e.clientX,
      clientY: e.clientY,
      origX: node.pos_x,
      origY: node.pos_y,
      moved: false,
    };
  };

  useEffect(() => {
    if (draggingId == null) return;
    const onMove = (e: MouseEvent) => {
      const s = dragStart.current;
      if (!s) return;
      const dx = (e.clientX - s.clientX) / transform.k;
      const dy = (e.clientY - s.clientY) / transform.k;
      if (!s.moved && Math.hypot(e.clientX - s.clientX, e.clientY - s.clientY) > 4) {
        s.moved = true;
      }
      setLocalPos((prev) => ({
        ...prev,
        [draggingId]: { x: Math.round(s.origX + dx), y: Math.round(s.origY + dy) },
      }));
    };
    const onUp = () => {
      const s = dragStart.current;
      const nid = draggingId;
      setDraggingId(null);
      dragStart.current = null;
      if (!s) return;
      if (!s.moved) {
        // Treat as click — select the node
        setSelectedId(nid);
        setLocalPos((prev) => {
          const next = { ...prev };
          delete next[nid!];
          return next;
        });
        return;
      }
      const pos = localPos[nid!];
      if (!pos) return;
      // Persist new pos_x/pos_y for this node
      updateNode.mutate({ nid: nid!, patch: { pos_x: pos.x, pos_y: pos.y } });
      // Also: re-sort positions by x-coordinate so run order follows layout
      const sorted = [...rawNodes.map((n) => ({ ...n, pos_x: n.id === nid ? pos.x : n.pos_x }))]
        .sort((a, b) => a.pos_x - b.pos_x);
      const newOrder = sorted.map((n) => n.id);
      const oldOrder = [...rawNodes].sort((a, b) => a.position - b.position).map((n) => n.id);
      if (JSON.stringify(newOrder) !== JSON.stringify(oldOrder)) {
        reorder.mutate(newOrder);
      }
      // Clear local override after mutation resolves
      setTimeout(() => {
        setLocalPos((prev) => {
          const next = { ...prev };
          delete next[nid!];
          return next;
        });
      }, 400);
    };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
    return () => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [draggingId, transform.k, rawNodes, localPos]);

  // Wheel zoom
  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const rect = el.getBoundingClientRect();
      const cx = e.clientX - rect.left;
      const cy = e.clientY - rect.top;
      setTransform((t) => {
        const bx = (cx - t.x) / t.k;
        const by = (cy - t.y) / t.k;
        const speed = e.ctrlKey ? 0.015 : 0.004;
        const factor = Math.exp(-e.deltaY * speed);
        const newK = Math.max(0.25, Math.min(2.5, t.k * factor));
        if (newK === t.k) return t;
        return { x: cx - bx * newK, y: cy - by * newK, k: newK };
      });
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, []);

  // Pan with mouse drag
  const onMouseDown = (e: React.MouseEvent) => {
    const target = e.target as HTMLElement;
    if (target.closest(".wf-node")) return;
    if (target.closest(".side-panel")) return;
    if (e.button !== 0) return;
    setIsPanning(true);
    panStart.current = { x: e.clientX, y: e.clientY, tx: transform.x, ty: transform.y };
    e.preventDefault();
  };

  useEffect(() => {
    if (!isPanning) return;
    const onMove = (e: MouseEvent) => {
      if (!panStart.current) return;
      const s = panStart.current;
      setTransform((t) => ({ ...t, x: s.tx + (e.clientX - s.x), y: s.ty + (e.clientY - s.y) }));
    };
    const onUp = () => setIsPanning(false);
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
    return () => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
  }, [isPanning]);

  // Layout constants used by both the canvas render and fitToContent
  const NODE_W = 240;
  const NODE_H = 120;
  const MEMBER_W = 200;
  const MEMBER_H = 70;
  const MEMBER_GAP_Y = 14;
  const HUB_OFFSET_X = 30;

  type GeoNode = {
    n: WorkflowNode;
    x: number; y: number; w: number; h: number;
    inX: number; inY: number;
    outX: number; outY: number;
    members?: Array<{
      memberId: number;
      agentId: number;
      name: string;
      roleTitle: string | null;
      avatarCfg: Record<string, string>;
      x: number; y: number; w: number; h: number;
    }>;
  };

  const geos: GeoNode[] = useMemo(() => {
    return nodes.map((n) => {
      if (n.node_type === "group" && n.group?.members?.length) {
        const m = n.group.members;
        const memberCount = m.length;
        const totalH = memberCount * MEMBER_H + (memberCount - 1) * MEMBER_GAP_Y;
        const hubLeftX = n.pos_x;
        const membersX = hubLeftX + HUB_OFFSET_X + 40;
        const startY = n.pos_y - totalH / 2 + NODE_H / 2;
        const w = HUB_OFFSET_X + 40 + MEMBER_W + 40 + HUB_OFFSET_X;
        const h = Math.max(NODE_H, totalH + 40);
        return {
          n,
          x: hubLeftX,
          y: n.pos_y + NODE_H / 2 - h / 2,
          w,
          h,
          inX: hubLeftX,
          inY: n.pos_y + NODE_H / 2,
          outX: hubLeftX + w,
          outY: n.pos_y + NODE_H / 2,
          members: m.map((mm, i) => ({
            memberId: mm.id,
            agentId: mm.agent_id,
            name: mm.agent_name,
            roleTitle: mm.role_title,
            avatarCfg: mm.avatar_config || {},
            x: membersX,
            y: startY + i * (MEMBER_H + MEMBER_GAP_Y),
            w: MEMBER_W,
            h: MEMBER_H,
          })),
        };
      }
      return {
        n,
        x: n.pos_x,
        y: n.pos_y,
        w: NODE_W,
        h: NODE_H,
        inX: n.pos_x,
        inY: n.pos_y + NODE_H / 2,
        outX: n.pos_x + NODE_W,
        outY: n.pos_y + NODE_H / 2,
      };
    });
  }, [nodes]);

  const fitToContent = useCallback(() => {
    if (!wrapRef.current || geos.length === 0) return;
    const wrap = wrapRef.current.getBoundingClientRect();
    const minX = Math.min(...geos.map((g) => g.x));
    const maxX = Math.max(...geos.map((g) => g.x + g.w));
    const minY = Math.min(...geos.map((g) => g.y));
    const maxY = Math.max(...geos.map((g) => g.y + g.h));
    const cw = maxX - minX;
    const ch = maxY - minY;
    const padding = 80;
    const k = Math.min(
      (wrap.width - padding * 2) / cw,
      (wrap.height - padding * 2) / ch,
      1.5
    );
    const cx = (minX + maxX) / 2;
    const cy = (minY + maxY) / 2;
    setTransform({
      x: wrap.width / 2 - cx * k,
      y: wrap.height / 2 - cy * k,
      k: Math.max(0.25, k),
    });
  }, [geos]);

  // Auto-fit on first node load only. Previously this depended on
  // `fitToContent` (whose ref changes whenever `geos` changes) which
  // meant every zoom/pan triggered an auto-fit that clobbered the user's
  // view. Use a ref-based one-shot flag so the auto-fit only fires the
  // first time nodes actually show up.
  const didFitRef = useRef(false);
  useEffect(() => {
    if (didFitRef.current) return;
    if (nodes.length === 0) return;
    didFitRef.current = true;
    // Defer to next tick so the canvas-wrap has its final size
    setTimeout(() => fitToContent(), 50);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nodes.length]);

  const selectedNode = nodes.find((n) => n.id === selectedId);
  const selectedAgent = selectedNode?.agent_id ? agents.find((a) => a.id === selectedNode.agent_id) : undefined;

  return (
    <div className="wf-editor">
      <div className="toolbar">
        <button className="tb-btn" onClick={() => navigate("/workflows")}>
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M19 12H5M12 19l-7-7 7-7"/>
          </svg>
          {t("workflowEditor.back")}
        </button>
        <input
          className="tb-name"
          data-testid="wf-name-input"
          value={nameDraft}
          onChange={(e) => { setNameDraft(e.target.value); setDirty(true); }}
        />

        <label className="tb-loop" style={{ display: "flex", alignItems: "center", gap: 6, cursor: "pointer" }}>
          <input
            type="checkbox"
            checked={loopDraft}
            onChange={(e) => { setLoopDraft(e.target.checked); setDirty(true); }}
          />
          Loop
          {loopDraft && (
            <input
              type="number"
              min={1}
              max={10}
              value={maxLoopsDraft}
              onChange={(e) => { setMaxLoopsDraft(Number(e.target.value)); setDirty(true); }}
              style={{ width: 48, marginLeft: 4 }}
            />
          )}
        </label>

        <label className="tb-loop" style={{ display: "flex", alignItems: "center", gap: 6, cursor: "pointer" }}>
          <input
            type="checkbox"
            data-testid="wf-is-template"
            checked={!!wf?.is_template}
            onChange={(e) => toggleTemplate.mutate(e.target.checked)}
            disabled={toggleTemplate.isPending}
          />
          {t("workflowEditor.template")}
        </label>

        <div className="tb-spacer"></div>
        <button
          className="tb-btn"
          data-testid="wf-add-node-btn"
          onClick={() => setAddNodeOpen(true)}
        >
          {t("workflowEditor.addNode")}
        </button>
        <button
          className="tb-btn"
          data-testid="wf-auto-layout-btn"
          onClick={() => autoLayout.mutate()}
          disabled={autoLayout.isPending || nodes.length === 0}
          
        >
          {autoLayout.isPending ? t("workflowEditor.autoLayouting") : t("workflowEditor.autoLayout")}
        </button>
        <button
          className="tb-btn primary"
          data-testid="wf-save-btn"
          onClick={() => save.mutate()}
          disabled={!dirty || save.isPending}
        >
          {save.isPending ? t("btn.saving") : dirty ? t("btn.save") : t("btn.saved")}
        </button>
        <button
          className="tb-btn run"
          data-testid="wf-run-btn"
          onClick={() => setRunOpen(true)}
          disabled={nodes.length === 0}
          title={nodes.length === 0 ? t("workflowEditor.noNodesRun") : t("workflowEditor.runTitle")}
        >
          {t("workflowEditor.run")}
        </button>
      </div>

      <div className="canvas-wrap" ref={wrapRef} onMouseDown={onMouseDown}>
        <div
          className="canvas"
          style={{
            transform: `translate(${transform.x}px, ${transform.y}px) scale(${transform.k})`,
          }}
        >
          {(() => {
            return (
              <>
                <svg className="connections">
                  <defs>
                    <marker id="arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto">
                      <path d="M0 0 L10 5 L0 10 z" fill="#d4cdbc" />
                    </marker>
                  </defs>
                  {geos.map((g, i) => {
                    if (i === geos.length - 1) return null;
                    const next = geos[i + 1];
                    const ax = g.outX, ay = g.outY;
                    const bx = next.inX, by = next.inY;
                    const dx = Math.max(40, Math.abs(bx - ax) / 2);
                    return (
                      <path
                        key={`c-${g.n.id}`}
                        d={`M ${ax} ${ay} C ${ax + dx} ${ay}, ${bx - dx} ${by}, ${bx} ${by}`}
                        stroke="#d4cdbc"
                        strokeWidth="2"
                        fill="none"
                        markerEnd="url(#arrow)"
                      />
                    );
                  })}
                  {/* Internal fan-out and fan-in for group nodes */}
                  {geos.flatMap((g) => {
                    if (!g.members) return [];
                    const hubInX = g.inX;
                    const hubInY = g.inY;
                    const hubOutX = g.outX;
                    const hubOutY = g.outY;
                    return g.members.flatMap((m) => {
                      const mInX = m.x;
                      const mInY = m.y + m.h / 2;
                      const mOutX = m.x + m.w;
                      const mOutY = m.y + m.h / 2;
                      const dx1 = Math.max(20, (mInX - hubInX) / 2);
                      const dx2 = Math.max(20, (hubOutX - mOutX) / 2);
                      return [
                        <path
                          key={`fan-in-${g.n.id}-${m.memberId}`}
                          d={`M ${hubInX} ${hubInY} C ${hubInX + dx1} ${hubInY}, ${mInX - dx1} ${mInY}, ${mInX} ${mInY}`}
                          stroke="#d4cdbc"
                          strokeWidth="1.6"
                          fill="none"
                        />,
                        <path
                          key={`fan-out-${g.n.id}-${m.memberId}`}
                          d={`M ${mOutX} ${mOutY} C ${mOutX + dx2} ${mOutY}, ${hubOutX - dx2} ${hubOutY}, ${hubOutX} ${hubOutY}`}
                          stroke="#d4cdbc"
                          strokeWidth="1.6"
                          fill="none"
                        />,
                      ];
                    });
                  })}
                  {/* Hub dots for groups */}
                  {geos.map((g) => {
                    if (!g.members) return null;
                    return (
                      <g key={`hub-${g.n.id}`}>
                        <circle cx={g.inX} cy={g.inY} r="6" fill="#ff7a59" stroke="white" strokeWidth="2" />
                        <circle cx={g.outX} cy={g.outY} r="6" fill="#ff7a59" stroke="white" strokeWidth="2" />
                      </g>
                    );
                  })}
                </svg>

                {geos.map((g) => {
                  if (g.members) {
                    return (
                      <GroupContainer
                        key={g.n.id}
                        node={g.n}
                        x={g.x}
                        y={g.y}
                        w={g.w}
                        h={g.h}
                        selected={selectedId === g.n.id}
                        dragging={draggingId === g.n.id}
                        onMouseDown={(e) => onNodeMouseDown(g.n.id, e)}
                        members={g.members}
                      />
                    );
                  }
                  return (
                    <NodeCard
                      key={g.n.id}
                      node={g.n}
                      agent={agents.find((a) => a.id === g.n.agent_id)}
                      selected={selectedId === g.n.id}
                      dragging={draggingId === g.n.id}
                      onMouseDown={(e) => onNodeMouseDown(g.n.id, e)}
                    />
                  );
                })}
              </>
            );
          })()}
        </div>

        <div className="zoom-controls">
          <button onClick={() => setTransform((t) => ({ ...t, k: Math.min(2.5, t.k * 1.2) }))}>+</button>
          <div className="zoom-level">{Math.round(transform.k * 100)}%</div>
          <button onClick={() => setTransform((t) => ({ ...t, k: Math.max(0.25, t.k / 1.2) }))}>−</button>
          <button onClick={fitToContent} title={t("workflowEditor.fitToScreen")}>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M8 3H5a2 2 0 0 0-2 2v3"/>
              <path d="M21 8V5a2 2 0 0 0-2-2h-3"/>
              <path d="M3 16v3a2 2 0 0 0 2 2h3"/>
              <path d="M16 21h3a2 2 0 0 0 2-2v-3"/>
            </svg>
          </button>
        </div>

        {selectedNode && (
          <aside className="side-panel" data-testid="wf-side-panel">
            <div className="side-head">
              <div>
                <div className="side-type">{t("workflowEditor.nodeType", { type: selectedNode.node_type.toUpperCase() })}</div>
                <h3>{selectedNode.label || t("workflows.unnamed")}</h3>
              </div>
              <button className="side-close" onClick={() => setSelectedId(null)}>×</button>
            </div>
            <div className="side-body">
              {selectedAgent && (
                <div className="side-field">
                  <label>{t("workflowEditor.assignedAgent")}</label>
                  <div className="agent-select-row">
                    <Avatar cfg={selectedAgent.avatar_config} size={40} title={selectedAgent.name} className="av" />
                    <div>
                      <div className="nm-strong">{selectedAgent.name}</div>
                      <div className="nm-small">{selectedAgent.role_title}</div>
                    </div>
                  </div>
                </div>
              )}
              <div className="side-field">
                <label>{t("workflowEditor.nodeLabel")}</label>
                <input
                  type="text"
                  data-testid="wf-node-label-input"
                  key={selectedNode.id}
                  defaultValue={selectedNode.label || ""}
                  onBlur={(e) => {
                    if (e.target.value !== (selectedNode.label || "")) {
                      updateNode.mutate({ nid: selectedNode.id, patch: { label: e.target.value } });
                    }
                  }}
                />
              </div>
              <div className="side-field">
                <label>{t("workflowEditor.promptTemplate")}</label>
                <textarea
                  key={`p-${selectedNode.id}`}
                  defaultValue={selectedNode.prompt_template || ""}
                  onBlur={(e) => {
                    if (e.target.value !== (selectedNode.prompt_template || "")) {
                      updateNode.mutate({ nid: selectedNode.id, patch: { prompt_template: e.target.value } });
                    }
                  }}
                />
              </div>
              <div className="side-field">
                <label>{t("workflowEditor.systemPromptOverride")}</label>
                <textarea
                  key={`spo-${selectedNode.id}`}
                  defaultValue={(selectedNode as any).system_prompt_override || ""}
                  placeholder={t("workflowEditor.systemPromptOverridePlaceholder")}
                  onBlur={(e) => {
                    if (e.target.value !== ((selectedNode as any).system_prompt_override || "")) {
                      updateNode.mutate({ nid: selectedNode.id, patch: { system_prompt_override: e.target.value || null } });
                    }
                  }}
                  style={{ minHeight: 80 }}
                />
                <div className="hint">{t("workflowEditor.systemPromptOverrideHint")}</div>
              </div>
              <div style={{ marginTop: 20, paddingTop: 16, borderTop: "1px solid var(--border)" }}>
                <button
                  data-testid="wf-delete-node-btn"
                  onClick={() => deleteNode.mutate(selectedNode.id)}
                  disabled={deleteNode.isPending}
                  style={{
                    width: "100%",
                    padding: "8px 14px",
                    background: "white",
                    color: "var(--danger)",
                    border: "1px solid rgba(232, 100, 80, 0.4)",
                    borderRadius: 8,
                    fontSize: 12,
                    fontWeight: 700,
                    cursor: "pointer",
                  }}
                >
                  {deleteNode.isPending ? t("workflowEditor.deletingNode") : t("workflowEditor.deleteNode")}
                </button>
              </div>
            </div>
          </aside>
        )}
      </div>

      <AddNodeModal
        open={addNodeOpen}
        onClose={() => setAddNodeOpen(false)}
        agents={agents.filter((a) => !a.is_lead)}
        onPick={(agentId) => addNode.mutate(agentId)}
        pending={addNode.isPending}
      />

      <Modal
        open={runOpen}
        onClose={() => setRunOpen(false)}
        title={t("workflowEditor.runWorkflow")}
        subtitle={t("workflowEditor.runSubtitle")}
        size="md"
        footer={
          <>
            <button className="mbtn" onClick={() => setRunOpen(false)} disabled={runWorkflow.isPending}>
              {t("btn.cancel")}
            </button>
            <button
              className="mbtn primary"
              data-testid="wf-run-submit"
              onClick={() => runWorkflow.mutate(runInput)}
              disabled={runWorkflow.isPending}
            >
              {runWorkflow.isPending ? t("workflowEditor.dispatching") : t("workflowEditor.startRun")}
            </button>
          </>
        }
      >
        <div className="modal-field">
          <label>{t("workflowEditor.initialInput")}</label>
          <textarea
            data-testid="wf-run-input"
            value={runInput}
            onChange={(e) => setRunInput(e.target.value)}
            placeholder={t("workflowEditor.inputPlaceholder")}
            autoFocus
          />
        </div>
      </Modal>
    </div>
  );
}

function AddNodeModal({
  open,
  onClose,
  agents,
  onPick,
  pending,
}: {
  open: boolean;
  onClose: () => void;
  agents: Agent[];
  onPick: (agentId: number) => void;
  pending: boolean;
}) {
  const { t } = useTranslation();
  return (
    <Modal
      open={open}
      onClose={onClose}
      title={t("workflowEditor.addNodeTitle")}
      subtitle={t("workflowEditor.addNodeSubtitle")}
      size="md"
    >
      <div style={{
        display: "grid",
        gridTemplateColumns: "repeat(auto-fill, minmax(180px, 1fr))",
        gap: 10,
      }}>
        {agents.map((a) => (
          <button
            key={a.id}
            data-testid={`pick-agent-${a.id}`}
            disabled={pending}
            onClick={() => onPick(a.id)}
            style={{
              display: "flex",
              alignItems: "center",
              gap: 10,
              padding: 12,
              background: "white",
              border: "1px solid var(--border)",
              borderRadius: 12,
              cursor: "pointer",
              textAlign: "left",
            }}
          >
            <Avatar cfg={a.avatar_config} size={40} title={a.name} />
            <div style={{ minWidth: 0 }}>
              <div style={{ fontSize: 13, fontWeight: 800 }}>{a.name}</div>
              <div style={{ fontSize: 10, color: "var(--ink-3)", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                {a.role_title || "—"}
              </div>
            </div>
          </button>
        ))}
      </div>
    </Modal>
  );
}

function NodeCard({ node, agent, selected, dragging, onMouseDown }: {
  node: WorkflowNode;
  agent?: Agent;
  selected: boolean;
  dragging: boolean;
  onMouseDown: (e: React.MouseEvent) => void;
}) {
  const { t } = useTranslation();
  return (
    <div
      className={`wf-node ${selected ? "selected" : ""} ${node.node_type}-node`}
      style={{
        left: node.pos_x,
        top: node.pos_y,
        cursor: dragging ? "grabbing" : "grab",
        zIndex: dragging ? 10 : 1,
      }}
      onMouseDown={onMouseDown}
    >
      <div className="nd-type">{node.node_type.toUpperCase()}</div>
      <div className="nd-title">{node.label || t("workflows.unnamed")}</div>
      {agent && (
        <div className="nd-body">
          <Avatar cfg={agent.avatar_config} size={44} title={agent.name} className="nd-avatar" />
          <div className="nd-meta">
            <strong>{agent.name}</strong>
            <small>{agent.role_title}</small>
          </div>
        </div>
      )}
    </div>
  );
}

interface MemberRender {
  memberId: number;
  agentId: number;
  name: string;
  roleTitle: string | null;
  avatarCfg: Record<string, string>;
  x: number;
  y: number;
  w: number;
  h: number;
}

function GroupContainer({
  node, x, y, w, h, selected, dragging, onMouseDown, members,
}: {
  node: WorkflowNode;
  x: number;
  y: number;
  w: number;
  h: number;
  selected: boolean;
  dragging: boolean;
  onMouseDown: (e: React.MouseEvent) => void;
  members: MemberRender[];
}) {
  const { t } = useTranslation();
  const mode = node.group?.mode || "parallel";
  return (
    <>
      {/* Group bounding box */}
      <div
        className={`wf-group ${selected ? "selected" : ""}`}
        style={{
          position: "absolute",
          left: x,
          top: y,
          width: w,
          height: h,
          cursor: dragging ? "grabbing" : "grab",
          zIndex: dragging ? 10 : 1,
        }}
        onMouseDown={onMouseDown}
      >
        <div className="wf-group-label">
          <span className="wf-group-mode">{mode === "parallel" ? t("workflowEditor.parallel") : t("workflowEditor.sequential")}</span>
          <span className="wf-group-name">{node.label || node.group?.name || t("workflows.unnamed")}</span>
        </div>
      </div>
      {/* Member mini-cards as siblings so the absolute positions match the SVG geometry */}
      {members.map((m) => (
        <div
          key={`m-${m.memberId}`}
          className="wf-group-member"
          style={{
            position: "absolute",
            left: m.x,
            top: m.y,
            width: m.w,
            height: m.h,
            zIndex: 2,
          }}
        >
          <Avatar cfg={m.avatarCfg} size={36} title={m.name} className="wf-group-member-avatar" />
          <div className="wf-group-member-meta">
            <strong>{m.name}</strong>
            <small>{m.roleTitle || ""}</small>
          </div>
        </div>
      ))}
    </>
  );
}
