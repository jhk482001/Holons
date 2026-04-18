"""Lead agent proxy-answer mechanism.

When Lead asks the user a question (e.g. "shall I dispatch this workflow?")
we tag the lead_messages row with `pending_decision_expires_at`. If the
user doesn't respond before that deadline *and* they haven't been seen in
the app recently, a scheduler tick invokes this service to:

  1. Compose an LLM answer "on behalf of" the user.
  2. Insert the answer as a new lead_messages row tagged
     `metadata.proxy=true` + `on_behalf_of_user_id=N` + `reason=...`.
  3. Clear the pending_decision_expires_at on the original row.

Design guardrails:
  * Workflow-dispatch decisions default to REJECT (never silently fire
    a run). The LLM can override that but a conservative prompt steers
    it toward "decline and say I'll confirm later".
  * Users can retract a proxy answer; the `cancelled=true` flag on the
    proxy row makes the audit view show it as withdrawn.

Public entry point: `tick()` — called by scheduler every N seconds.
"""
from __future__ import annotations

import datetime as _dt
import json
import logging
from typing import Any

from .. import db
from ..llm_clients import invoke_for_agent as llm_invoke  # type: ignore

log = logging.getLogger("agent_company.lead_proxy")


# ============================================================================
# Public API
# ============================================================================

PROXY_SYSTEM_PROMPT = """\
You are acting AS the user who is away from their terminal right now.
A Lead agent has asked them a question. Your job is to produce a short,
conservative answer on their behalf — the same way a cautious assistant
would speak on their boss's behalf.

Rules:
  1. Never approve a workflow dispatch or any action with real side
     effects. If asked whether to execute, politely decline and say
     "I'll confirm when I'm back."
  2. If the question is informational (clarification, preferences that
     can be guessed from context), answer briefly in first person from
     the user's point of view in zh-TW.
  3. Keep the reply under 120 characters.
  4. End every reply with: "（Lead 代答，未經本人確認）"
"""


def tick(now: _dt.datetime | None = None) -> int:
    """Process all pending lead_messages whose decision has expired AND
    whose user has been away longer than their configured threshold.
    Returns the number of proxy answers inserted this tick."""
    now = now or _dt.datetime.now(tz=_dt.timezone.utc)
    rows = db.fetch_all(
        """
        SELECT m.id AS msg_id, m.thread_id, m.content, m.metadata,
               m.proposed_workflow_id,
               c.user_id AS owner_user_id, c.agent_id AS thread_agent_id,
               u.lead_proxy_enabled, u.lead_proxy_timeout_minutes,
               u.lead_proxy_away_minutes, u.last_seen_at
        FROM lead_messages m
        JOIN lead_conversations c ON c.thread_id = m.thread_id
        JOIN as_users u ON u.id = c.user_id
        WHERE m.pending_decision_expires_at IS NOT NULL
          AND m.pending_decision_expires_at <= %s
          AND u.lead_proxy_enabled IS TRUE
        """,
        (now,),
    )
    count = 0
    for row in rows:
        try:
            if _user_still_around(row, now):
                # User is still active — don't proxy yet, extend the
                # deadline by the timeout so we re-check on the next tick.
                _extend_deadline(
                    row["msg_id"],
                    minutes=row["lead_proxy_timeout_minutes"] or 10,
                )
                continue
            _proxy_answer(row, reason="away_and_timeout")
            count += 1
        except Exception:  # noqa: BLE001
            log.exception(
                "lead_proxy: failed to answer on behalf of user %s msg %s",
                row.get("owner_user_id"),
                row.get("msg_id"),
            )
    return count


def mark_retracted(message_id: int, actor_user_id: int) -> bool:
    """Let the user retract a proxy answer. Sets cancelled=TRUE + audit
    marker in metadata. Returns True if the row was updated."""
    result = db.fetch_one(
        """
        UPDATE lead_messages
           SET cancelled = TRUE,
               metadata = jsonb_set(
                 COALESCE(metadata, '{}'::jsonb),
                 '{retracted_by}', to_jsonb(%s::int)
               )
         WHERE id = %s
           AND (metadata ->> 'proxy') = 'true'
         RETURNING id
        """,
        (actor_user_id, message_id),
    )
    return result is not None


