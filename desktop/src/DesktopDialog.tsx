import { useState, useEffect, useRef, useCallback } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { listen } from "@tauri-apps/api/event";
import { invoke } from "@tauri-apps/api/core";
import { AgentsAPI, LeadAPI, Agent } from "@shared/api/client";
import { absoluteUrl } from "./api-adapter";
import Avatar from "@shared/components/Avatar";
import "@shared/components/Avatar.css";
import "./desktop.css";

interface MeInfo {
  id: number;
  username: string;
  display_name?: string;
  role?: string;
}

type PanelMode = "chat" | "settings" | "log" | null;
type BustSize = "small" | "medium" | "large";
type BustColor = "default" | "black" | "orange";
const BUST_HEIGHTS: Record<BustSize, number> = { small: 120, medium: 180, large: 260 };

interface CastLayout {
  agent_order?: number[];
  bust_size?: BustSize;
  desktop_positions?: Record<string, { xPct: number; yPct: number }>;
  facing?: Record<string, "left" | "right">;
  hidden_agents?: number[];
  colors?: Record<string, BustColor>;
  // When true, the cast shows only the Lead bust. Flipped by the tray
  // "Show Lead only" menu item.
  show_lead_only?: boolean;
}

export default function DesktopDialog({
  me,
  onLogout,
}: {
  me: MeInfo;
  onLogout: () => void;
}) {
  const qc = useQueryClient();
  const { data: agents = [] } = useQuery({
    queryKey: ["agents"],
    queryFn: AgentsAPI.list,
    refetchInterval: 10_000,
  });

  const [activeAgentId, setActiveAgentId] = useState<number | null>(null);
  const [panelMode, setPanelMode] = useState<PanelMode>(null);

  // Layout from server (shared with web)
  const { data: serverLayout } = useQuery<CastLayout>({
    queryKey: ["cast-layout"],
    queryFn: async () => {
      const r = await fetch("/api/me/cast_layout");
      return r.ok ? r.json() : {};
    },
  });

  const [layout, setLayout] = useState<CastLayout>({});
  const layoutLoaded = useRef(false);

  // Sync server layout → local state on first load
  useEffect(() => {
    if (serverLayout && !layoutLoaded.current) {
      setLayout(serverLayout);
      layoutLoaded.current = true;
    }
  }, [serverLayout]);

  // Debounced save to server
  const saveTimer = useRef<ReturnType<typeof setTimeout>>();
  const saveLayout = useCallback(
    (next: CastLayout) => {
      setLayout(next);
      clearTimeout(saveTimer.current);
      saveTimer.current = setTimeout(() => {
        fetch("/api/me/cast_layout", {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(next),
        });
      }, 800);
    },
    [],
  );

  // Listen for tray events
  useEffect(() => {
    const u1 = listen<string>("set-bust-size", (e) => {
      const size = e.payload as BustSize;
      if (BUST_HEIGHTS[size]) {
        saveLayout({ ...layout, bust_size: size });
      }
    });
    const u2 = listen("reset-cast-positions", () => {
      // Clear saved positions → regenerate defaults on next render
      saveLayout({ ...layout, desktop_positions: undefined, agent_order: undefined });
    });
    const u3 = listen("toggle-show-lead-only", () => {
      // Flip the lead-only filter on/off each time the tray item fires.
      saveLayout({ ...layout, show_lead_only: !layout.show_lead_only });
    });
    return () => {
      u1.then((fn) => fn());
      u2.then((fn) => fn());
      u3.then((fn) => fn());
    };
  }, [layout, saveLayout]);

  const bustSize: BustSize = layout.bust_size || "medium";
  const bustHeight = BUST_HEIGHTS[bustSize];
  const activeAgent = agents.find((a) => a.id === activeAgentId) ?? null;

  // Resolve the active agent's on-screen anchor + facing so the chat
  // panel can attach itself to the selected bust rather than screen center.
  const orderedForAnchor = buildOrderedAgents(agents, layout.agent_order);
  const defaultPosForAnchor = generateDefaultPositions(orderedForAnchor, bustHeight);
  const savedPosForAnchor = layout.desktop_positions || {};
  const activeAnchorPct =
    activeAgent && (savedPosForAnchor[activeAgent.id] || defaultPosForAnchor[activeAgent.id]);
  const activeFacing: "left" | "right" =
    (activeAgent && (layout.facing || {})[activeAgent.id]) || "right";

  function selectAgent(id: number, mode: PanelMode) {
    if (activeAgentId === id && panelMode === mode) {
      setActiveAgentId(null);
      setPanelMode(null);
    } else {
      setActiveAgentId(id);
      setPanelMode(mode);
    }
  }

  return (
    <div className="desktop-root" style={{ pointerEvents: "none" }}>
      <div className="desktop-version-badge" aria-label="build version">
        {__BUILD_VERSION__}
      </div>
      {activeAgent && panelMode === "chat" && (
        <ChatPanel
          agent={activeAgent}
          me={me}
          anchorXPct={activeAnchorPct?.xPct}
          anchorYPct={activeAnchorPct?.yPct}
          facing={activeFacing}
          bustHeight={bustHeight}
          onClose={() => { setActiveAgentId(null); setPanelMode(null); }}
        />
      )}
      {activeAgent && (panelMode === "settings" || panelMode === "log") && (
        <FloatingInfoPanel
          agent={activeAgent}
          kind={panelMode}
          anchorXPct={activeAnchorPct?.xPct}
          anchorYPct={activeAnchorPct?.yPct}
          facing={activeFacing}
          bustHeight={bustHeight}
          onClose={() => { setActiveAgentId(null); setPanelMode(null); }}
        />
      )}

      <CastBar
        agents={agents}
        activeId={activeAgentId}
        bustHeight={bustHeight}
        layout={layout}
        onLayoutChange={saveLayout}
        onChat={(id) => selectAgent(id, "chat")}
        onSettings={(id) => selectAgent(id, "settings")}
        onLog={(id) => selectAgent(id, "log")}
      />
    </div>
  );
}


