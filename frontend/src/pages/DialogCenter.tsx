import { useTranslation } from "react-i18next";
import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery, useInfiniteQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useNavigate, useSearchParams } from "react-router-dom";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { AgentsAPI, LeadAPI, McpAPI, RunsAPI, ToolsAPI, api, bustUrl, headUrl, Agent, LeadMessage } from "../api/client";
import WorkflowBubble from "../components/WorkflowBubble";
import HireBubble from "../components/HireBubble";
import "../components/HireBubble.css";
import { AgentOverviewEditor, AgentSkillsEditor } from "../components/AgentEditors";
import "./DialogCenter.css";

type CastTab = "chat" | "calendar" | "settings";

export default function DialogCenter() {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const { data: agents = [] } = useQuery({
    queryKey: ["agents"],
    queryFn: AgentsAPI.list,
    refetchInterval: 10_000,
  });

  const { data: leadPending } = useQuery({
    queryKey: ["lead-pending"],
    queryFn: LeadAPI.pendingCount,
    refetchInterval: 8_000,
  });
  const leadPendingCount = leadPending?.count ?? 0;

  // Cast layout (shared with desktop) — used for hidden_agents list
  const { data: castLayout } = useQuery<{
    hidden_agents?: number[];
    [k: string]: unknown;
  }>({
    queryKey: ["cast-layout"],
    queryFn: async () => {
      const r = await fetch("/api/me/cast_layout", { credentials: "include" });
      return r.ok ? r.json() : {};
    },
  });
  const hiddenAgents = new Set(castLayout?.hidden_agents || []);
  const toggleAgentHidden = (agentId: number) => {
    const current = castLayout?.hidden_agents || [];
    const next = current.includes(agentId)
      ? current.filter((id: number) => id !== agentId)
      : [...current, agentId];
    const newLayout = { ...castLayout, hidden_agents: next };
    fetch("/api/me/cast_layout", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify(newLayout),
    }).then(() => qc.invalidateQueries({ queryKey: ["cast-layout"] }));
  };

  // `?agent=<id>` URL query pre-selects a specific cast member on mount.
  // Used when coming in from "borrowed agents" on the Agents page.
  const [searchParams, setSearchParams] = useSearchParams();
  const initialAgentParam = searchParams.get("agent");
  const [activeId, setActiveId] = useState<string | null>(
    initialAgentParam ? initialAgentParam : "lead",
  );
  const [activeTab, setActiveTab] = useState<CastTab>("chat");
  // Clear the ?agent= param once consumed so refreshing doesn't re-trigger.
  useEffect(() => {
    if (initialAgentParam) {
      const next = new URLSearchParams(searchParams);
      next.delete("agent");
      setSearchParams(next, { replace: true });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  // Bust height in px. Default ≈ 260; user drags the handle vertically to
  // resize. Clamped between viewport/10 and viewport/3 below. Persisted to
  // localStorage so the preference survives refresh.
  const CAST_SIZE_KEY = "agent_company.dialog.castSize";
  const [castSize, setCastSize] = useState(() => {
    try {
      const stored = window.localStorage.getItem(CAST_SIZE_KEY);
      const n = stored ? Number(stored) : NaN;
      const min = Math.max(60, Math.floor(window.innerHeight / 10));
      const max = Math.floor(window.innerHeight / 3);
      if (Number.isFinite(n) && n > 0) {
        return Math.min(max, Math.max(min, Math.round(n)));
      }
    } catch {}
    return Math.min(260, Math.max(140, Math.floor(window.innerHeight / 3.6)));
  });
  useEffect(() => {
    try {
      window.localStorage.setItem(CAST_SIZE_KEY, String(castSize));
    } catch {}
  }, [castSize]);
  const BADGE_THRESHOLD = 130; // below this height we render circular badges
  const isBadgeMode = castSize < BADGE_THRESHOLD;
  // Thread state per cast member:
  //   undefined → not decided yet; auto-load most recent thread when
  //               the thread list arrives
  //   null      → user explicitly wants a fresh new thread
  //   string    → specific thread id
  const [threadByActive, setThreadByActive] = useState<Record<string, string | null | undefined>>({});
  const [drawerOpen, setDrawerOpen] = useState(false);
  // Sub-tab inside the settings panel: 總覽 / 技能 / 工具
  const [settingsSubTab, setSettingsSubTab] = useState<"overview" | "skills" | "tools">("overview");

  // Helpers used by cast member icon clicks
  const selectCalendar = (id: string) => {
    setActiveId(id);
    setActiveTab("calendar");
  };
  const selectSettings = (id: string) => {
    setActiveId(id);
    setActiveTab("settings");
    setSettingsSubTab("overview");
  };
  const toggleChat = (id: string) => {
    // Click bust: if already chat-active, close; otherwise switch to chat
    setActiveId((cur) => (cur === id && activeTab === "chat" ? null : id));
    setActiveTab("chat");
  };

  const stageRef = useRef<HTMLElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);

  // Resolve the currently-active agent from cast id
  const activeAgent = activeId && activeId !== "lead"
    ? agents.find((a) => `${a.id}` === activeId) ?? null
    : null;
  const isLeadActive = activeId === "lead";
  const hasActive = !!activeId;
  // Only treat string values as "load this thread"; undefined or null means no thread.
  const rawThreadId = activeId ? threadByActive[activeId] : undefined;
  const currentThreadId = typeof rawThreadId === "string" ? rawThreadId : undefined;

  // Thread list — Lead mode shows Lead threads; agent mode shows that agent's threads
  const { data: threads = [] } = useQuery({
    queryKey: isLeadActive ? ["lead-threads"] : ["agent-threads", activeAgent?.id],
    queryFn: () =>
      isLeadActive
        ? LeadAPI.threads()
        : activeAgent ? AgentsAPI.threads(activeAgent.id) : Promise.resolve([]),
    enabled: hasActive,
  });

  // Cursor-paginated history. The first page loads the newest MESSAGES_PAGE_SIZE
  // rows; older pages are fetched on-demand when the user scrolls to the top
  // of the messages list. `fetchNextPage` conceptually means "older", not
  // "further down" — we use `before_id = oldest currently-loaded id`.
  const MESSAGES_PAGE_SIZE = 20;
  const {
    data: messagesPages,
    fetchNextPage,
    hasNextPage,
    isFetchingNextPage,
  } = useInfiniteQuery({
    queryKey: ["messages", currentThreadId],
    enabled: !!currentThreadId,
    initialPageParam: undefined as number | undefined,
    queryFn: ({ pageParam }) =>
      LeadAPI.messages(currentThreadId!, {
        limit: MESSAGES_PAGE_SIZE,
        before_id: pageParam,
      }),
    getNextPageParam: (lastPage) => {
      if (!lastPage.has_more || lastPage.messages.length === 0) return undefined;
      return lastPage.messages[0].id;
    },
  });
  // Flatten pages into a single ascending-by-id list for rendering. Pages
  // come back oldest→newest within the page; older pages are fetched AFTER
  // newer ones, so we prepend older pages at the front.
  const messages = useMemo<LeadMessage[]>(() => {
    if (!messagesPages) return [];
    // pages[0] is the newest window; pages[1] is older; etc.
    // To render oldest→newest we reverse the page order then flatten.
    const ordered = [...messagesPages.pages].reverse();
    return ordered.flatMap((p) => p.messages);
  }, [messagesPages]);

  // Auto-load most recent thread when activeId is fresh (undefined entry).
  // Skip if user explicitly chose "new thread" (null entry).
  useEffect(() => {
    if (!activeId) return;
    const decided = activeId in threadByActive;
    if (decided) return;
    if (threads.length === 0) return;
    setThreadByActive((prev) => ({
      ...prev,
      [activeId]: threads[0].thread_id,
    }));
  }, [activeId, threads, threadByActive]);

  const [input, setInput] = useState("");
  const abortRef = useRef<AbortController | null>(null);

  const sendMutation = useMutation({
    mutationFn: async (text: string) => {
      const ctrl = new AbortController();
      abortRef.current = ctrl;
      try {
        if (isLeadActive) {
          return await LeadAPI.chatWithSignal(text, currentThreadId, ctrl.signal);
        }
        if (!activeAgent) throw new Error("no active agent");
        return await AgentsAPI.chatWithSignal(activeAgent.id, text, currentThreadId, ctrl.signal);
      } finally {
        abortRef.current = null;
      }
    },
    onSuccess: (data) => {
      // Persist the returned thread_id for the current cast member
      if (activeId) {
        setThreadByActive((prev) => ({ ...prev, [activeId]: data.thread_id }));
      }
      qc.invalidateQueries({ queryKey: isLeadActive ? ["lead-threads"] : ["agent-threads", activeAgent?.id] });
      // After a send, the newest page gains two rows (user + assistant) and
      // the stable `before_id` cursors on older pages would now overlap the
      // shifted window. Collapse the cache to the newest page only and
      // re-fetch it; scrolling back up will re-hydrate older pages cleanly.
      qc.setQueryData(
        ["messages", data.thread_id],
        (old: { pages: { messages: LeadMessage[]; has_more: boolean }[]; pageParams: (number | undefined)[] } | undefined) => {
          if (!old) return old;
          return { pages: old.pages.slice(0, 1), pageParams: [undefined] };
        },
      );
      qc.invalidateQueries({ queryKey: ["messages", data.thread_id] });
      // User just replied to Lead → clear the pending indicator
      if (isLeadActive) qc.invalidateQueries({ queryKey: ["lead-pending"] });
    },
  });

  function stopSending() {
    if (abortRef.current) {
      abortRef.current.abort();
      abortRef.current = null;
    }
  }

  const archiveThread = useMutation({
    mutationFn: (tid: string) => LeadAPI.archive(tid),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["lead-threads"] });
      qc.invalidateQueries({ queryKey: ["agent-threads", activeAgent?.id] });
    },
  });

  const leadAgent = agents.find((a) => a.is_lead) ?? null;
  const otherAgents = agents.filter((a) => !a.is_lead);

  const castRowRef = useRef<HTMLDivElement>(null);

  // Auto-focus the composer when active changes so the user can type
  // immediately. No fancy scroll/anchor behaviour — the cast row is now a
  // simple centered list with fixed spacing.
  useEffect(() => {
    if (!activeId) return;
    textareaRef.current?.focus();
  }, [activeId]);

  // Track the newest (last) message id so we can tell "new message at tail"
  // apart from "older page prepended at head". Only the former should
  // auto-scroll to the bottom; the latter should preserve scroll position.
  const lastMsgIdRef = useRef<number | null>(null);
  const messagesScrollRef = useRef<HTMLDivElement>(null);
  const prependScrollAnchorRef = useRef<{ prevHeight: number; prevTop: number } | null>(null);

  useEffect(() => {
    const tailId = messages.length ? messages[messages.length - 1].id : null;
    const prev = lastMsgIdRef.current;
    const container = messagesScrollRef.current;

    // Thread switch or first load — jump straight to the bottom. We must
    // bypass CSS `scroll-behavior: smooth` here: a smooth-scrolled programmatic
    // jump from 0 → max fires intermediate scroll events whose scrollTop is
    // low enough to trip the "reached top" check and auto-fetch older pages.
    if (prev === null && tailId !== null) {
      lastMsgIdRef.current = tailId;
      if (container) {
        const prevBehavior = container.style.scrollBehavior;
        container.style.scrollBehavior = "auto";
        container.scrollTop = container.scrollHeight;
        container.style.scrollBehavior = prevBehavior;
      }
      return;
    }
    // New message appended at tail — smooth-scroll to it.
    if (tailId !== null && prev !== null && tailId > prev) {
      lastMsgIdRef.current = tailId;
      messagesEndRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
      return;
    }
    // Older page prepended — restore scroll position so the viewport stays
    // anchored on the message the user was looking at. Same smooth-scroll
    // caveat applies: set scrollTop instantly.
    const anchor = prependScrollAnchorRef.current;
    if (anchor && container) {
      const delta = container.scrollHeight - anchor.prevHeight;
      const prevBehavior = container.style.scrollBehavior;
      container.style.scrollBehavior = "auto";
      container.scrollTop = anchor.prevTop + delta;
      container.style.scrollBehavior = prevBehavior;
      prependScrollAnchorRef.current = null;
    }
    lastMsgIdRef.current = tailId;
  }, [messages]);

  // Show a loading bubble while waiting for the LLM reply — this is the
  // same "thinking…" indicator as before, just scrolled into view.
  useEffect(() => {
    if (sendMutation.isPending) {
      messagesEndRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
    }
  }, [sendMutation.isPending]);

  // Reset the "last seen" cursor when the user switches threads so the
  // initial-jump branch runs for the new thread.
  useEffect(() => {
    lastMsgIdRef.current = null;
    prependScrollAnchorRef.current = null;
  }, [currentThreadId]);

  // When the user scrolls near the top of the messages container, fetch
  // the next (older) page. We capture scroll geometry first so the effect
  // above can restore the user's viewport once the prepend lands.
  function onMessagesScroll(e: React.UIEvent<HTMLDivElement>) {
    const el = e.currentTarget;
    if (el.scrollTop > 60) return;
    if (!hasNextPage || isFetchingNextPage) return;
    prependScrollAnchorRef.current = {
      prevHeight: el.scrollHeight,
      prevTop: el.scrollTop,
    };
    fetchNextPage();
  }

  // Drag handle for resizing the cast row. Vertical drag only; ignore X.
  const dragRef = useRef<{ startY: number; startSize: number } | null>(null);
  function onResizeHandleDown(e: React.PointerEvent) {
    e.preventDefault();
    e.stopPropagation();
    dragRef.current = { startY: e.clientY, startSize: castSize };
    (e.target as HTMLElement).setPointerCapture?.(e.pointerId);
  }
  function onResizeHandleMove(e: React.PointerEvent) {
    if (!dragRef.current) return;
    const dy = dragRef.current.startY - e.clientY; // up = positive
    const min = Math.max(60, Math.floor(window.innerHeight / 10));
    const max = Math.floor(window.innerHeight / 3);
    setCastSize(Math.min(max, Math.max(min, dragRef.current.startSize + dy)));
  }
  function onResizeHandleUp(e: React.PointerEvent) {
    dragRef.current = null;
    (e.target as HTMLElement).releasePointerCapture?.(e.pointerId);
  }

  // ESC closes the message area and returns the cast to its default sizes
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setActiveId(null);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  async function send() {
    if (!input.trim() || sendMutation.isPending) return;
    const text = input;
    setInput("");
    try {
      await sendMutation.mutateAsync(text);
    } catch (err: any) {
      if (err?.name === "AbortError") {
        // User cancelled — silent
        return;
      }
      throw err;
    }
  }

  return (
    <div
      className={`dc ${isBadgeMode ? "badge-mode" : ""}`}
      style={{ ["--cast-size" as any]: `${castSize}px` }}
    >
      {/* Thread drawer */}
      <button className="thread-toggle" onClick={() => setDrawerOpen((v) => !v)}>
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <line x1="3" y1="6" x2="21" y2="6" />
          <line x1="3" y1="12" x2="21" y2="12" />
          <line x1="3" y1="18" x2="21" y2="18" />
        </svg>{t("dialog.threads")}
        <span className="count">{threads.length}</span>
      </button>

      {drawerOpen && (
        <div className="thread-panel">
          <h4>{isLeadActive ? t("dialog.leadThreads") : t("dialog.agentThreads", { name: activeAgent?.name || "agent" })}</h4>
          <div
            className={`thread-item ${currentThreadId === undefined ? "active" : ""}`}
            onClick={() => {
              if (activeId) setThreadByActive((prev) => ({ ...prev, [activeId]: null }));
              setDrawerOpen(false);
            }}
          >{t("dialog.newThread")}
          </div>
          {threads.map((th) => (
            <div
              key={th.thread_id}
              className={`thread-item ${currentThreadId === th.thread_id ? "active" : ""}`}
              style={{ display: "flex", alignItems: "center", gap: 6 }}
            >
              <div
                style={{ flex: 1, cursor: "pointer" }}
                onClick={() => {
                  if (activeId) setThreadByActive((prev) => ({ ...prev, [activeId]: th.thread_id }));
                  setDrawerOpen(false);
                }}
              >
                {th.title || `Thread ${th.thread_id.slice(0, 6)}`}
              </div>
              <button
                title={t("dialog.archiveThread")}
                data-testid={`archive-thread-${th.thread_id}`}
                onClick={(e) => {
                  e.stopPropagation();
                  archiveThread.mutate(th.thread_id);
                  if (activeId && currentThreadId === th.thread_id) {
                    setThreadByActive((prev) => ({ ...prev, [activeId]: null }));
                  }
                }}
                style={{
                  border: "none",
                  background: "transparent",
                  color: "var(--ink-4)",
                  cursor: "pointer",
                  fontSize: 14,
                  padding: "0 4px",
                }}
              >
                ×
              </button>
            </div>
          ))}
        </div>
      )}

      <main
        className="stage"
        ref={stageRef}
        onClick={(e) => {
          // Click on stage empty area (not on focus-lane or any descendant)
          // closes the message area.
          if (e.target === e.currentTarget) setActiveId(null);
        }}
      >
        {hasActive && (
        <div className="focus-lane">
          {activeTab === "chat" && (
            <>
              <div
                className="messages"
                ref={messagesScrollRef}
                onScroll={onMessagesScroll}
                data-testid="dialog-messages"
              >
                {isFetchingNextPage && (
                  <div
                    className="messages-loading-older"
                    data-testid="messages-loading-older"
                  >{t("dialog.loadingOlder")}
                  </div>
                )}
                {messages.length === 0 && (
                  <div className="empty">
                    {isLeadActive
                      ? t("dialog.leadGreeting")
                      : activeAgent
                        ? t("dialog.agentGreeting", { name: activeAgent.name })
                        : t("dialog.selectAgent")}
                  </div>
                )}
                {messages.map((m) => (
                  <MessageBubble key={m.id} msg={m} threadId={currentThreadId} />
                ))}
                {sendMutation.isPending && (
                  <div className="bubble bot loading" data-testid="lead-thinking">
                    {t("dialog.thinking", { name: isLeadActive ? "Lead" : activeAgent?.name || "agent" })}
                  </div>
                )}
                <div ref={messagesEndRef} />
              </div>

              <div className="composer">
                <textarea
                  ref={textareaRef}
                  rows={1}
                  placeholder={
                    !hasActive
                      ? t("dialog.composerPlaceholderNone")
                      : isLeadActive
                        ? t("dialog.composerPlaceholderLead")
                        : t("dialog.composerPlaceholder", { name: activeAgent?.name || "" })
                  }
                  value={input}
                  onChange={(e) => setInput(e.target.value)}
                  onKeyDown={(e) => {
                    // Enter 送出、Shift+Enter 換行。isComposing 為 true 代表使用者正在
                    // 用輸入法選字（中文注音/拼音等），這時的 Enter 是 IME 確認鍵而非
                    // 真正的 Enter，不能當作送出處理。
                    if (
                      e.key === "Enter" &&
                      !e.shiftKey &&
                      !e.nativeEvent.isComposing
                    ) {
                      e.preventDefault();
                      send();
                    }
                  }}
                  disabled={!hasActive}
                />
                {sendMutation.isPending ? (
                  <button
                    className="send stop"
                    data-testid="composer-stop"
                    onClick={stopSending}
                    style={{ background: "var(--danger)", borderColor: "var(--danger)" }}
                  >{t("dialog.stop")}</button>
                ) : (
                  <button
                    className="send"
                    data-testid="composer-send"
                    onClick={send}
                    disabled={!input.trim() || !hasActive}
                  >{t("dialog.send")}</button>
                )}
              </div>
            </>
          )}

          {activeTab === "calendar" && (
            <CalendarTab agent={activeAgent || leadAgent} />
          )}

          {activeTab === "settings" && (activeAgent || leadAgent) && (
            <SettingsTab
              agent={activeAgent || leadAgent!}
              subTab={settingsSubTab}
              onSubTab={setSettingsSubTab}
            />
          )}
        </div>
        )}
      </main>

      <div
        className={`cast ${hasActive ? "has-active" : ""}`}
        onClick={(e) => {
          // Click on cast container background (not on a member or handle) closes
          const t = e.target as HTMLElement;
          if (t.closest(".cast-member") || t.closest(".cast-resize-handle")) return;
          setActiveId(null);
        }}
      >
        <button
          className="cast-resize-handle"
          title={t("dialog.resizeHandle")}
          onPointerDown={onResizeHandleDown}
          onPointerMove={onResizeHandleMove}
          onPointerUp={onResizeHandleUp}
          onPointerCancel={onResizeHandleUp}
        >
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
            <polyline points="18 15 12 9 6 15" />
            <polyline points="18 9 12 15 6 9" />
          </svg>
        </button>
        <div
          className="cast-row"
          ref={castRowRef}
          onClick={(e) => {
            // Same: clicking row whitespace between members deselects
            if ((e.target as HTMLElement).closest(".cast-member")) return;
            setActiveId(null);
          }}
        >
          {(() => {
            // Build the natural order: lead first, then other agents.
            // Filter out hidden agents (unless they're currently active).
            const naturalOrder = (leadAgent ? [leadAgent, ...otherAgents] : otherAgents)
              .filter((a) => !hiddenAgents.has(a.id) || (a.is_lead ? activeId === "lead" : `${a.id}` === activeId));
            const ordered = activeId
              ? [
                  ...naturalOrder.filter((a) =>
                    a.is_lead ? activeId === "lead" : `${a.id}` === activeId,
                  ),
                  ...naturalOrder.filter((a) =>
                    a.is_lead ? activeId !== "lead" : `${a.id}` !== activeId,
                  ),
                ]
              : naturalOrder;
            return ordered.map((a) => {
              const isLeadMember = !!a.is_lead;
              const id = isLeadMember ? "lead" : `${a.id}`;
              return (
                <CastMember
                  key={a.id}
                  agent={a}
                  active={activeId === id}
                  activeTab={activeTab}
                  isLead={isLeadMember}
                  pendingCount={isLeadMember ? leadPendingCount : 0}
                  onChat={() => toggleChat(id)}
                  onCalendar={() => selectCalendar(id)}
                  onSettings={() => selectSettings(id)}
                  onHide={() => toggleAgentHidden(a.id)}
                />
              );
            });
          })()}
        </div>
      </div>

    </div>
  );
}

