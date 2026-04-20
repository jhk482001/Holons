"""Inbound-message router.

Takes a normalised `InboundMessage`, figures out which Holons user it's
for (via the `im_bindings` row that spawned this adapter), handles a
handful of slash commands inline, and otherwise hands the text off to
`lead_agent.chat()` — reusing whatever Lead thread the user last used
from this platform so chats stay continuous across sessions.

Outgoing replies come back in `dispatch()`'s return value; the manager
uses the adapter's `send()` to push them back to the chat.
"""
from __future__ import annotations

import logging

from ... import db
from .. import lead_agent
from .base import InboundMessage

log = logging.getLogger("agent_company.im.router")

HELP_TEXT = (
    "*Holons bot*\n\n"
    "Send any text to talk to your Lead agent. Commands:\n\n"
    "/start — link this chat to your Holons account\n"
    "/help — this message\n"
    "/runs — today's run history\n"
    "/status — agent team load snapshot\n"
)


def dispatch(msg: InboundMessage, user_id: int) -> str | None:
    """Return the text to send back, or None if nothing to send."""
    text = msg.text.strip()

    # ------ Slash commands ---------------------------------------------
    if text.startswith("/start"):
        return _handle_start(user_id, msg)
    if text.startswith("/help"):
        return HELP_TEXT
    if text.startswith("/runs"):
        return _handle_runs(user_id)
    if text.startswith("/status"):
        return _handle_status(user_id)

    # ------ Free text → Lead chat --------------------------------------
    return _handle_lead(user_id, msg)


# ============================================================================
# Command handlers
# ============================================================================

def _handle_start(user_id: int, msg: InboundMessage) -> str:
    """Persist the chat's external_id so outbound replies know where to
    go. Called on every /start — idempotent."""
    db.execute(
        "UPDATE im_bindings SET external_id = %s, updated_at = NOW() "
        "WHERE user_id = %s AND platform = %s",
        (msg.external_id, user_id, msg.platform),
    )
    user = db.fetch_one(
        "SELECT display_name, username FROM as_users WHERE id = %s", (user_id,),
    ) or {}
    name = user.get("display_name") or user.get("username") or "there"
    return (
        f"Hi {name} — you're now linked to Holons.\n\n"
        "Send anything and I'll pass it to your Lead agent. "
        "Type /help for commands."
    )


def _handle_runs(user_id: int) -> str:
    rows = db.fetch_all(
        """
        SELECT r.id, r.status, r.total_cost_usd, w.name
        FROM runs r LEFT JOIN workflows w ON w.id = r.workflow_id
        WHERE r.user_id = %s
          AND r.started_at >= NOW() - INTERVAL '1 day'
        ORDER BY r.id DESC LIMIT 10
        """,
        (user_id,),
    )
    if not rows:
        return "No runs in the last 24h."
    lines = ["*Recent runs*"]
    for r in rows:
        name = (r.get("name") or "")[:50]
        cost = float(r.get("total_cost_usd") or 0)
        lines.append(f"  • run #{r['id']} [{r['status']}] ${cost:.4f}  {name}")
    return "\n".join(lines)


def _handle_status(user_id: int) -> str:
    agents = db.fetch_all(
        """
        SELECT a.name, a.role_title, a.status,
               (SELECT COUNT(*) FROM agent_tasks t
                WHERE t.agent_id = a.id AND t.status IN ('queued','running','paused')) AS busy
        FROM agents a
        WHERE a.user_id = %s AND a.status = 'active'
        ORDER BY a.is_lead DESC, a.id
        """,
        (user_id,),
    )
    if not agents:
        return "No active agents."
    lines = ["*Team snapshot*"]
    for a in agents:
        busy = int(a.get("busy") or 0)
        flag = " 🟡 busy" if busy else ""
        lines.append(f"  • {a['name']} ({a['role_title']}){flag}")
    return "\n".join(lines)


# ============================================================================
# Lead chat with source-platform session continuity
# ============================================================================

def _handle_lead(user_id: int, msg: InboundMessage) -> str | None:
    # Reuse the last thread this user opened from this platform.
    # lead_agent.chat auto-creates a thread when thread_id is None, but
    # then we'd lose continuity — so we look one up ourselves first and
    # tag it with the platform source.
    existing = db.fetch_one(
        """
        SELECT thread_id FROM lead_conversations
        WHERE user_id = %s AND status = 'active'
          AND source_platform = %s
          AND source_external_id = %s
        ORDER BY id DESC LIMIT 1
        """,
        (user_id, msg.platform, msg.external_id),
    )
    thread_id = existing["thread_id"] if existing else None
    try:
        resp = lead_agent.chat(user_id, msg.text, thread_id=thread_id)
    except Exception as e:
        log.exception("lead.chat failed for user %s", user_id)
        return f"⚠️ Sorry — Lead call failed: {e}"

    new_thread_id = resp.get("thread_id")
    # First message from this platform → tag the thread so later messages
    # from the same chat find it.
    if new_thread_id and not existing:
        db.execute(
            "UPDATE lead_conversations "
            "SET source_platform = %s, source_external_id = %s "
            "WHERE thread_id = %s",
            (msg.platform, msg.external_id, new_thread_id),
        )

    reply = (resp.get("response") or "").strip()
    if not reply:
        return "(Lead returned an empty reply — try again.)"

    # Strip artifact fences from the text (artifacts are persisted on the
    # lead_message; pointing the TG user at the web UI is cleaner than
    # trying to send 50KB of HTML inline).
    import re
    reply = re.sub(
        r"```artifact-(?:html|slides|file|markdown)(?:\s+[^\n]+)?\s*\n.*?\n```",
        "\n_[artifact produced — see Holons web UI]_",
        reply,
        flags=re.DOTALL,
    )
    # Workflow / hire / project fences too — they're for the web cards.
    reply = re.sub(r"```(?:workflow|hire|project)\s*\n.*?\n```",
                   "\n_[proposal — see Holons web UI to accept]_",
                   reply, flags=re.DOTALL)
    return reply.strip()
