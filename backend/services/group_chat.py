"""Group chat: user sits in a room with a group's members.

Modes:
  - parallel   — each member independently replies to the same pre-round context
  - sequential — members reply in order; each later member sees earlier replies

`continue_rounds()` lets agents keep talking among themselves for N rounds
(no user turn in between) so the user can observe them deliberating.
"""
from __future__ import annotations

import json

from .. import db
from ..llm_clients import invoke_for_agent as llm_invoke


_HISTORY_LIMIT = 40          # messages fed to each agent as context
_CONTINUE_HISTORY_LIMIT = 80  # larger window during self-continue
_MAX_CONTINUE_ROUNDS = 10

_GROUP_SYSTEM_SUFFIX = (
    "\n\nYou are in a small group chat. This is a conversation, not a one-shot "
    "task hand-off — speak casually, like a person, and keep replies tight "
    "(usually 1–3 sentences; expand only when it matters). "
    "Don't prefix your reply with your own name or a [role] label — the chat UI "
    "already tags who's speaking."
)


def _group(user_id: int, group_id: int) -> dict | None:
    return db.fetch_one(
        "SELECT id, mode FROM groups_tbl WHERE id = %s AND user_id = %s",
        (group_id, user_id),
    )


def _members(group_id: int) -> list[dict]:
    return db.fetch_all(
        """
        SELECT gm.agent_id, gm.position, gm.custom_prompt,
               a.name, a.system_prompt, a.primary_model_id
        FROM group_members gm
        JOIN agents a ON a.id = gm.agent_id
        WHERE gm.group_id = %s
        ORDER BY gm.position, gm.id
        """,
        (group_id,),
    )


def _load_history(thread_id: int, limit: int = _HISTORY_LIMIT) -> list[dict]:
    rows = db.fetch_all(
        """
        SELECT m.id, m.role, m.agent_id, m.content, m.created_at,
               a.name AS agent_name
        FROM group_chat_messages m
        LEFT JOIN agents a ON a.id = m.agent_id
        WHERE m.thread_id = %s
        ORDER BY m.created_at DESC, m.id DESC
        LIMIT %s
        """,
        (thread_id, limit),
    )
    return list(reversed(rows))


def _format_history(msgs: list[dict], own_agent_id: int | None) -> str:
    lines: list[str] = []
    for m in msgs:
        if m["role"] == "user":
            lines.append(f"[user] {m['content']}")
        elif m["role"] == "agent":
            label = "you" if m.get("agent_id") == own_agent_id else (m.get("agent_name") or f"agent#{m.get('agent_id')}")
            lines.append(f"[{label}] {m['content']}")
    return "\n".join(lines)


def _generate_reply(member: dict, history: list[dict], thread_id: int) -> dict:
    base_prompt = (member.get("system_prompt") or "").strip()
    system_prompt = base_prompt + _GROUP_SYSTEM_SUFFIX
    custom = (member.get("custom_prompt") or "").strip()
    if custom:
        system_prompt += f"\n\nRole context: {custom}"

    history_text = _format_history(history, own_agent_id=member["agent_id"])
    user_text = f"{history_text}\n\n[It's your turn — respond in character, continuing the conversation.]"

    result = llm_invoke(
        agent_id=member["agent_id"],
        model_key=member.get("primary_model_id"),
        system_prompt=system_prompt,
        user_text=user_text,
    )
    text = (result.get("text") or "").strip()

    new_id = db.execute_returning(
        """
        INSERT INTO group_chat_messages (thread_id, role, agent_id, content, metadata)
        VALUES (%s, 'agent', %s, %s, %s::jsonb) RETURNING id
        """,
        (
            thread_id,
            member["agent_id"],
            text,
            json.dumps({
                "tokens": (result.get("input_tokens", 0) or 0) + (result.get("output_tokens", 0) or 0),
                "cost_usd": float(result.get("cost_usd", 0) or 0),
                "model": result.get("model_id"),
            }),
        ),
    )
    return {
        "id": new_id,
        "role": "agent",
        "agent_id": member["agent_id"],
        "agent_name": member["name"],
        "content": text,
    }