function CalendarTab({ agent }: { agent: Agent | null }) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { data: runs = [] } = useQuery({
    queryKey: ["agent-runs", agent?.id],
    queryFn: () => (agent ? AgentsAPI.runs(agent.id) : Promise.resolve([])),
    enabled: !!agent,
  });
  const { data: threads = [] } = useQuery({
    queryKey: ["agent-threads", agent?.id],
    queryFn: () => (agent ? AgentsAPI.threads(agent.id) : Promise.resolve([])),
    enabled: !!agent,
  });

  // Status labels resolved via t() at render

  if (!agent) {
    return <div className="focus-panel"><div className="empty">{t("dialog.noAgentSelected")}</div></div>;
  }

  return (
    <div className="focus-panel calendar-tab">
      <h3>{t("dialog.recentTasks", { name: agent.name })}</h3>
      {runs.length === 0 ? (
        <div className="empty-card">{t("dialog.noTasks")}</div>
      ) : (
        <div className="calendar-list">
          {runs.map((r) => (
            <button
              key={r.id}
              type="button"
              className={`calendar-row run-${r.status}`}
              onClick={() => navigate(`/runs/${r.id}`)}
            >
              <div className="calendar-row-main">
                <div className="calendar-row-title">{r.workflow_name}</div>
                <div className="calendar-row-meta">
                  Run #{r.id} · {t("dialog.mySteps", { count: r.my_steps })} ·{" "}
                  {new Date(r.started_at).toLocaleString("zh-TW", {
                    month: "numeric", day: "numeric",
                    hour: "2-digit", minute: "2-digit",
                  })}
                </div>
              </div>
              <span className={`calendar-pill run-${r.status}`}>
                {t(`runs.statusLabels.${r.status}`, r.status)}
              </span>
            </button>
          ))}
        </div>
      )}

      <h3 style={{ marginTop: 24 }}>{t("dialog.chatHistory", { name: agent.name })}</h3>
      {threads.length === 0 ? (
        <div className="empty-card">{t("dialog.noChats")}</div>
      ) : (
        <div className="calendar-list">
          {threads.map((th) => (
            <div key={th.thread_id} className="calendar-row thread-row">
              <div className="calendar-row-main">
                <div className="calendar-row-title">
                  {th.title || `Thread ${th.thread_id.slice(0, 6)}`}
                </div>
                <div className="calendar-row-meta">
                  {t("dialog.msgCount", { count: th.msg_count })} · {new Date(th.updated_at).toLocaleString("zh-TW", {
                    month: "numeric", day: "numeric",
                    hour: "2-digit", minute: "2-digit",
                  })}
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

type SettingsSubTab = "overview" | "skills" | "tools";

function SettingsTab({
  agent,
  subTab,
  onSubTab,
}: {
  agent: Agent;
  subTab: SettingsSubTab;
  onSubTab: (st: SettingsSubTab) => void;
}) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const qc = useQueryClient();
  // Re-fetch the full agent record so any saves elsewhere stay fresh
  const { data: liveAgent } = useQuery({
    queryKey: ["agent", agent.id],
    queryFn: () => AgentsAPI.get(agent.id),
    initialData: agent as any,
  });

  // Inline name editing
  const [editingName, setEditingName] = useState(false);
  const [nameDraft, setNameDraft] = useState("");
  const saveName = useMutation({
    mutationFn: (name: string) => AgentsAPI.update(agent.id, { name }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["agent", agent.id] });
      qc.invalidateQueries({ queryKey: ["agents"] });
      setEditingName(false);
    },
  });

  function startEditingName() {
    setNameDraft(liveAgent?.name || agent.name || "");
    setEditingName(true);
  }
  function commitName() {
    const next = nameDraft.trim();
    if (next && next !== (liveAgent?.name || agent.name)) {
      saveName.mutate(next);
    } else {
      setEditingName(false);
    }
  }

  return (
    <div className="focus-panel settings-tab">
      <div className="settings-tab-layout">
        {/* Left sidebar: avatar + editable name */}
        <aside className="settings-tab-side">
          <button
            type="button"
            className="settings-side-avatar"
            title={t("dialog.openFullPage")}
            onClick={() => navigate(`/agents/${agent.id}`)}
          >
            <img
              src={headUrl((liveAgent?.avatar_config || agent.avatar_config) as any)}
              alt={liveAgent?.name || agent.name}
            />
          </button>
          <div className="settings-side-name">
            {editingName ? (
              <input
                autoFocus
                value={nameDraft}
                onChange={(e) => setNameDraft(e.target.value)}
                onBlur={commitName}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.nativeEvent.isComposing) commitName();
                  if (e.key === "Escape") setEditingName(false);
                }}
                disabled={saveName.isPending}
              />
            ) : (
              <>
                <span className="name-text">{liveAgent?.name || agent.name}</span>
                <button
                  type="button"
                  className="name-edit-btn"
                  title={t("dialog.editName")}
                  onClick={startEditingName}
                >
                  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7" />
                    <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z" />
                  </svg>
                </button>
              </>
            )}
          </div>
          <div className="settings-side-role">
            {liveAgent?.role_title || agent.role_title || "—"}
          </div>
        </aside>

        {/* Right pane: sub-tab bar + editor */}
        <div className="settings-tab-main">
          <div className="settings-tab-head">
            <div className="cast-settings-tabs">
              <button
                className={subTab === "overview" ? "active" : ""}
                onClick={() => onSubTab("overview")}
              >{t("dialog.overview")}</button>
              <button
                className={subTab === "skills" ? "active" : ""}
                onClick={() => onSubTab("skills")}
              >{t("dialog.skills")}</button>
              <button
                className={subTab === "tools" ? "active" : ""}
                onClick={() => onSubTab("tools")}
              >{t("dialog.tools")}</button>
            </div>
            <button
              className="mbtn"
              onClick={() => navigate(`/agents/${agent.id}`)}
            >{t("dialog.openFullSettings")}</button>
          </div>
          {liveAgent && subTab === "overview" && (
            <AgentOverviewEditor agent={liveAgent} showAvatar={false} />
          )}
          {subTab === "skills" && <AgentSkillsEditor agentId={agent.id} />}
          {liveAgent && subTab === "tools" && (
            <AgentToolsEditor agent={liveAgent} />
          )}
        </div>
      </div>
    </div>
  );
}