/* ============================================================================
   Default position generator — Lead leftmost, new agents next to Lead
   ============================================================================ */

function buildOrderedAgents(agents: Agent[], savedOrder?: number[]): Agent[] {
  if (savedOrder && savedOrder.length > 0) {
    const idSet = new Set(agents.map((a) => a.id));
    const ordered: Agent[] = [];
    const used = new Set<number>();
    for (const id of savedOrder) {
      const a = agents.find((x) => x.id === id);
      if (a) { ordered.push(a); used.add(id); }
    }
    // New agents not in saved order → insert right after Lead (position 1)
    const newAgents = agents.filter((a) => !used.has(a.id));
    if (newAgents.length > 0) {
      const insertIdx = ordered.findIndex((a) => !a.is_lead) || 1;
      ordered.splice(insertIdx, 0, ...newAgents);
    }
    return ordered;
  }
  // No saved order: Lead first, then by id
  const lead = agents.filter((a) => a.is_lead);
  const rest = agents.filter((a) => !a.is_lead).sort((a, b) => a.id - b.id);
  return [...lead, ...rest];
}

type Positions = Record<string, { xPct: number; yPct: number }>;

function generateDefaultPositions(agents: Agent[], bustHeight: number): Positions {
  const out: Positions = {};
  const n = agents.length;
  if (n === 0) return out;
  // Each agent occupies roughly bustHeight * 0.6 pixels of width.
  // Gap = one agent width between each. Total span centered on screen.
  const agentWidthPct = (bustHeight * 0.6) / window.innerWidth;
  const gap = agentWidthPct; // one agent-width gap
  const totalWidth = n * agentWidthPct + (n - 1) * gap;
  const startX = 0.5 - totalWidth / 2 + agentWidthPct / 2;
  agents.forEach((a, i) => {
    out[a.id] = {
      xPct: Math.max(0.03, Math.min(0.97, startX + i * (agentWidthPct + gap))),
      yPct: 0.88,
    };
  });
  return out;
}


/* ============================================================================
   Cast Bar — free-drag with persisted positions
   ============================================================================ */

