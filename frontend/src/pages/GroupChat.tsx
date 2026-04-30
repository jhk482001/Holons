import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import {
  Agent,
  AgentsAPI,
  GroupChatAPI,
  GroupChatMessage,
  GroupMember,
  GroupsAPI,
} from "../api/client";
import Avatar from "../components/Avatar";
import Modal from "../components/Modal";

export default function GroupChat() {
  const { t } = useTranslation();
  const { id } = useParams<{ id: string }>();
  const groupId = Number(id);
  const navigate = useNavigate();
  const qc = useQueryClient();

  const { data: group, isLoading: loadingGroup } = useQuery({
    queryKey: ["group", groupId],
    queryFn: () => GroupsAPI.get(groupId),
    enabled: !!groupId,
  });

  const { data: threadData } = useQuery({
    queryKey: ["group-chat-thread", groupId],
    queryFn: () => GroupsAPI.chatThread(groupId),
    enabled: !!groupId,
  });
  const threadId = threadData?.thread_id;

  const { data: msgData } = useQuery({
    queryKey: ["group-chat-messages", threadId],
    queryFn: () => GroupChatAPI.messages(threadId!),
    enabled: !!threadId,
  });
  const messages = msgData?.messages ?? [];

  const [input, setInput] = useState("");
  const [rounds, setRounds] = useState(1);
  const scrollRef = useRef<HTMLDivElement>(null);
  // agent_id -> partial text, populated while a streaming chunk arrives
  // and cleared on that member's complete event. Rendered as live
  // bubbles after the committed messages.
  const [streamingBuffers, setStreamingBuffers] = useState<Record<number, string>>({});
  const [currentRound, setCurrentRound] = useState<{ round: number; of: number } | null>(null);

  // "Include previous conversation" toggle. Sticky per group via
  // localStorage so toggling once carries over to the user's next
  // visit. Default ON — the LLM sees prior history just like before
  // this checkbox existed.
  const includeHistoryKey = `holons.group.includeHistory.${groupId}`;
  const [includeHistory, setIncludeHistory] = useState<boolean>(() => {
    if (typeof window === "undefined") return true;
    const stored = window.localStorage.getItem(includeHistoryKey);
    return stored === null ? true : stored === "1";
  });
  useEffect(() => {
    if (typeof window === "undefined") return;
    window.localStorage.setItem(includeHistoryKey, includeHistory ? "1" : "0");
  }, [includeHistoryKey, includeHistory]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [messages.length, streamingBuffers]);

  function makeStreamHandlers() {
    return {
      onMemberStart: ({ agent_id }: { agent_id: number }) => {
        setStreamingBuffers((b) => ({ ...b, [agent_id]: "" }));
      },
      onChunk: ({ agent_id, text }: { agent_id: number; text: string }) => {
        setStreamingBuffers((b) => ({ ...b, [agent_id]: (b[agent_id] ?? "") + text }));
      },
      onMemberComplete: (msg: GroupChatMessage) => {
        // Inject the persisted row directly into the cache so the live
        // bubble can hand off to the real one in a single render. Without
        // this, the buffer-drop happens before the refetch lands and the
        // user briefly sees a gap; or — if we kept the buffer until the
        // mutation's `finally` — the same agent's bubble appears twice
        // (live buffer + persisted row) until the whole round completes.
        if (msg && typeof msg.id === "number" && threadId) {
          qc.setQueryData<{ thread_id: number; group_id: number; messages: GroupChatMessage[] } | undefined>(
            ["group-chat-messages", threadId],
            (old) => {
              if (!old) return old;
              if (old.messages.some((m) => m.id === msg.id)) return old;
              const created_at = msg.created_at || new Date().toISOString();
              return { ...old, messages: [...old.messages, { ...msg, created_at }] };
            },
          );
        }
        // Drop this agent's live buffer now that the persisted row owns
        // its slot. Other agents in the same round keep streaming.
        setStreamingBuffers((b) => {
          if (msg?.agent_id == null) return b;
          if (!(msg.agent_id in b)) return b;
          const next = { ...b };
          delete next[msg.agent_id];
          return next;
        });
        // Eventually-consistent reconciliation in case the optimistic
        // injection drifted from the server's canonical row (metadata,
        // avatar_config, etc.).
        qc.invalidateQueries({ queryKey: ["group-chat-messages", threadId] });
      },
      onUserMessage: () => {
        qc.invalidateQueries({ queryKey: ["group-chat-messages", threadId] });
      },
      onRoundStart: (info: { round: number; of: number }) => setCurrentRound(info),
      onError: (msg: string) => console.warn("[group-stream]", msg),
    };
  }

  const sending = useMutation({
    mutationFn: async (text: string) => {
      if (!threadId) return;
      setStreamingBuffers({});
      try {
        return await GroupChatAPI.sendStreaming(
          threadId,
          text,
          makeStreamHandlers(),
          undefined,
          { includeHistory },
        );
      } finally {
        setStreamingBuffers({});
      }
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["group-chat-messages", threadId] });
    },
    onError: () => setStreamingBuffers({}),
  });

  const continuing = useMutation({
    mutationFn: async (n: number) => {
      if (!threadId) return;
      setStreamingBuffers({});
      setCurrentRound(null);
      try {
        return await GroupChatAPI.continueRoundsStreaming(threadId, n, makeStreamHandlers());
      } finally {
        setStreamingBuffers({});
        setCurrentRound(null);
      }
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["group-chat-messages", threadId] });
    },
    onError: () => { setStreamingBuffers({}); setCurrentRound(null); },
  });

  const busy = sending.isPending || continuing.isPending;

  const members: GroupMember[] = group?.members ?? [];
  const agentById = useMemo(() => {
    const m = new Map<number, GroupMember>();
    for (const x of members) m.set(x.agent_id, x);
    return m;
  }, [members]);

  // Show a "waiting to reply" indicator for each member whose last contribution
  // in the current round hasn't arrived yet after the user's latest turn.
  const lastUserIdx = (() => {
    for (let i = messages.length - 1; i >= 0; i--) {
      if (messages[i].role === "user") return i;
    }
    return -1;
  })();
  const pendingAfterUser = new Set<number>();
  if (busy && lastUserIdx >= 0) {
    const replied = new Set<number>();
    for (let i = lastUserIdx + 1; i < messages.length; i++) {
      const aid = messages[i].agent_id;
      if (aid) replied.add(aid);
    }
    for (const m of members) {
      // Hide the "thinking" pill for agents whose live bubble is
      // already showing chunks — otherwise the user sees both at once.
      if (!replied.has(m.agent_id) && streamingBuffers[m.agent_id] === undefined) {
        pendingAfterUser.add(m.agent_id);
      }
    }
  }

  const send = () => {
    const t = input.trim();
    if (!t || busy || !threadId) return;
    setInput("");
    sending.mutate(t);
  };

  const [addMemberOpen, setAddMemberOpen] = useState(false);

  if (loadingGroup) {
    return <div className="page" style={{ padding: 40, textAlign: "center", color: "var(--ink-3)" }}>{t("btn.loading")}</div>;
  }
  if (!group) {
    return (
      <div className="page" style={{ padding: 40, textAlign: "center" }}>
        <div style={{ marginBottom: 12 }}>{t("groupChat.groupNotFound")}</div>
        <button className="mbtn" onClick={() => navigate("/groups")}>{t("groupChat.backToGroups")}</button>
      </div>
    );
  }

  return (
    <div
      className="page"
      style={{
        display: "flex",
        flexDirection: "column",
        height: "calc(100vh - 40px)",
        maxWidth: 900,
        margin: "0 auto",
      }}
    >
      {/* Header */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          padding: "12px 4px",
          borderBottom: "1px solid var(--border)",
          marginBottom: 10,
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <button className="mbtn" onClick={() => navigate("/groups")} style={{ padding: "4px 10px", fontSize: 12 }}>
            {t("groupChat.backToGroups")}
          </button>
          <div>
            <div style={{ fontSize: 15, fontWeight: 800 }}>{group.name}</div>
            <div style={{ fontSize: 11, color: "var(--ink-4)" }}>
              {t("groupChat.modePrefix")} {group.mode === "parallel" ? t("groupChat.parallelMode") : t("groupChat.sequentialMode")}
              {" · "}{members.length} {t("common.members")}
            </div>
          </div>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <button
            type="button"
            className="mbtn"
            data-testid="group-chat-add-member"
            onClick={() => setAddMemberOpen(true)}
            style={{ padding: "4px 10px", fontSize: 12 }}
            title={t("groupChat.addMemberTooltip")}
          >
            {t("groupChat.addMember")}
          </button>
          <span
            style={{
              fontSize: 9,
              fontWeight: 800,
              letterSpacing: 1.2,
              background: group.mode === "parallel" ? "var(--accent-soft)" : "var(--surface-2)",
              color: group.mode === "parallel" ? "var(--accent)" : "var(--ink-3)",
              padding: "3px 10px",
              borderRadius: 999,
            }}
          >
            {group.mode === "parallel" ? t("groupChat.parallel") : t("groupChat.sequential")}
          </span>
        </div>
      </div>

      {/* Friendly hint */}
      <div
        style={{
          fontSize: 11,
          color: "var(--ink-3)",
          background: "var(--surface-2)",
          border: "1px dashed var(--border)",
          borderRadius: 10,
          padding: "8px 12px",
          marginBottom: 10,
        }}
      >
        {t("groupChat.hint")}
      </div>

      {/* Messages */}
      <div
        ref={scrollRef}
        style={{
          flex: 1,
          overflowY: "auto",
          padding: "4px 2px 12px",
          display: "flex",
          flexDirection: "column",
          gap: 10,
        }}
      >
        {messages.length === 0 && (
          <div style={{ textAlign: "center", color: "var(--ink-4)", padding: 40 }}>
            {t("groupChat.empty")}
          </div>
        )}
        {messages.map((m) => (
          <MessageRow key={m.id} m={m} agent={m.agent_id ? agentById.get(m.agent_id) : undefined} />
        ))}
        {/* Live streaming bubbles — one per active agent */}
        {Object.entries(streamingBuffers).map(([aidStr, text]) => {
          const aid = Number(aidStr);
          const a = agentById.get(aid);
          return (
            <StreamingMessageRow
              key={`stream-${aid}`}
              agent={a}
              agent_id={aid}
              text={text}
            />
          );
        })}
        {currentRound && (
          <div style={{
            alignSelf: "center",
            fontSize: 10,
            color: "var(--ink-4)",
            background: "var(--surface-2)",
            padding: "2px 10px",
            borderRadius: 999,
          }}>
            {t("groupChat.roundLabel", {
              round: currentRound.round,
              of: currentRound.of,
              defaultValue: `Round ${currentRound.round} / ${currentRound.of}`,
            })}
          </div>
        )}
        {busy && pendingAfterUser.size > 0 && (
          <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginTop: 4 }}>
            {[...pendingAfterUser].map((aid) => {
              const a = agentById.get(aid);
              return (
                <div
                  key={aid}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: 6,
                    background: "var(--surface-2)",
                    border: "1px solid var(--border)",
                    padding: "4px 10px 4px 4px",
                    borderRadius: 999,
                    fontSize: 11,
                    color: "var(--ink-3)",
                  }}
                >
                  <Avatar cfg={a?.avatar_config} size={22} title={a?.agent_name} />
                  <span>{t("groupChat.thinking", { name: a?.agent_name || `agent#${aid}` })}</span>
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* Composer */}
      <div
        style={{
          borderTop: "1px solid var(--border)",
          paddingTop: 10,
          display: "flex",
          flexDirection: "column",
          gap: 6,
        }}
      >
        <label
          style={{
            display: "flex",
            alignItems: "center",
            gap: 6,
            fontSize: 11,
            color: includeHistory ? "var(--ink-3)" : "var(--accent)",
            cursor: "pointer",
            userSelect: "none",
          }}
          title={t("groupChat.includeHistoryTooltip")}
        >
          <input
            type="checkbox"
            data-testid="group-include-history"
            checked={includeHistory}
            onChange={(e) => setIncludeHistory(e.target.checked)}
            style={{ margin: 0, cursor: "pointer" }}
          />
          <span>
            {includeHistory
              ? t("groupChat.includeHistoryOn")
              : t("groupChat.includeHistoryOff")}
          </span>
        </label>
      <div
        style={{
          display: "flex",
          gap: 8,
          alignItems: "flex-end",
        }}
      >
        <textarea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            // Mirror DialogCenter: Enter sends, Shift+Enter inserts a
            // newline. `isComposing` skips IME confirmation Enters
            // (zhuyin / pinyin / kana etc.) so picking a candidate
            // doesn't accidentally fire the message.
            if (
              e.key === "Enter" &&
              !e.shiftKey &&
              !e.nativeEvent.isComposing
            ) {
              e.preventDefault();
              send();
            }
          }}
          placeholder={busy ? t("groupChat.waitingForReplies") : t("groupChat.placeholder")}
          disabled={busy}
          style={{
            flex: 1,
            resize: "none",
            minHeight: 42,
            maxHeight: 140,
            padding: "10px 12px",
            border: "1px solid var(--border)",
            borderRadius: 10,
            fontSize: 13,
            fontFamily: "inherit",
            background: "var(--surface)",
          }}
          rows={2}
        />
        <button
          className="mbtn primary"
          onClick={send}
          disabled={busy || !input.trim()}
          style={{ padding: "10px 16px", fontSize: 13 }}
        >
          {sending.isPending ? t("btn.sending") : t("btn.send")}
        </button>
        <ContinueButton
          rounds={rounds}
          onRoundsChange={setRounds}
          disabled={busy || messages.length === 0}
          running={continuing.isPending}
          onClick={() => continuing.mutate(rounds)}
        />
      </div>
      </div>

      <AddMemberModal
        open={addMemberOpen}
        onClose={() => setAddMemberOpen(false)}
        groupId={groupId}
        currentMemberIds={members.map((m) => m.agent_id)}
        onSaved={() => {
          setAddMemberOpen(false);
          qc.invalidateQueries({ queryKey: ["group", groupId] });
        }}
      />
    </div>
  );
}