function AgentToolsEditor({ agent }: { agent: Agent }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const { data: tools = [] } = useQuery({
    queryKey: ["tools"],
    queryFn: ToolsAPI.list,
  });
  const selected = new Set<string>(agent.tool_config || []);

  const save = useMutation({
    mutationFn: (next: string[]) =>
      api.put(`/agents/${agent.id}`, { tool_config: next }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["agent", agent.id] });
      qc.invalidateQueries({ queryKey: ["agents"] });
    },
  });

  function toggle(name: string) {
    const next = new Set(selected);
    if (next.has(name)) next.delete(name);
    else next.add(name);
    save.mutate(Array.from(next));
  }

  return (
    <div className="agent-tools-editor">
      <p className="tools-help">
        {t("dialog.toolsHelp")}
      </p>
      <h4 className="tools-section-h">{t("dialog.builtInTools")}</h4>
      {tools.length === 0 ? (
        <div className="empty-card">{t("dialog.noBuiltInTools")}</div>
      ) : (
        <div className="tool-list">
          {tools.map((t) => {
            const on = selected.has(t.name);
            return (
              <label key={t.name} className={`tool-row ${on ? "on" : ""}`}>
                <input
                  type="checkbox"
                  checked={on}
                  onChange={() => toggle(t.name)}
                  disabled={save.isPending}
                />
                <div className="tool-main">
                  <div className="tool-name">{t.name}</div>
                  <div className="tool-desc">{t.description}</div>
                </div>
              </label>
            );
          })}
        </div>
      )}

      <h4 className="tools-section-h" style={{ marginTop: 24 }}>{t("dialog.externalMcp")}</h4>
      <p className="tools-help" style={{ marginBottom: 12 }}>
        {t("dialog.mcpHelp")}
      </p>
      <McpServerList agentId={agent.id} />
    </div>
  );
}