def list_proxy_responses(user_id: int, limit: int = 50) -> list[dict]:
    """Return recent proxy answers for this user's threads, newest first.
    Used by the 紀錄 → 代答紀錄 tab."""
    return db.fetch_all(
        """
        SELECT m.id, m.thread_id, m.content, m.metadata, m.cancelled,
               m.created_at,
               c.agent_id AS thread_agent_id
        FROM lead_messages m
        JOIN lead_conversations c ON c.thread_id = m.thread_id
        WHERE c.user_id = %s
          AND (m.metadata ->> 'proxy') = 'true'
        ORDER BY m.created_at DESC
        LIMIT %s
        """,
        (user_id, limit),
    )


# ============================================================================
# Helpers
# ============================================================================

def _user_still_around(row: dict, now: _dt.datetime) -> bool:
    """A user counts as 'away' if their last_seen_at is older than
    `lead_proxy_away_minutes`. A missing last_seen_at means 'never seen'
    which also counts as away."""
    away_minutes = row.get("lead_proxy_away_minutes") or 5
    last_seen = row.get("last_seen_at")
    if last_seen is None:
        return False
    if last_seen.tzinfo is None:
        last_seen = last_seen.replace(tzinfo=_dt.timezone.utc)
    return (now - last_seen) < _dt.timedelta(minutes=away_minutes)


def _extend_deadline(msg_id: int, minutes: int) -> None:
    db.execute(
        """
        UPDATE lead_messages
           SET pending_decision_expires_at = NOW() + (%s || ' minutes')::interval
         WHERE id = %s
        """,
        (int(minutes), msg_id),
    )


def _proxy_answer(row: dict, reason: str) -> None:
    """Generate and persist the proxy answer. Mutates DB state."""
    # Compose the question context. For now just pass the Lead message
    # body; the system prompt tells the LLM to stay conservative.
    question = row.get("content") or ""
    proposed_wf = row.get("proposed_workflow_id")

    if proposed_wf:
        # Side-effect question: default to a conservative decline without
        # even calling the LLM. The proposal stays pending; the user can
        # dispatch it manually when they come back.
        answer_text = (
            "我暫時先不執行這個 workflow，等我回來再確認。"
            "（Lead 代答，未經本人確認）"
        )
        llm_used = False
    else:
        try:
            # Route through the agent referenced by this lead thread (falls
            # back to user's own lead agent if the thread isn't pinned to
            # one). This ensures the proxy answer respects whichever model
            # client the agent is bound to.
            dispatch_agent_id = row.get("thread_agent_id")
            if not dispatch_agent_id:
                lead_row = db.fetch_one(
                    "SELECT id FROM agents WHERE user_id = %s AND is_lead = TRUE LIMIT 1",
                    (row["owner_user_id"],),
                )
                dispatch_agent_id = (lead_row or {}).get("id")
            result = llm_invoke(
                agent_id=dispatch_agent_id,
                model_key=None,
                system_prompt=PROXY_SYSTEM_PROMPT,
                user_text=f"Lead 剛剛問我：\n\n{question}\n\n請以我的身分簡短回覆。",
            )
            answer_text = (result.get("text") or "").strip()
            if not answer_text:
                answer_text = "好的，晚點我會回覆這個問題。（Lead 代答，未經本人確認）"
            llm_used = True
        except Exception:  # noqa: BLE001
            log.exception("lead_proxy: LLM invocation failed")
            answer_text = "好的，晚點我會回覆這個問題。（Lead 代答，未經本人確認）"
            llm_used = False

    proxy_metadata = {
        "proxy": True,
        "on_behalf_of_user_id": int(row["owner_user_id"]),
        "reason": reason,
        "replied_to_msg_id": int(row["msg_id"]),
        "llm_used": llm_used,
    }

    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO lead_messages
                  (thread_id, role, content, metadata)
                VALUES (%s, 'user', %s, %s::jsonb)
                """,
                (row["thread_id"], answer_text, json.dumps(proxy_metadata)),
            )
            cur.execute(
                """
                UPDATE lead_messages
                   SET pending_decision_expires_at = NULL
                 WHERE id = %s
                """,
                (row["msg_id"],),
            )
