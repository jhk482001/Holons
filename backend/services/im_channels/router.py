"""Inbound-message router.

Takes a normalised `InboundMessage`, dispatches slash commands via the
central `COMMAND_REGISTRY`, and otherwise hands the text to Lead chat
through the session-continuity path.

Adding a new command = adding a function with `@command(...)` in
`commands.py`. Every IM adapter (Telegram today; Slack/LINE/Discord
next) goes through the same registry — one place to add, one place to
list, one place to generate per-platform command menus.
"""
from __future__ import annotations

import logging
import re

from ... import db
from .. import lead_agent
from .base import InboundMessage
from . import commands  # registers built-in commands on import

log = logging.getLogger("agent_company.im.router")


def dispatch(msg: InboundMessage, user_id: int) -> str | None:
    """Return the text to send back, or None if nothing to send."""
    text = msg.text.strip()

    # Slash command? Run through the registry.
    if text.startswith("/"):
        tok = text[1:].split(None, 1)
        cmd_name = tok[0].lower() if tok else ""
        args = tok[1] if len(tok) > 1 else ""
        reply = commands.dispatch(cmd_name, args, user_id, msg)
        if reply is not None:
            return reply
        return (
            f"Unknown command `/{cmd_name}`. Type /help for the list."
        )

    # Free text → Lead chat with source-platform session continuity.
    return _handle_lead(user_id, msg)


# ============================================================================
# Lead chat with session continuity across platforms
# ============================================================================

_FENCE_STRIP_RE = re.compile(
    r"```artifact-(?:html|slides|file|markdown)(?:\s+[^\n]+)?\s*\n.*?\n```",
    re.DOTALL,
)
_PROPOSAL_STRIP_RE = re.compile(
    r"```(?:workflow|hire|project)\s*\n.*?\n```", re.DOTALL,
)


def _handle_lead(user_id: int, msg: InboundMessage) -> str | None:
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

    # Artifact + proposal fences are for the web UI — breadcrumb them.
    reply = _FENCE_STRIP_RE.sub("\n_[artifact produced — see Holons web UI]_", reply)
    reply = _PROPOSAL_STRIP_RE.sub(
        "\n_[proposal — see Holons web UI to accept]_", reply,
    )
    return reply.strip()