function McpServerList({ agentId }: { agentId: number }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const { data: servers = [] } = useQuery({
    queryKey: ["mcp-servers", agentId],
    queryFn: () => McpAPI.list(agentId),
  });
  const [adding, setAdding] = useState(false);
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [authHeader, setAuthHeader] = useState("");
  const [probeResult, setProbeResult] = useState<Record<number, { ok: boolean; msg: string }>>({});

  const create = useMutation({
    mutationFn: () =>
      McpAPI.create(agentId, {
        name: name.trim(),
        url: url.trim(),
        auth_header: authHeader.trim() || undefined,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["mcp-servers", agentId] });
      setAdding(false);
      setName(""); setUrl(""); setAuthHeader("");
    },
  });
  const del = useMutation({
    mutationFn: (sid: number) => McpAPI.delete(agentId, sid),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["mcp-servers", agentId] }),
  });
  const toggleEnabled = useMutation({
    mutationFn: (args: { sid: number; enabled: boolean }) =>
      McpAPI.update(agentId, args.sid, { enabled: args.enabled }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["mcp-servers", agentId] }),
  });
  async function probe(sid: number) {
    const r = await McpAPI.probe(agentId, sid);
    setProbeResult((prev) => ({
      ...prev,
      [sid]: r.ok
        ? { ok: true, msg: t("dialog.probeOk", { count: r.count }) }
        : { ok: false, msg: r.error || t("dialog.probeFail") },
    }));
  }

  return (
    <div className="mcp-server-list">
      {servers.length === 0 && !adding && (
        <div className="empty-card" style={{ marginBottom: 10 }}>{t("dialog.noMcpServers")}</div>
      )}
      {servers.map((s) => {
        const probed = probeResult[s.id];
        return (
          <div key={s.id} className={`mcp-row ${s.enabled ? "on" : "off"}`}>
            <div className="mcp-main">
              <div className="mcp-name">
                {s.name}
                {s.has_auth && <span className="mcp-badge">{t("dialog.hasAuth")}</span>}
              </div>
              <div className="mcp-url">{s.url}</div>
              {probed && (
                <div className={`mcp-probe ${probed.ok ? "ok" : "err"}`}>
                  {probed.msg}
                </div>
              )}
            </div>
            <div className="mcp-actions">
              <button
                className="mbtn"
                onClick={() => probe(s.id)}
                style={{ padding: "4px 10px", fontSize: 10 }}
              >{t("dialog.testConn")}
              </button>
              <button
                className="mbtn"
                onClick={() => toggleEnabled.mutate({ sid: s.id, enabled: !s.enabled })}
                style={{ padding: "4px 10px", fontSize: 10 }}
              >
                {s.enabled ? t("schedules.disable") : t("schedules.enable")}
              </button>
              <button
                className="mbtn danger"
                onClick={() => del.mutate(s.id)}
                style={{ padding: "4px 10px", fontSize: 10 }}
              >{t("btn.delete")}</button>
            </div>
          </div>
        );
      })}

      {adding ? (
        <div className="mcp-add-form">
          <div className="modal-field">
            <label>{t("dialog.mcpName")}</label>
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="github / filesystem / ..."
            />
          </div>
          <div className="modal-field">
            <label>URL</label>
            <input
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              placeholder="https://mcp.example.com/rpc"
            />
          </div>
          <div className="modal-field">
            <label>{t("dialog.mcpAuth")}</label>
            <input
              value={authHeader}
              onChange={(e) => setAuthHeader(e.target.value)}
              placeholder="Bearer sk-..."
              type="password"
            />
          </div>
          <div style={{ display: "flex", gap: 6, justifyContent: "flex-end" }}>
            <button
              className="mbtn"
              onClick={() => { setAdding(false); setName(""); setUrl(""); setAuthHeader(""); }}
            >{t("btn.cancel")}</button>
            <button
              className="mbtn primary"
              disabled={!name.trim() || !url.trim() || create.isPending}
              onClick={() => create.mutate()}
            >
              {create.isPending ? t("btn.saving") : t("dialog.addMcp")}
            </button>
          </div>
        </div>
      ) : (
        <button
          className="mbtn"
          onClick={() => setAdding(true)}
          style={{ marginTop: 8 }}
        >{t("dialog.addMcp")}
        </button>
      )}
    </div>
  );
}