function CastBar({
  agents,
  activeId,
  bustHeight,
  layout,
  onLayoutChange,
  onChat,
  onSettings,
  onLog,
}: {
  agents: Agent[];
  activeId: number | null;
  bustHeight: number;
  layout: CastLayout;
  onLayoutChange: (next: CastLayout) => void;
  onChat: (id: number) => void;
  onSettings: (id: number) => void;
  onLog: (id: number) => void;
}) {
  const { t } = useTranslation();
  const containerRef = useRef<HTMLDivElement>(null);
  const [hoveredId, setHoveredId] = useState<number | null>(null);
  const [draggingId, setDraggingId] = useState<number | null>(null);
  const [ctxMenu, setCtxMenu] = useState<{ agentId: number; x: number; y: number } | null>(null);
  const dragState = useRef<{
    id: number; startX: number; startY: number;
    startPctX: number; startPctY: number; moved: boolean;
  } | null>(null);

  const facing = layout.facing || {};
  const colors = layout.colors || {};
  const hiddenAgents = new Set(layout.hidden_agents || []);
  // Show-lead-only mode (tray toggle): collapse the cast to just the
  // Lead agent. Non-lead agents get hidden from the bust row.
  const showLeadOnly = !!layout.show_lead_only;

  function setFacing(agentId: number, dir: "left" | "right") {
    const next = { ...facing, [agentId]: dir };
    onLayoutChange({ ...layout, facing: next });
    setCtxMenu(null);
  }

  function setColor(agentId: number, color: BustColor) {
    const next = { ...colors, [agentId]: color };
    onLayoutChange({ ...layout, colors: next });
    setCtxMenu(null);
  }

  function toggleHidden(agentId: number) {
    const current = layout.hidden_agents || [];
    const next = current.includes(agentId)
      ? current.filter((id: number) => id !== agentId)
      : [...current, agentId];
    onLayoutChange({ ...layout, hidden_agents: next });
    setCtxMenu(null);
  }

  // Close context menu on click anywhere
  useEffect(() => {
    if (!ctxMenu) return;
    function close() { setCtxMenu(null); }
    window.addEventListener("click", close);
    return () => window.removeEventListener("click", close);
  }, [ctxMenu]);

  const orderedAgentsRaw = buildOrderedAgents(agents, layout.agent_order);
  // Filter to Lead only when the tray toggle is on. Keeps context menu,
  // drag, facing etc. working against the smaller set.
  const orderedAgents = showLeadOnly
    ? orderedAgentsRaw.filter((a) => a.is_lead)
    : orderedAgentsRaw;
  const savedPos = layout.desktop_positions || {};
  const defaults = generateDefaultPositions(orderedAgents, bustHeight);
  // Merge: use saved if exists, otherwise default
  const positions: Positions = {};
  orderedAgents.forEach((a) => {
    positions[a.id] = savedPos[a.id] || defaults[a.id];
  });

  function updatePosition(agentId: number, xPct: number, yPct: number) {
    const nextPos = { ...savedPos, [agentId]: { xPct, yPct } };
    const order = orderedAgents.map((a) => a.id);
    onLayoutChange({ ...layout, desktop_positions: nextPos, agent_order: order });
  }

  useEffect(() => {
    function onMove(e: MouseEvent) {
      const ds = dragState.current;
      if (!ds || !containerRef.current) return;
      const rect = containerRef.current.getBoundingClientRect();
      const dx = e.clientX - ds.startX;
      if (Math.abs(dx) > 3) ds.moved = true;
      const xPct = Math.max(0.02, Math.min(0.98, ds.startPctX + dx / rect.width));
      updatePosition(ds.id, xPct, ds.startPctY);
    }
    function onUp() {
      if (dragState.current) { setDraggingId(null); dragState.current = null; }
    }
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
    return () => { window.removeEventListener("mousemove", onMove); window.removeEventListener("mouseup", onUp); };
  });

  const hasActive = activeId !== null;
  // z-index: leftmost highest, hover/drag top
  const sortedByX = [...orderedAgents].sort(
    (a, b) => (positions[a.id]?.xPct ?? 0) - (positions[b.id]?.xPct ?? 0),
  );
  const zMap: Record<number, number> = {};
  sortedByX.forEach((a, i) => { zMap[a.id] = agents.length - i; });

  return (
    <div ref={containerRef} className="cast-bar cast-bar-freeform" style={{ pointerEvents: "none" }}>
      {/* Right-click context menu */}
      {ctxMenu && (
        <div
          className="cast-ctx-menu"
          data-interactive
          style={{ left: ctxMenu.x, top: ctxMenu.y, pointerEvents: "auto" }}
          onClick={(e) => e.stopPropagation()}
        >
          <button
            className={`cast-ctx-item ${(facing[ctxMenu.agentId] || "right") === "right" ? "active" : ""}`}
            onClick={() => setFacing(ctxMenu.agentId, "right")}
          >
            {t("dialog.faceRight")}
          </button>
          <button
            className={`cast-ctx-item ${(facing[ctxMenu.agentId] || "right") === "left" ? "active" : ""}`}
            onClick={() => setFacing(ctxMenu.agentId, "left")}
          >
            {t("dialog.faceLeft")}
          </button>
          <div style={{ height: 1, background: "var(--desktop-border)", margin: "4px 0" }} />
          <div className="cast-ctx-label">{t("dialog.color")}</div>
          <div className="cast-ctx-colors">
            {(["default", "black", "orange"] as BustColor[]).map((c) => {
              const active = (colors[ctxMenu.agentId] || "default") === c;
              return (
                <button
                  key={c}
                  className={`cast-ctx-swatch cast-ctx-swatch-${c} ${active ? "active" : ""}`}
                  onClick={() => setColor(ctxMenu.agentId, c)}
                  title={t(`dialog.color_${c}`)}
                />
              );
            })}
          </div>
          <div style={{ height: 1, background: "var(--desktop-border)", margin: "4px 0" }} />
          <button
            className="cast-ctx-item"
            onClick={() => toggleHidden(ctxMenu.agentId)}
          >
            {t("dialog.hide")}
          </button>
        </div>
      )}
      {orderedAgents.filter((a) => !hiddenAgents.has(a.id)).map((a) => {
        const pos = positions[a.id] || { xPct: 0.5, yPct: 0.85 };
        const isActive = activeId === a.id;
        const isHovered = hoveredId === a.id;
        const isDragging = draggingId === a.id;
        let z = zMap[a.id] || 1;
        if (isHovered || isDragging) z = agents.length + 2;
        if (isActive && !isHovered) z = agents.length + 1;

        return (
          <div
            key={a.id}
            className={`cast-member cast-member-abs ${isActive ? "active" : ""} ${hasActive && !isActive ? "dimmed" : ""} ${isDragging ? "dragging" : ""}`}
            data-interactive
            style={{ left: `${pos.xPct * 100}%`, top: `${pos.yPct * 100}%`, zIndex: z, height: bustHeight, pointerEvents: "auto" }}
            onMouseEnter={() => setHoveredId(a.id)}
            onMouseLeave={() => setHoveredId(null)}
            onContextMenu={(e) => {
              e.preventDefault();
              setCtxMenu({ agentId: a.id, x: e.clientX, y: e.clientY });
            }}
            onMouseDown={(e) => {
              if ((e.target as HTMLElement).closest(".cast-actions")) return;
              dragState.current = {
                id: a.id, startX: e.clientX, startY: e.clientY,
                startPctX: pos.xPct, startPctY: pos.yPct, moved: false,
              };
              setDraggingId(a.id);
              e.preventDefault();
            }}
            onClick={(e) => {
              if (dragState.current?.moved) return;
              e.stopPropagation();
              onChat(a.id);
            }}
          >
            <div className="cast-actions" onClick={(e) => e.stopPropagation()}>
              <button className="cast-action-btn" title={t("dialog.actionLog")} onClick={() => onLog(a.id)}>
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round"><rect x="3" y="4" width="18" height="18" rx="2" /><line x1="16" y1="2" x2="16" y2="6" /><line x1="8" y1="2" x2="8" y2="6" /><line x1="3" y1="10" x2="21" y2="10" /></svg>
              </button>
              <button className="cast-action-btn" title={t("dialog.actionChat")} onClick={() => onChat(a.id)}>
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" /></svg>
              </button>
              <button className="cast-action-btn" title={t("dialog.actionSettings")} onClick={() => onSettings(a.id)}>
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="3" /><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06A1.65 1.65 0 0 0 4.6 15 1.65 1.65 0 0 0 3.09 14H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9 1.65 1.65 0 0 0 4.27 7.18l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9c.26.604.852.997 1.51 1H21a2 2 0 0 1 0 4h-.09c-.658.003-1.25.396-1.51 1z" /></svg>
              </button>
            </div>
            {(() => {
              const color = colors[a.id] || "default";
              const url = absoluteUrl(`/api/avatar/compose?body_type=body_bust&body=${encodeURIComponent(a.avatar_config?.body || "Shirt")}&hair=${encodeURIComponent(a.avatar_config?.hair || "Medium")}&face=${encodeURIComponent(a.avatar_config?.face || "Calm")}${a.avatar_config?.facial_hair ? `&facial_hair=${encodeURIComponent(a.avatar_config.facial_hair)}` : ""}${a.avatar_config?.accessory ? `&accessory=${encodeURIComponent(a.avatar_config.accessory)}` : ""}&vb=0,-200,850,1400`);
              return (
                <div
                  className={`cast-bust cast-bust-color-${color}`}
                  style={{
                    height: bustHeight - 30,
                    transform: (facing[a.id] || "right") === "left" ? "scaleX(-1)" : undefined,
                    ["--bust-url" as any]: `url("${url}")`,
                  }}
                >
                  {color === "default" ? (
                    <img src={url} alt={a.name} draggable={false} />
                  ) : (
                    <div className="cast-bust-silhouette" aria-label={a.name} />
                  )}
                </div>
              );
            })()}
            <div className="cast-name">{a.name}</div>
            <div className={`cast-status ${a.status}`} />
          </div>
        );
      })}
    </div>
  );
}