function AddMemberModal({
  open,
  onClose,
  groupId,
  currentMemberIds,
  onSaved,
}: {
  open: boolean;
  onClose: () => void;
  groupId: number;
  currentMemberIds: number[];
  onSaved: () => void;
}) {
  const { t } = useTranslation();
  const { data: agents = [] } = useQuery({
    queryKey: ["agents"],
    queryFn: AgentsAPI.list,
    enabled: open,
  });
  const [picked, setPicked] = useState<number[]>([]);

  // Reset selection each time the modal opens so reopening after a save
  // doesn't replay the previous picks.
  useEffect(() => {
    if (open) setPicked([]);
  }, [open]);

  const eligible = useMemo<Agent[]>(() => {
    const taken = new Set(currentMemberIds);
    return agents.filter((a) => !a.is_lead && !taken.has(a.id));
  }, [agents, currentMemberIds]);

  const save = useMutation({
    mutationFn: async () => {
      // The PUT endpoint replaces the member list wholesale, so we send
      // the existing members merged with the newly picked ones in one
      // shot rather than calling N times.
      const merged = Array.from(new Set([...currentMemberIds, ...picked]));
      await GroupsAPI.update(groupId, { member_agent_ids: merged });
    },
    onSuccess: onSaved,
  });

  const toggle = (id: number) =>
    setPicked((ids) => (ids.includes(id) ? ids.filter((x) => x !== id) : [...ids, id]));

  const canSubmit = picked.length > 0 && !save.isPending;

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={t("groupChat.addMembersTitle")}
      subtitle={t("groupChat.addMembersSubtitle")}
      size="md"
      footer={
        <>
          <button className="mbtn" onClick={onClose} disabled={save.isPending}>
            {t("btn.cancel")}
          </button>
          <button
            className="mbtn primary"
            data-testid="add-members-submit"
            onClick={() => save.mutate()}
            disabled={!canSubmit}
          >
            {save.isPending
              ? t("btn.saving")
              : t("groupChat.addMembersSubmit", { count: picked.length })}
          </button>
        </>
      }
    >
      {eligible.length === 0 ? (
        <div style={{ padding: 20, textAlign: "center", color: "var(--ink-4)", fontSize: 12 }}>
          {t("groupChat.noEligibleAgents")}
        </div>
      ) : (
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fill, minmax(180px, 1fr))",
            gap: 8,
            maxHeight: 320,
            overflowY: "auto",
          }}
        >
          {eligible.map((a) => {
            const selected = picked.includes(a.id);
            return (
              <button
                key={a.id}
                type="button"
                data-testid={`add-member-toggle-${a.id}`}
                onClick={() => toggle(a.id)}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 8,
                  padding: 8,
                  background: selected ? "var(--accent-soft)" : "white",
                  border: `1px solid ${selected ? "var(--accent)" : "var(--border)"}`,
                  borderRadius: 10,
                  cursor: "pointer",
                  textAlign: "left",
                }}
              >
                <Avatar cfg={a.avatar_config} size={32} title={a.name} />
                <div style={{ minWidth: 0 }}>
                  <div style={{ fontSize: 12, fontWeight: 800 }}>{a.name}</div>
                  <div
                    style={{
                      fontSize: 9,
                      color: "var(--ink-3)",
                      whiteSpace: "nowrap",
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                    }}
                  >
                    {a.role_title || "—"}
                  </div>
                </div>
              </button>
            );
          })}
        </div>
      )}
    </Modal>
  );
}