def _run_round(group: dict, members: list[dict], thread_id: int, *, history_limit: int) -> list[dict]:
    replies: list[dict] = []
    if group["mode"] == "parallel":
        # Snapshot once — within a parallel round, agents don't see each other's
        # current-round replies. History is shared (prior rounds + user message).
        history = _load_history(thread_id, limit=history_limit)
        for m in members:
            replies.append(_generate_reply(m, history, thread_id))
    else:
        # Sequential — reload after each member so the next one sees the prior.
        for m in members:
            history = _load_history(thread_id, limit=history_limit)
            replies.append(_generate_reply(m, history, thread_id))
    return replies


def get_or_create_thread(user_id: int, group_id: int) -> int:
    """Return an active thread id for this (user, group), creating one if none."""
    row = db.fetch_one(
        """
        SELECT id FROM group_chat_threads
        WHERE user_id = %s AND group_id = %s AND status = 'active'
        ORDER BY id DESC LIMIT 1
        """,
        (user_id, group_id),
    )
    if row:
        return row["id"]
    return db.execute_returning(
        """
        INSERT INTO group_chat_threads (user_id, group_id, status)
        VALUES (%s, %s, 'active') RETURNING id
        """,
        (user_id, group_id),
    )


def list_messages(thread_id: int) -> list[dict]:
    rows = db.fetch_all(
        """
        SELECT m.id, m.role, m.agent_id, m.content, m.created_at,
               a.name AS agent_name, a.avatar_config
        FROM group_chat_messages m
        LEFT JOIN agents a ON a.id = m.agent_id
        WHERE m.thread_id = %s
        ORDER BY m.created_at ASC, m.id ASC
        """,
        (thread_id,),
    )
    return rows


def send_user_message(user_id: int, group_id: int, thread_id: int, user_message: str) -> dict:
    group = _group(user_id, group_id)
    if not group:
        return {"error": "group not found"}
    # Verify thread belongs to this (user, group).
    t = db.fetch_one(
        "SELECT id FROM group_chat_threads WHERE id = %s AND user_id = %s AND group_id = %s",
        (thread_id, user_id, group_id),
    )
    if not t:
        return {"error": "thread not found"}
    members = _members(group_id)
    if not members:
        return {"error": "group has no members"}

    user_msg_id = db.execute_returning(
        """
        INSERT INTO group_chat_messages (thread_id, role, content)
        VALUES (%s, 'user', %s) RETURNING id
        """,
        (thread_id, user_message),
    )

    replies = _run_round(group, members, thread_id, history_limit=_HISTORY_LIMIT)

    db.execute("UPDATE group_chat_threads SET updated_at = NOW() WHERE id = %s", (thread_id,))

    return {
        "user_message": {
            "id": user_msg_id,
            "role": "user",
            "content": user_message,
        },
        "replies": replies,
        "mode": group["mode"],
    }


def continue_rounds(user_id: int, group_id: int, thread_id: int, rounds: int) -> dict:
    """Agents keep talking among themselves for `rounds` rounds (no user turn)."""
    rounds = max(1, min(_MAX_CONTINUE_ROUNDS, int(rounds or 1)))
    group = _group(user_id, group_id)
    if not group:
        return {"error": "group not found"}
    t = db.fetch_one(
        "SELECT id FROM group_chat_threads WHERE id = %s AND user_id = %s AND group_id = %s",
        (thread_id, user_id, group_id),
    )
    if not t:
        return {"error": "thread not found"}
    members = _members(group_id)
    if not members:
        return {"error": "group has no members"}

    all_new: list[dict] = []
    for _ in range(rounds):
        replies = _run_round(group, members, thread_id, history_limit=_CONTINUE_HISTORY_LIMIT)
        all_new.extend(replies)

    db.execute("UPDATE group_chat_threads SET updated_at = NOW() WHERE id = %s", (thread_id,))

    return {"replies": all_new, "rounds": rounds, "mode": group["mode"]}