/* ============================================================================
   Anchor-aware panel positioning
   ============================================================================ */

interface PanelAnchorProps {
  anchorXPct?: number;
  anchorYPct?: number;
  facing: "left" | "right";
  bustHeight: number;
}

// Produce an inline style that anchors a panel next to the selected
// agent on screen. If `anchorXPct` is missing (e.g. agent just deleted)
// we fall back to screen-centered positioning.
function useAnchoredPanelStyle(
  { anchorXPct, anchorYPct, facing, bustHeight }: PanelAnchorProps,
  size: { width: number; height: number },
): React.CSSProperties {
  const [vw, setVw] = useState(() => (typeof window !== "undefined" ? window.innerWidth : 1280));
  const [vh, setVh] = useState(() => (typeof window !== "undefined" ? window.innerHeight : 720));
  useEffect(() => {
    function onResize() { setVw(window.innerWidth); setVh(window.innerHeight); }
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  if (anchorXPct == null || anchorYPct == null) {
    return { left: "50%", top: "50%", transform: "translate(-50%, -50%)" };
  }
  const margin = 16;
  const gap = 24;
  const anchorPx = anchorXPct * vw;
  const anchorY = anchorYPct * vh;

  // Horizontal: if the agent faces right, the panel sits to the right
  // of the bust (i.e. the chat's left edge anchors near the agent).
  // If facing left, mirror that: panel sits to the left.
  let left: number;
  if (facing === "right") {
    left = Math.min(Math.max(anchorPx + gap, margin), vw - size.width - margin);
  } else {
    left = Math.min(Math.max(anchorPx - size.width - gap, margin), vw - size.width - margin);
  }

  // Vertical: place the panel's bottom above the bust, clamped so the
  // top never clips off-screen.
  const bustTop = anchorY - bustHeight;
  let top = bustTop - size.height - 12;
  if (top < margin) {
    // Not enough room above — try below the bust instead.
    top = Math.min(anchorY + 12, vh - size.height - margin);
  }
  top = Math.max(margin, top);

  return { left, top };
}

/* ============================================================================
   Chat Panel
   ============================================================================ */

function ChatPanel({
  agent,
  me,
  anchorXPct,
  anchorYPct,
  facing,
  bustHeight,
  onClose,
}: {
  agent: Agent;
  me: MeInfo;
  anchorXPct?: number;
  anchorYPct?: number;
  facing: "left" | "right";
  bustHeight: number;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const [input, setInput] = useState("");
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const isLead = agent.is_lead;

  const { data: threadData } = useQuery({
    queryKey: isLead ? ["lead-threads"] : ["agent-threads", agent.id],
    queryFn: () => (isLead ? LeadAPI.threads() : AgentsAPI.threads(agent.id)),
    refetchInterval: 5_000,
  });
  const threadId = Array.isArray(threadData) ? (threadData[0]?.thread_id || (threadData[0] as any)?.id || null) : null;

  const { data: messagesData } = useQuery({
    queryKey: isLead ? ["lead-messages", threadId] : ["agent-messages", agent.id],
    queryFn: async () => {
      if (isLead && threadId) { const r = await LeadAPI.messages(threadId); return (r as any).messages || r || []; }
      const r = await fetch(`/api/agents/${agent.id}/queue`); return r.json();
    },
    enabled: isLead ? !!threadId : true,
    refetchInterval: 3_000,
  });
  const messages = Array.isArray(messagesData) ? messagesData : [];

  // Live token buffer — populated chunk-by-chunk while a Lead streaming
  // request is in flight, cleared once the final structured result has
  // landed in the cache. Same pattern as the web DialogCenter.
  const [streamingText, setStreamingText] = useState("");

  const send = useMutation({
    mutationFn: async () => {
      if (!input.trim()) return;
      if (isLead) {
        // Streaming path — token-by-token rendering. Workflow / hire /
        // project / artifact proposals are still parsed at stream close
        // (handled server-side in chat_streaming) and surface via the
        // refetch invalidation below.
        setStreamingText("");
        let buf = "";
        await LeadAPI.chatStreaming(
          input.trim(),
          threadId || undefined,
          {
            onChunk: (delta) => { buf += delta; setStreamingText(buf); },
            onError: (msg) => { console.warn("[lead-stream]", msg); },
          },
        );
      } else {
        // Agent direct chat — no streaming endpoint yet. Stay on batch.
        await AgentsAPI.chat(agent.id, input.trim());
      }
    },
    onSuccess: () => {
      setStreamingText("");
      setInput("");
      qc.invalidateQueries({ queryKey: ["lead-threads"] });
      qc.invalidateQueries({ queryKey: ["lead-messages"] });
      qc.invalidateQueries({ queryKey: ["agent-threads", agent.id] });
      qc.invalidateQueries({ queryKey: ["agent-messages", agent.id] });
    },
    onError: () => { setStreamingText(""); },
  });

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages.length, streamingText]);

  // Dock bounce when an agent replies while the window isn't focused.
  // We watch for the message count to grow — if the tail message is
  // from the assistant (role !== "user") and document.hasFocus() is
  // false, fire `request_attention` to nudge the OS dock/taskbar.
  const lastMsgCountRef = useRef<number>(messages.length);
  useEffect(() => {
    const prev = lastMsgCountRef.current;
    lastMsgCountRef.current = messages.length;
    if (messages.length <= prev) return;
    const tail = messages[messages.length - 1];
    const tailRole = tail && (tail.role as string | undefined);
    const fromAgent = tailRole && tailRole !== "user";
    if (fromAgent && typeof document !== "undefined" && !document.hasFocus()) {
      invoke("request_attention").catch(() => {
        // Plugin might not be available in dev (web-vite). Silently drop.
      });
    }
  }, [messages.length]);

  // Compact when there is no conversation history yet; expand once
  // the user actually has a thread going so replies have room to breathe.
  const hasHistory = messages.length > 0;
  const panelSize = hasHistory
    ? { width: 520, height: 480 }
    : { width: 380, height: 220 };
  const anchorStyle = useAnchoredPanelStyle(
    { anchorXPct, anchorYPct, facing, bustHeight },
    panelSize,
  );

  return (
    <div
      className={`chat-panel chat-panel-anchored ${hasHistory ? "chat-panel-expanded" : "chat-panel-compact"}`}
      data-interactive
      style={{ pointerEvents: "auto", ...anchorStyle, width: panelSize.width }}
      onClick={(e) => e.stopPropagation()}
    >
      <div className="chat-header">
        <Avatar cfg={agent.avatar_config} size={32} title={agent.name} />
        <div className="chat-header-name"><strong>{agent.name}</strong><span>{agent.role_title || ""}</span></div>
        <button className="chat-close" onClick={onClose}>&times;</button>
      </div>
      <div className="chat-messages">
        {messages.map((m: any, i: number) => {
          const isUser = m.role === "user" || m.sender === "user" || m.role === "human";
          return <div key={m.id || i} className={`chat-bubble ${isUser ? "user" : "bot"}`}>{m.content || m.text || m.message || ""}</div>;
        })}
        {/* Live streaming bubble — only shown while Lead is mid-reply.
            Clears as soon as onSuccess invalidates the messages query. */}
        {streamingText && (
          <div className="chat-bubble bot streaming">
            {streamingText}
            <span className="streaming-cursor" aria-hidden="true">▍</span>
          </div>
        )}
        <div ref={messagesEndRef} />
      </div>
      <div className="chat-composer">
        <textarea value={input} onChange={(e) => setInput(e.target.value)} placeholder={t("dialog.composerPlaceholder", { name: agent.name })}
          onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); send.mutate(); } }} />
        <button onClick={() => send.mutate()} disabled={!input.trim() || send.isPending}>{send.isPending ? "…" : "↑"}</button>
      </div>
    </div>
  );
}