// ============================================================================

function CastMember({
  agent, active, activeTab, isLead, pendingCount, onChat, onCalendar, onSettings, onHide,
}: {
  agent: Agent;
  active: boolean;
  activeTab: CastTab;
  isLead?: boolean;
  pendingCount?: number;
  onChat: () => void;
  onCalendar: () => void;
  onSettings: () => void;
  onHide: () => void;
}) {
  const { t } = useTranslation();
  const id = isLead ? "lead" : `${agent.id}`;
  const statusClass =
    agent.status === "active" ? "online" :
    agent.status === "quota_exceeded" || agent.status === "budget_exceeded" ? "warn" :
    agent.status === "off_duty" ? "off" : "busy";
  const hasPending = (pendingCount ?? 0) > 0;
  const isChatActive = active && activeTab === "chat";
  const isCalendarActive = active && activeTab === "calendar";
  const [ctxOpen, setCtxOpen] = useState(false);
  const [ctxPos, setCtxPos] = useState({ x: 0, y: 0 });

  useEffect(() => {
    if (!ctxOpen) return;
    const close = () => setCtxOpen(false);
    window.addEventListener("click", close);
    return () => window.removeEventListener("click", close);
  }, [ctxOpen]);

  return (
    <div
      data-id={id}
      className={`cast-member ${isLead ? "lead" : ""} ${active ? "active" : ""} ${hasPending && !active ? "has-ping" : ""}`}
      onClick={onChat}
      onContextMenu={(e) => {
        if (isLead) return;
        e.preventDefault();
        setCtxPos({ x: e.clientX, y: e.clientY });
        setCtxOpen(true);
      }}
    >
      {ctxOpen && (
        <div
          style={{
            position: "fixed", left: ctxPos.x, top: ctxPos.y, zIndex: 100,
            background: "var(--surface)", border: "1px solid var(--border)",
            borderRadius: 10, padding: 4, minWidth: 120,
            boxShadow: "0 4px 16px rgba(0,0,0,0.12)",
          }}
          onClick={(e) => e.stopPropagation()}
        >
          <button
            style={{
              display: "block", width: "100%", background: "none", border: "none",
              padding: "7px 12px", fontSize: 12, textAlign: "left", borderRadius: 6,
              cursor: "pointer", color: "var(--ink)",
            }}
            onMouseOver={(e) => (e.currentTarget.style.background = "var(--surface-2)")}
            onMouseOut={(e) => (e.currentTarget.style.background = "none")}
            onClick={() => { onHide(); setCtxOpen(false); }}
          >{t("dialog.hide")}
          </button>
        </div>
      )}
      <div className="cast-actions" onClick={(e) => e.stopPropagation()}>
        <button
          className={`cast-action-btn ${isCalendarActive ? "is-active" : ""}`}
          title="Tasks / Chat history"
          data-testid={`cast-calendar-${id}`}
          onClick={onCalendar}
        >
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
            <rect x="3" y="4" width="18" height="18" rx="2" />
            <line x1="16" y1="2" x2="16" y2="6" />
            <line x1="8" y1="2" x2="8" y2="6" />
            <line x1="3" y1="10" x2="21" y2="10" />
          </svg>
        </button>
        <button
          className={`cast-action-btn ${isChatActive ? "is-active" : ""}`}
          title="Start conversation"
          data-testid={`cast-chat-${id}`}
          onClick={onChat}
        >
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
          </svg>
          {hasPending && <span className="chat-ping" data-testid={`ping-${id}`} />}
        </button>
        <button
          className="cast-action-btn"
          title="Settings"
          data-testid={`cast-settings-${id}`}
          onClick={onSettings}
        >
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
            <circle cx="12" cy="12" r="3" />
            <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
          </svg>
        </button>
      </div>
      <div className="bust">
        <img src={bustUrl(agent.avatar_config, true)} alt={agent.name} loading="lazy" />
      </div>
      <div className="info">
        <div className="name-line">
          <span className="name">{agent.name}</span>
          <span className={`status-dot ${statusClass}`}></span>
        </div>
        <div className="role-line">{agent.role_title || ""}</div>
      </div>
    </div>
  );
}

