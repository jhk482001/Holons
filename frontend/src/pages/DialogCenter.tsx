import { useTranslation } from "react-i18next";
import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery, useInfiniteQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useNavigate, useSearchParams } from "react-router-dom";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { AgentsAPI, DashboardAPI, LeadAPI, McpAPI, RunsAPI, ToolsAPI, api, bustUrl, headUrl, Agent, LeadMessage } from "../api/client";
import WorkflowBubble from "../components/WorkflowBubble";
import HireBubble from "../components/HireBubble";
import "../components/HireBubble.css";
import ArtifactBubble from "../components/ArtifactBubble";
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

  // Per-agent queue depth drives the "busy" chest pill on each cast bust.
  // Polled more aggressively than the agent list since busy-ness is the
  // most time-sensitive visual signal in the cast.
  const { data: agentLoad = [] } = useQuery({
    queryKey: ["agent-load"],
    queryFn: DashboardAPI.agentLoad,
    refetchInterval: 5_000,
  });
  const busyByAgentId = new Map<number, boolean>();
  for (const row of agentLoad) busyByAgentId.set(row.id, (row.queue_depth || 0) > 0);

  const { data: leadPending } = useQuery({
    queryKey: ["lead-pending"],
    queryFn: LeadAPI.pendingCount,
    refetchInterval: 8_000,
  });
  const leadPendingCount = leadPending?.count ?? 0;

  // Cast layout (shared with desktop) — used for hidden_agents + facing.
  // `facing` keys are stringified agent ids so the JSON round-trips cleanly.
  const { data: castLayout } = useQuery<{
    hidden_agents?: number[];
    facing?: Record<string, "left" | "right">;
    [k: string]: unknown;
  }>({
    queryKey: ["cast-layout"],
    queryFn: async () => {
      const r = await fetch("/api/me/cast_layout", { credentials: "include" });
      return r.ok ? r.json() : {};
    },
  });
  const hiddenAgents = new Set(castLayout?.hidden_agents || []);
  const facingMap = castLayout?.facing || {};
  const saveCastLayout = (next: Record<string, unknown>) => {
    fetch("/api/me/cast_layout", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify(next),
    }).then(() => qc.invalidateQueries({ queryKey: ["cast-layout"] }));
  };
  const toggleAgentHidden = (agentId: number) => {
    const current = castLayout?.hidden_agents || [];
    const next = current.includes(agentId)
      ? current.filter((id: number) => id !== agentId)
      : [...current, agentId];
    saveCastLayout({ ...castLayout, hidden_agents: next });
  };
  const setAgentFacing = (agentId: number, dir: "left" | "right") => {
    const nextFacing = { ...facingMap, [String(agentId)]: dir };
    saveCastLayout({ ...castLayout, facing: nextFacing });
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
  // Auto-grow the composer textarea so multi-line drafts stay visible.
  // CSS caps it at max-height; the textarea will scroll past that.
  useEffect(() => {
    const ta = textareaRef.current;
    if (!ta) return;
    ta.style.height = "auto";
    ta.style.height = `${ta.scrollHeight}px`;
  }, [input]);

  // Mark a Lead thread as read whenever the user is actively viewing
  // it. Without this the cast-bar unread badge would only clear after
  // the user types a reply — broken for messages they can't naturally
  // reply to (project reports, run-complete summaries). Re-fires when
  // new messages arrive in the open thread so a streaming Lead reply
  // doesn't leave a stale badge.
  useEffect(() => {
    if (!isLeadActive || !currentThreadId) return;
    LeadAPI.markRead(currentThreadId)
      .then(() => qc.invalidateQueries({ queryKey: ["lead-pending"] }))
      .catch(() => { /* best-effort; the existing 8s poll catches up */ });
  }, [isLeadActive, currentThreadId, messages.length, qc]);

  // ---- Expanded artifact-panel mode (Claude.ai-style) ----
  // `expanded` flips the layout: cast bar hidden, conversation column
  // takes full height with its composer pinned at the bottom, and an
  // optional artifact panel slides in from the right when the user
  // clicks a run / artifact / proposal. Two entry points:
  //   1. Expand-icon button on the chat header (composer-side toggle).
  //   2. Clicking a RunStatusCard or ArtifactBubble — auto-opens the
  //      panel with that target, and flips `expanded` on if it isn't.
  const [expanded, setExpanded] = useState(false);
  // What to show in the right artifact panel. `null` = panel closed.
  // Discriminated by `kind`. We deliberately keep this shape extensible
  // so future artifact types (slides, files) drop in here too.
  type ArtifactTarget =
    | { kind: "run"; runId: number; workflowName?: string }
    | { kind: "html"; title: string; html: string }
    | { kind: "markdown"; title: string; md: string }
    | { kind: "file"; title: string; payload: any };
  const [panelTarget, setPanelTarget] = useState<ArtifactTarget | null>(null);

  function openInPanel(target: ArtifactTarget) {
    setPanelTarget(target);
    if (!expanded) setExpanded(true);
  }
  function closePanel() {
    setPanelTarget(null);
  }
  function toggleExpanded() {
    if (expanded) {
      setPanelTarget(null);
      setExpanded(false);
    } else {
      setExpanded(true);
    }
  }
  // Safety: if the active chat ever clears while expanded (e.g. via a future
  // codepath), collapse so the cast bar comes back — otherwise the user
  // sees a blank screen with no way to recover.
  useEffect(() => {
    if (!activeId && expanded) {
      setExpanded(false);
      setPanelTarget(null);
    }
  }, [activeId, expanded]);
  // Live stream buffer — filled chunk-by-chunk while a Lead streaming
  // request is in flight, cleared when the final result has landed in
  // the cache. Rendered as a bubble below the committed messages so the
  // user sees text arrive token-by-token.
  const [streamingText, setStreamingText] = useState("");

  const sendMutation = useMutation({
    mutationFn: async (text: string) => {
      const ctrl = new AbortController();
      abortRef.current = ctrl;
      try {
        if (isLeadActive) {
          // Streaming path — accumulate chunks into `streamingText` so
          // the user sees text arrive token-by-token. The final
          // structured result (with proposed_workflow / hire / project
          // / artifacts) comes back at stream close and flows into
          // onSuccess where the cache gets its real row written.
          setStreamingText("");
          let buf = "";
          return await LeadAPI.chatStreaming(
            text,
            currentThreadId,
            {
              onChunk: (delta) => {
                buf += delta;
                setStreamingText(buf);
              },
              onError: (msg) => { console.warn("[lead-stream]", msg); },
            },
            ctrl.signal,
          );
        }
        if (!activeAgent) throw new Error("no active agent");
        return await AgentsAPI.chatWithSignal(activeAgent.id, text, currentThreadId, ctrl.signal);
      } finally {
        abortRef.current = null;
      }
    },
    // Optimistic update — drop the user's message into the cache right
    // away + add a "thinking" assistant placeholder so the chat feels
    // responsive. The mutation still roundtrips to the server; onSuccess
    // collapses the cache + invalidates so the real rows overwrite
    // these placeholders. onError rolls back.
    onMutate: async (text: string) => {
      const threadId = currentThreadId;
      if (!threadId) return { rollback: false };
      await qc.cancelQueries({ queryKey: ["messages", threadId] });
      const previous = qc.getQueryData<{
        pages: { messages: LeadMessage[]; has_more: boolean }[];
        pageParams: (number | undefined)[];
      }>(["messages", threadId]);
      const now = new Date().toISOString();
      const optimisticUser: LeadMessage = {
        id: -Date.now(),                      // negative id flags it as optimistic
        role: "user",
        content: text,
        proposed_workflow_id: null,
        cancelled: false,
        created_at: now,
        metadata: { optimistic: true } as any,
      };
      const optimisticThinking: LeadMessage = {
        id: -(Date.now() + 1),
        role: "lead",
        content: "…",
        proposed_workflow_id: null,
        cancelled: false,
        created_at: now,
        metadata: { optimistic: true, thinking: true } as any,
      };
      qc.setQueryData(["messages", threadId], (old: any) => {
        if (!old) {
          return {
            pages: [{ messages: [optimisticUser, optimisticThinking], has_more: false }],
            pageParams: [undefined],
          };
        }
        const lastPage = old.pages[old.pages.length - 1] ?? { messages: [], has_more: false };
        const newLast = {
          ...lastPage,
          messages: [...lastPage.messages, optimisticUser, optimisticThinking],
        };
        return {
          ...old,
          pages: [...old.pages.slice(0, -1), newLast],
        };
      });
      return { previous, threadId, rollback: true };
    },
    onError: (_err, _text, ctx: any) => {
      // Clear the live stream buffer so we don't leave a half-written
      // bubble behind when the connection drops or the user hits stop.
      setStreamingText("");
      if (ctx?.rollback && ctx?.threadId && ctx.previous !== undefined) {
        qc.setQueryData(["messages", ctx.threadId], ctx.previous);
      }
    },
    onSuccess: (data) => {
      // Streaming complete → drop the live buffer; the real row lands
      // via the messages invalidation below.
      setStreamingText("");
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

  // After Hire is accepted on a Lead-proposal bubble, drop a canned
  // "approved — please continue" turn into the thread so Lead picks
  // up the conversation. Without this the user has to type their
  // own ack between every hire when Lead proposes several candidates
  // in a row, which is friction.
  function handleHireAccepted(info: { agent_id: number; name: string; role_title: string }) {
    // Defer the auto-send until the current send (if any) finishes —
    // sendMutation only allows one in-flight call.
    setTimeout(() => {
      const canned = t("dialog.afterHireApproved", {
        name: info.name,
        role: info.role_title,
      });
      try {
        sendMutation.mutate(canned);
      } catch {
        // Ignore — user can still type follow-up manually.
      }
    }, 250);
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

  // Cast row horizontal overflow indicators. Arrow buttons (left/right)
  // appear whenever the row has more members than fit horizontally, which
  // happens on narrow screens with many agents.
  const [canScrollLeft, setCanScrollLeft] = useState(false);
  const [canScrollRight, setCanScrollRight] = useState(false);
  useEffect(() => {
    const row = castRowRef.current;
    if (!row) return;
    const update = () => {
      const maxScroll = row.scrollWidth - row.clientWidth;
      setCanScrollLeft(row.scrollLeft > 1);
      setCanScrollRight(row.scrollLeft < maxScroll - 1);
    };
    update();
    row.addEventListener("scroll", update, { passive: true });
    const ro = new ResizeObserver(update);
    ro.observe(row);
    window.addEventListener("resize", update);
    return () => {
      row.removeEventListener("scroll", update);
      ro.disconnect();
      window.removeEventListener("resize", update);
    };
  }, [agents.length, castSize]);
  const scrollCast = (delta: number) => {
    castRowRef.current?.scrollBy({ left: delta, behavior: "smooth" });
  };

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
      className={`dc ${isBadgeMode ? "badge-mode" : ""} ${expanded ? "dc-expanded" : ""} ${panelTarget ? "dc-panel-open" : ""}`}
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
          // closes the message area. Disabled in expanded mode — the cast
          // bar is hidden there, so closing would leave a blank screen
          // with no way back in.
          if (expanded) return;
          if (e.target === e.currentTarget) setActiveId(null);
        }}
      >
        {hasActive && (
        <div
          className="focus-lane"
          // Stop clicks inside the chat panel from bubbling to the cast
          // row / stage whose onClick handlers treat "click outside agent"
          // as dismiss. Without this, clicking the textarea closed the
          // chat.
          onClick={(e) => e.stopPropagation()}
        >
          {activeTab === "chat" && (
            <>
              <div className="focus-lane-toolbar">
                {expanded && (activeAgent || leadAgent) && (
                  <>
                    <div className="focus-lane-agent">
                      <img
                        className="focus-lane-agent-avatar"
                        src={headUrl(((activeAgent || leadAgent) as Agent).avatar_config as any)}
                        alt=""
                      />
                      <div className="focus-lane-agent-text">
                        <div className="focus-lane-agent-name">{(activeAgent || leadAgent)?.name}</div>
                        {(activeAgent || leadAgent)?.role_title && (
                          <div className="focus-lane-agent-role">{(activeAgent || leadAgent)?.role_title}</div>
                        )}
                      </div>
                    </div>
                    <button
                      type="button"
                      className="focus-lane-threads"
                      title={t("dialog.threads")}
                      onClick={() => setDrawerOpen((v) => !v)}
                    >
                      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                        <line x1="3" y1="6" x2="21" y2="6" />
                        <line x1="3" y1="12" x2="21" y2="12" />
                        <line x1="3" y1="18" x2="21" y2="18" />
                      </svg>
                      <span>{threads.length}</span>
                    </button>
                  </>
                )}
                <button
                  type="button"
                  className="focus-lane-expand"
                  data-testid="dialog-expand-toggle"
                  title={expanded ? t("dialog.collapsePanel") : t("dialog.expandPanel")}
                  aria-label={expanded ? t("dialog.collapsePanel") : t("dialog.expandPanel")}
                  onClick={toggleExpanded}
                >
                  {expanded ? (
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                      <polyline points="4 14 10 14 10 20" />
                      <polyline points="20 10 14 10 14 4" />
                      <line x1="14" y1="10" x2="21" y2="3" />
                      <line x1="3" y1="21" x2="10" y2="14" />
                    </svg>
                  ) : (
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                      <polyline points="15 3 21 3 21 9" />
                      <polyline points="9 21 3 21 3 15" />
                      <line x1="21" y1="3" x2="14" y2="10" />
                      <line x1="3" y1="21" x2="10" y2="14" />
                    </svg>
                  )}
                </button>
              </div>
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
                  <MessageBubble
                    key={m.id}
                    msg={m}
                    threadId={currentThreadId}
                    onHireAccepted={isLeadActive ? handleHireAccepted : undefined}
                    onOpenArtifact={openInPanel}
                  />
                ))}
                {sendMutation.isPending && (
                  streamingText ? (
                    <div className="bubble bot streaming" data-testid="lead-streaming">
                      <div className="content markdown">
                        <ReactMarkdown remarkPlugins={[remarkGfm]}>{streamingText}</ReactMarkdown>
                      </div>
                      <span className="streaming-cursor" aria-hidden="true">▍</span>
                    </div>
                  ) : (
                    <div className="bubble bot loading" data-testid="lead-thinking">
                      {t("dialog.thinking", { name: isLeadActive ? "Lead" : activeAgent?.name || "agent" })}
                    </div>
                  )
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

      {panelTarget && (
        <ArtifactPanel
          target={panelTarget}
          onClose={closePanel}
        />
      )}

      <div
        className={`cast ${hasActive ? "has-active" : ""}`}
        onClick={(e) => {
          // Click on cast container background (not on a member or handle) closes
          const t = e.target as HTMLElement;
          if (t.closest(".cast-member") || t.closest(".cast-resize-handle") || t.closest(".cast-nav-btn")) return;
          setActiveId(null);
        }}
      >
        {canScrollLeft && (
          <button
            className="cast-nav-btn left"
            aria-label="Scroll cast left"
            onClick={() => scrollCast(-220)}
          >
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round">
              <polyline points="15 18 9 12 15 6" />
            </svg>
          </button>
        )}
        {canScrollRight && (
          <button
            className="cast-nav-btn right"
            aria-label="Scroll cast right"
            onClick={() => scrollCast(220)}
          >
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round">
              <polyline points="9 18 15 12 9 6" />
            </svg>
          </button>
        )}
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
                  facing={facingMap[String(a.id)] === "left" ? "left" : "right"}
                  busy={busyByAgentId.get(a.id) || false}
                  onChat={() => toggleChat(id)}
                  onCalendar={() => selectCalendar(id)}
                  onSettings={() => selectSettings(id)}
                  onHide={() => toggleAgentHidden(a.id)}
                  onFace={(dir) => setAgentFacing(a.id, dir)}
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
  agent, active, activeTab, isLead, pendingCount, facing = "right", busy = false,
  onChat, onCalendar, onSettings, onHide, onFace,
}: {
  agent: Agent;
  active: boolean;
  activeTab: CastTab;
  isLead?: boolean;
  pendingCount?: number;
  facing?: "left" | "right";
  busy?: boolean;
  onChat: () => void;
  onCalendar: () => void;
  onSettings: () => void;
  onHide: () => void;
  onFace?: (dir: "left" | "right") => void;
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
            borderRadius: 10, padding: 4, minWidth: 140,
            boxShadow: "0 4px 16px rgba(0,0,0,0.12)",
          }}
          onClick={(e) => e.stopPropagation()}
        >
          <button
            style={{
              display: "block", width: "100%", background: facing === "right" ? "var(--surface-2)" : "none",
              border: "none", padding: "7px 12px", fontSize: 12, textAlign: "left",
              borderRadius: 6, cursor: "pointer", color: "var(--ink)",
            }}
            onMouseOver={(e) => (e.currentTarget.style.background = "var(--surface-2)")}
            onMouseOut={(e) => (e.currentTarget.style.background = facing === "right" ? "var(--surface-2)" : "none")}
            onClick={() => { onFace?.("right"); setCtxOpen(false); }}
          >{t("dialog.faceRight")}
          </button>
          <button
            style={{
              display: "block", width: "100%", background: facing === "left" ? "var(--surface-2)" : "none",
              border: "none", padding: "7px 12px", fontSize: 12, textAlign: "left",
              borderRadius: 6, cursor: "pointer", color: "var(--ink)",
            }}
            onMouseOver={(e) => (e.currentTarget.style.background = "var(--surface-2)")}
            onMouseOut={(e) => (e.currentTarget.style.background = facing === "left" ? "var(--surface-2)" : "none")}
            onClick={() => { onFace?.("left"); setCtxOpen(false); }}
          >{t("dialog.faceLeft")}
          </button>
          {!isLead && (
            <>
              <div style={{ height: 1, background: "var(--border)", margin: "4px 0" }} />
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
            </>
          )}
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
      <div className={`bust ${busy ? "is-busy" : ""}`}>
        <img
          src={bustUrl(agent.avatar_config, true)}
          alt={agent.name}
          loading="lazy"
          style={facing === "left" ? { transform: "scaleX(-1)" } : undefined}
        />
        {busy && (
          <span className="bust-busy-pill" aria-label={t("dialog.busy")}>
            {t("dialog.busy")}<span className="bust-busy-dots"><span>.</span><span>.</span><span>.</span></span>
          </span>
        )}
        {hasPending && !active && (
          <span
            className="cast-unread-badge"
            data-testid={`unread-${id}`}
            aria-label={`${pendingCount} unread`}
          >
            {(pendingCount ?? 0) > 9 ? "9+" : pendingCount}
          </span>
        )}
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

type OpenArtifactFn = (target:
  | { kind: "run"; runId: number; workflowName?: string }
  | { kind: "html"; title: string; html: string }
  | { kind: "markdown"; title: string; md: string }
  | { kind: "file"; title: string; payload: any }
) => void;

function MessageBubble({ msg, threadId, onHireAccepted, onOpenArtifact }: {
  msg: LeadMessage;
  threadId?: string;
  onHireAccepted?: (info: { agent_id: number; name: string; role_title: string }) => void;
  onOpenArtifact?: OpenArtifactFn;
}) {
  const createdAtLabel = new Date(msg.created_at).toLocaleTimeString("zh-TW", { hour: "2-digit", minute: "2-digit" });
  // Strip every fenced block that's rendered as its own card (workflow,
  // hire, project, and the three artifact kinds) from the prose.
  const cleanContent = msg.content
    .replace(/```workflow\s*\n[\s\S]*?\n```/g, "")
    .replace(/```hire\s*\n[\s\S]*?\n```/g, "")
    .replace(/```project\s*\n[\s\S]*?\n```/g, "")
    .replace(/```artifact-(?:html|slides|file|markdown)(?:\s+[^\n]+)?\s*\n[\s\S]*?\n```/g, "")
    .trim();
  // Run-completion messages — backend writes `event: "run_complete"`
  // or `"run_failed"`. We also accept the legacy `"run_event"` tag, so
  // older rows still render as the structured status card.
  const isRunEvent = !!msg.metadata?.run_id && (
    msg.metadata?.event === "run_event"
    || msg.metadata?.event === "run_complete"
    || msg.metadata?.event === "run_failed"
  );
  const runId = msg.metadata?.run_id as number | undefined;
  // The run-event prose carries the workflow name as `**<name>**` in the
  // first line. Extract it as a fallback for older rows that didn't include
  // `workflow_name` in metadata.
  const runWorkflowName =
    (msg.metadata?.workflow_name as string | undefined)
    ?? msg.content.match(/^The \*\*(.+?)\*\* run you dispatched/)?.[1];
  const hireProposal = msg.metadata?.proposed_hire;
  const hiredAgentId = msg.metadata?.hired_agent_id;
  const artifacts = msg.metadata?.artifacts || [];
  const isWide = msg.proposed_workflow_id || hireProposal || artifacts.length > 0;

  return (
    <div className={`bubble ${msg.role === "user" ? "user" : "bot"} ${isWide ? "wide" : ""} ${isRunEvent ? "run-event" : ""}`}>
      {isRunEvent && runId ? (
        <RunStatusCard
          runId={runId}
          workflowName={runWorkflowName}
          createdAtLabel={createdAtLabel}
          onOpenArtifact={onOpenArtifact}
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
          onAccepted={onHireAccepted}
        />
      )}
      {artifacts.map((a, i) => (
        <ArtifactBubble key={i} artifact={a} onOpenArtifact={onOpenArtifact} />
      ))}
      {!isRunEvent && (
        <div className="meta">{createdAtLabel}</div>
      )}
    </div>
  );
}

function RunStatusCard({ runId, workflowName, createdAtLabel, onOpenArtifact }: {
  runId: number;
  workflowName?: string;
  createdAtLabel?: string;
  onOpenArtifact?: OpenArtifactFn;
}) {
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

  const handleOpenInPanel = () => {
    if (onOpenArtifact) {
      onOpenArtifact({ kind: "run", runId, workflowName });
    } else {
      navigate(`/runs/${runId}`);
    }
  };

  // Workflow name preference: explicit prop (parsed from metadata or
  // content) → run row's `workflow_name` (returned by the runs endpoint
  // alongside steps) → numeric fallback.
  const displayName = workflowName || (run as any)?.workflow_name || `Workflow #${run?.workflow_id ?? ""}`;

  return (
    <div
      className={`run-status-card ${cls} ${onOpenArtifact ? "clickable" : ""}`}
      onClick={onOpenArtifact ? handleOpenInPanel : undefined}
      role={onOpenArtifact ? "button" : undefined}
      tabIndex={onOpenArtifact ? 0 : undefined}
      onKeyDown={onOpenArtifact ? (e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          handleOpenInPanel();
        }
      } : undefined}
    >
      <div className="run-status-row">
        {isActive && <span className="spinner" />}
        <svg className="run-status-icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <polyline points="9 11 12 14 22 4" />
          <path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11" />
        </svg>
        <span className="run-status-name" title={displayName}>{displayName}</span>
        <span className="run-status-runid">#{runId}</span>
        <span className={`run-status-pill ${cls}`}>{STATUS_LABEL[status] || status}</span>
        <span className="run-status-spacer" />
        {createdAtLabel && (
          <span className="run-status-time">{createdAtLabel}</span>
        )}
        {onOpenArtifact && (
          <button
            type="button"
            className="run-status-open"
            aria-label="Open in panel"
            title="Open in panel"
            onClick={(e) => { e.stopPropagation(); handleOpenInPanel(); }}
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
              <polyline points="9 18 15 12 9 6" />
            </svg>
          </button>
        )}
      </div>
    </div>
  );
}

function ArtifactPanel({ target, onClose }: {
  target:
    | { kind: "run"; runId: number; workflowName?: string }
    | { kind: "html"; title: string; html: string }
    | { kind: "markdown"; title: string; md: string }
    | { kind: "file"; title: string; payload: any };
  onClose: () => void;
}) {
  const navigate = useNavigate();
  return (
    <aside className="artifact-panel" data-testid="artifact-panel">
      <header className="artifact-panel-head">
        <div className="artifact-panel-title" title={
          target.kind === "run"
            ? (target.workflowName || `Run #${target.runId}`)
            : target.title
        }>
          {target.kind === "run" && (
            <span className="artifact-panel-kind">Run</span>
          )}
          {target.kind === "html" && (
            <span className="artifact-panel-kind">HTML</span>
          )}
          {target.kind === "markdown" && (
            <span className="artifact-panel-kind">Markdown</span>
          )}
          {target.kind === "file" && (
            <span className="artifact-panel-kind">File</span>
          )}
          <span className="artifact-panel-name">
            {target.kind === "run"
              ? (target.workflowName || `Run #${target.runId}`)
              : target.title}
          </span>
        </div>
        {target.kind === "run" && (
          <button
            type="button"
            className="artifact-panel-action"
            title="Open full Run page"
            onClick={() => navigate(`/runs/${target.runId}`)}
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6" />
              <polyline points="15 3 21 3 21 9" />
              <line x1="10" y1="14" x2="21" y2="3" />
            </svg>
          </button>
        )}
        <button
          type="button"
          className="artifact-panel-close"
          aria-label="Close"
          onClick={onClose}
        >
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round">
            <line x1="18" y1="6" x2="6" y2="18" />
            <line x1="6" y1="6" x2="18" y2="18" />
          </svg>
        </button>
      </header>
      <div className="artifact-panel-body">
        {target.kind === "run" && <RunPanelBody runId={target.runId} />}
        {target.kind === "html" && (
          <iframe
            title={target.title || "HTML"}
            sandbox="allow-scripts"
            srcDoc={target.html}
            className="artifact-panel-iframe"
          />
        )}
        {target.kind === "markdown" && (
          <div className="artifact-panel-md">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{target.md}</ReactMarkdown>
          </div>
        )}
        {target.kind === "file" && <FilePanelBody payload={target.payload} />}
      </div>
    </aside>
  );
}

function RunPanelBody({ runId }: { runId: number }) {
  const { data: run } = useQuery({
    queryKey: ["run", runId],
    queryFn: () => RunsAPI.get(runId),
    refetchInterval: (q) => {
      const r = q.state.data as { status?: string } | undefined;
      if (!r) return 3_000;
      return ["running", "queued", "cancelling", "paused"].includes(r.status || "") ? 3_000 : false;
    },
  });

  if (!run) {
    return <div className="artifact-panel-empty">Loading run #{runId}…</div>;
  }
  const steps = (run as any).steps || [];
  const status = run.status || "queued";
  const tokens = (Number(run.total_input_tokens) || 0) + (Number(run.total_output_tokens) || 0);
  const cost = Number(run.total_cost_usd) || 0;

  return (
    <div className="run-panel">
      <div className="run-panel-summary">
        <div className="run-panel-row">
          <span className="run-panel-label">Status</span>
          <span className={`run-panel-pill run-panel-pill-${status}`}>{status}</span>
        </div>
        <div className="run-panel-row">
          <span className="run-panel-label">Run</span>
          <span>#{run.id}</span>
        </div>
        <div className="run-panel-row">
          <span className="run-panel-label">Steps</span>
          <span>{steps.length}</span>
        </div>
        <div className="run-panel-row">
          <span className="run-panel-label">Tokens</span>
          <span>{tokens.toLocaleString()}</span>
        </div>
        <div className="run-panel-row">
          <span className="run-panel-label">Cost</span>
          <span>${cost.toFixed(4)}</span>
        </div>
        {run.final_output && (
          <div className="run-panel-final">
            <div className="run-panel-label">Final output</div>
            <div className="run-panel-final-body markdown">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{run.final_output}</ReactMarkdown>
            </div>
          </div>
        )}
      </div>
      <div className="run-panel-steps">
        <div className="run-panel-section-title">Steps</div>
        {steps.length === 0 ? (
          <div className="artifact-panel-empty">No steps yet.</div>
        ) : (
          steps.map((s: any, idx: number) => (
            <div key={s.id ?? idx} className="run-panel-step">
              <div className="run-panel-step-head">
                <span className="run-panel-step-idx">#{idx + 1}</span>
                <span className="run-panel-step-role">{s.role_label || `Agent ${s.agent_id ?? ""}`}</span>
                {s.duration_ms != null && (
                  <span className="run-panel-step-meta">{(s.duration_ms / 1000).toFixed(1)}s</span>
                )}
              </div>
              {s.error ? (
                <div className="run-panel-step-error">{s.error}</div>
              ) : (
                <div className="run-panel-step-body markdown">
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>{s.response || ""}</ReactMarkdown>
                </div>
              )}
            </div>
          ))
        )}
      </div>
    </div>
  );
}

function FilePanelBody({ payload }: { payload: any }) {
  const isText = !payload?.encoding || payload.encoding !== "base64";
  const dataUrl = payload?.encoding === "base64"
    ? `data:${payload.mime || "application/octet-stream"};base64,${payload.content}`
    : `data:${payload?.mime || "text/plain"};charset=utf-8,${encodeURIComponent(payload?.content || "")}`;
  return (
    <div className="run-panel">
      <div className="run-panel-row">
        <span className="run-panel-label">File</span>
        <span>{payload?.filename || "(unnamed)"}</span>
      </div>
      <div className="run-panel-row">
        <span className="run-panel-label">Type</span>
        <span>{payload?.mime || "—"}</span>
      </div>
      <a className="run-status-link" href={dataUrl} download={payload?.filename || "download"}>
        Download
      </a>
      {isText && payload?.content && (
        <pre className="run-panel-step-body" style={{ whiteSpace: "pre-wrap", marginTop: 12 }}>
          {payload.content}
        </pre>
      )}
    </div>
  );
}