/* ============================================================================
   Floating Info Panel (Settings / Log)
   ============================================================================ */

function FloatingInfoPanel({
  agent,
  kind,
  anchorXPct,
  anchorYPct,
  facing,
  bustHeight,
  onClose,
}: {
  agent: Agent;
  kind: "settings" | "log";
  anchorXPct?: number;
  anchorYPct?: number;
  facing: "left" | "right";
  bustHeight: number;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const panelSize = { width: 380, height: 420 };
  const anchorStyle = useAnchoredPanelStyle(
    { anchorXPct, anchorYPct, facing, bustHeight },
    panelSize,
  );
  return (
    <div
      className="chat-panel chat-panel-anchored floating-info-panel"
      data-interactive
      style={{ pointerEvents: "auto", ...anchorStyle, width: panelSize.width }}
      onClick={(e) => e.stopPropagation()}
    >
      <div className="chat-header">
        <Avatar cfg={agent.avatar_config} size={32} title={agent.name} />
        <div className="chat-header-name"><strong>{agent.name}</strong><span>{kind === "settings" ? t("dialog.settings") : t("dialog.log")}</span></div>
        <button className="chat-close" onClick={onClose}>&times;</button>
      </div>
      <div className="floating-info-body">
        {kind === "settings" ? <SettingsContent agent={agent} /> : <LogContent agent={agent} />}
      </div>
    </div>
  );
}

function SettingsContent({ agent }: { agent: Agent }) {
  const { t } = useTranslation();
  return (
    <div className="floating-info-scroll">
      <div className="fi-row"><span className="fi-label">{t("dialog.name")}</span><span>{agent.name}</span></div>
      <div className="fi-row"><span className="fi-label">{t("dialog.role")}</span><span>{agent.role_title || "—"}</span></div>
      <div className="fi-row"><span className="fi-label">{t("dialog.status")}</span><span className={`cast-status-text ${agent.status}`}>{agent.status}</span></div>
      <div className="fi-row"><span className="fi-label">{t("dialog.model")}</span><span style={{fontSize:11,fontFamily:"monospace"}}>{agent.primary_model_id || "—"}</span></div>
      {agent.description && <div className="fi-section"><div className="fi-label">{t("dialog.description")}</div><div className="fi-text">{agent.description}</div></div>}
      <div className="fi-hint">{t("dialog.settingsHint", { name: agent.name })}</div>
    </div>
  );
}

function LogContent({ agent }: { agent: Agent }) {
  const { t, i18n } = useTranslation();
  const { data: runs = [] } = useQuery({
    queryKey: ["agent-runs", agent.id],
    queryFn: () => AgentsAPI.runs(agent.id),
  });
  const recent = (Array.isArray(runs) ? runs : []).slice(0, 10);
  const locale = i18n.language === "zh-TW" ? "zh-TW" : "en-US";
  return (
    <div className="floating-info-scroll">
      {recent.length === 0 ? <div className="fi-hint">{t("dialog.noRuns")}</div> : (
        recent.map((r: any) => (
          <div key={r.id} className="fi-row">
            <span className="fi-label">#{r.id}</span>
            <span className={`cast-status-text ${r.status}`}>{r.status}</span>
            <span style={{flex:1,textAlign:"right",fontSize:10,color:"rgba(240,236,230,0.4)"}}>{r.created_at ? new Date(r.created_at).toLocaleString(locale) : ""}</span>
          </div>
        ))
      )}
      <div className="fi-hint">{t("dialog.logHint")}</div>
    </div>
  );
}