function MessageBubble({ msg, threadId }: { msg: LeadMessage; threadId?: string }) {
  // Strip both the workflow and hire fenced blocks from display — they're
  // rendered as dedicated cards below instead of raw JSON in the prose.
  const cleanContent = msg.content
    .replace(/```workflow\s*\n[\s\S]*?\n```/g, "")
    .replace(/```hire\s*\n[\s\S]*?\n```/g, "")
    .trim();
  const isRunEvent = msg.metadata?.event === "run_event" && msg.metadata?.run_id;
  const hireProposal = msg.metadata?.proposed_hire;
  const hiredAgentId = msg.metadata?.hired_agent_id;
  const isWide = msg.proposed_workflow_id || hireProposal;

  return (
    <div className={`bubble ${msg.role === "user" ? "user" : "bot"} ${isWide ? "wide" : ""} ${isRunEvent ? "run-event" : ""}`}>
      {isRunEvent && msg.metadata?.run_id ? (
        <RunStatusCard
          runId={msg.metadata.run_id}
          workflowName={msg.metadata.workflow_name}
        />
      ) : (
        <div className="content markdown">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{cleanContent}</ReactMarkdown>
        </div>
      )}
      {msg.proposed_workflow_id && (
        <WorkflowBubble workflowId={msg.proposed_workflow_id} threadId={threadId} />
      )}
      {hireProposal && (
        <HireBubble
          messageId={msg.id}
          proposal={hireProposal}
          hiredAgentId={hiredAgentId}
        />
      )}
      <div className="meta">{new Date(msg.created_at).toLocaleTimeString("zh-TW", { hour: "2-digit", minute: "2-digit" })}</div>
    </div>
  );
}