// Live-streaming bubble shown for an agent whose chunks are arriving.
// Visually mirrors a regular bot MessageRow plus a blinking cursor.
function StreamingMessageRow({
  agent,
  agent_id,
  text,
}: {
  agent?: GroupMember;
  agent_id: number;
  text: string;
}) {
  const name = agent?.agent_name || `agent#${agent_id}`;
  const avatarCfg = agent?.avatar_config;
  return (
    <div style={{ display: "flex", gap: 10, alignItems: "flex-start" }}>
      <Avatar cfg={avatarCfg} size={32} title={name} />
      <div style={{ maxWidth: "72%" }}>
        <div style={{ fontSize: 10, color: "var(--ink-4)", marginBottom: 2 }}>
          {name}
        </div>
        <div
          style={{
            background: "var(--surface-2)",
            border: "1px solid var(--border)",
            borderRadius: 12,
            padding: "8px 12px",
            fontSize: 13,
            lineHeight: 1.55,
            whiteSpace: "pre-wrap",
            wordBreak: "break-word",
          }}
        >
          {text || "…"}
          <span style={{
            display: "inline-block",
            marginLeft: 2,
            color: "var(--accent)",
            animation: "stream-blink 1s steps(2, start) infinite",
          }}>▍</span>
        </div>
      </div>
    </div>
  );
}

function MessageRow({ m, agent }: { m: GroupChatMessage; agent?: GroupMember }) {
  const { t } = useTranslation();
  const isUser = m.role === "user";
  const name = isUser ? t("groupChat.you") : (m.agent_name || agent?.agent_name || `agent#${m.agent_id}`);
  const avatarCfg = isUser ? undefined : (m.avatar_config || agent?.avatar_config);

  return (
    <div
      style={{
        display: "flex",
        flexDirection: isUser ? "row-reverse" : "row",
        gap: 10,
        alignItems: "flex-start",
      }}
    >
      {isUser ? (
        <div
          style={{
            width: 32,
            height: 32,
            borderRadius: 999,
            background: "var(--accent)",
            color: "white",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            fontWeight: 700,
            fontSize: 13,
            flexShrink: 0,
          }}
        >
          {t("groupChat.you")}
        </div>
      ) : (
        <Avatar cfg={avatarCfg} size={32} title={name} />
      )}
      <div style={{ maxWidth: "72%" }}>
        <div
          style={{
            fontSize: 10,
            color: "var(--ink-4)",
            marginBottom: 2,
            textAlign: isUser ? "right" : "left",
          }}
        >
          {name}
        </div>
        <div
          style={{
            background: isUser ? "var(--accent-soft)" : "var(--surface-2)",
            color: "var(--ink-1)",
            border: "1px solid var(--border)",
            borderRadius: 12,
            padding: "8px 12px",
            fontSize: 13,
            lineHeight: 1.55,
            whiteSpace: "pre-wrap",
            wordBreak: "break-word",
          }}
        >
          {m.content}
        </div>
      </div>
    </div>
  );
}