function RunStatusCard({ runId, workflowName }: { runId: number; workflowName?: string }) {
  const navigate = useNavigate();
  const { data: run } = useQuery({
    queryKey: ["run", runId],
    queryFn: () => RunsAPI.get(runId),
    refetchInterval: (q) => {
      const r = q.state.data as { status?: string } | undefined;
      if (!r) return 3_000;
      return ["running", "queued", "cancelling", "paused"].includes(r.status || "") ? 3_000 : false;
    },
  });

  const status = run?.status || "queued";
  const isActive = ["running", "queued", "cancelling", "paused"].includes(status);
  const isDone = status === "done";
  const isError = status === "error" || status === "cancelled";
  const cls = isActive ? "active" : isDone ? "done" : isError ? "error" : "neutral";

  const STATUS_LABEL: Record<string, string> = {
    queued: "Queued",
    running: "Running",
    paused: "Paused",
    cancelling: "Cancelling",
    cancelled: "Cancelled",
    done: "Done",
    error: "Failed",
  };

  const stepCount = (run as any)?.steps?.length ?? 0;
  // Postgres NUMERIC / BIGINT can come back as strings — coerce defensively.
  const tokens = (Number(run?.total_input_tokens) || 0) + (Number(run?.total_output_tokens) || 0);
  const cost = Number(run?.total_cost_usd) || 0;
  const startedAt = run?.started_at ? new Date(run.started_at) : null;
  const finishedAt = run?.finished_at ? new Date(run.finished_at) : null;
  const seconds = startedAt && finishedAt
    ? (finishedAt.getTime() - startedAt.getTime()) / 1000
    : startedAt ? (Date.now() - startedAt.getTime()) / 1000 : 0;
  const durationStr = seconds < 60 ? `${seconds.toFixed(1)}s` : `${(seconds / 60).toFixed(1)}m`;

  return (
    <div className={`run-status-card ${cls}`}>
      <div className="run-status-head">
        <div className="run-status-title">
          {isActive && <span className="spinner" />}
          {workflowName || `Workflow #${run?.workflow_id ?? ""}`}
        </div>
        <span className={`run-status-pill ${cls}`}>{STATUS_LABEL[status] || status}</span>
      </div>
      <div className="run-status-stats">
        <div><strong>Run</strong>#{runId}</div>
        <div><strong>Steps</strong>{stepCount}</div>
        <div><strong>Tokens</strong>{tokens.toLocaleString()}</div>
        <div><strong>Cost</strong>${cost.toFixed(4)}</div>
        <div><strong>Duration</strong>{durationStr}</div>
      </div>
      <button
        type="button"
        className="run-status-link"
        onClick={() => navigate(`/runs/${runId}`)}
      >
        View Details
      </button>
    </div>
  );
}