function ContinueButton({
  rounds,
  onRoundsChange,
  disabled,
  running,
  onClick,
}: {
  rounds: number;
  onRoundsChange: (n: number) => void;
  disabled: boolean;
  running: boolean;
  onClick: () => void;
}) {
  const { t } = useTranslation();
  return (
    <div
      style={{
        display: "flex",
        alignItems: "stretch",
        border: "1px solid var(--border)",
        borderRadius: 10,
        overflow: "hidden",
        height: 42,
      }}
      title={t("groupChat.continueTitle")}
    >
      <button
        onClick={onClick}
        disabled={disabled}
        style={{
          padding: "0 12px",
          background: disabled ? "var(--surface-2)" : "var(--surface)",
          color: "var(--ink-2)",
          border: "none",
          borderRight: "1px solid var(--border)",
          fontSize: 12,
          fontWeight: 700,
          cursor: disabled ? "not-allowed" : "pointer",
        }}
      >
        {running ? t("groupChat.running") : t("groupChat.continue")}
      </button>
      <select
        value={rounds}
        disabled={disabled}
        onChange={(e) => onRoundsChange(Number(e.target.value))}
        style={{
          border: "none",
          padding: "0 8px",
          background: "var(--surface)",
          fontSize: 12,
          outline: "none",
          cursor: disabled ? "not-allowed" : "pointer",
        }}
      >
        {[1, 2, 3, 4, 5, 6, 7, 8, 9, 10].map((n) => (
          <option key={n} value={n}>
            {t("groupChat.rounds", { count: n })}
          </option>
        ))}
      </select>
    </div>
  );
}
