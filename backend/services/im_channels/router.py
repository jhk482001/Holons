"""Inbound-message router.

Takes a normalised `InboundMessage`, dispatches slash commands via the
central `COMMAND_REGISTRY`, and otherwise hands the text to Lead chat
through the session-continuity path.

Returns a DispatchResult containing:
  - `text`: prose reply to send via adapter.send()
  - `artifacts`: structured artifacts the adapter should try to ship
    natively (upload a .html file, render a preview image, etc.)
    before falling back to a text breadcrumb.

Adding a new command = adding a function with `@command(...)` in
`commands.py`. Every IM adapter (Telegram, Slack, LINE today) goes
through the same registry.
"""
from __future__ import annotations

import logging
import re

from ... import db
from .. import lead_agent
from .base import InboundMessage, DispatchResult
from . import commands  # registers built-in commands on import

log = logging.getLogger("agent_company.im.router")


def dispatch(msg: InboundMessage, user_id: int) -> DispatchResult:
    """Return a DispatchResult — never None. Text may be None if
    nothing to say; artifacts may be empty."""
    text = msg.text.strip()

    # Slash command? Run through the registry.
    if text.startswith("/"):
        tok = text[1:].split(None, 1)
        cmd_name = tok[0].lower() if tok else ""
        args = tok[1] if len(tok) > 1 else ""
        reply = commands.dispatch(cmd_name, args, user_id, msg)
        if reply is not None:
            return DispatchResult(text=reply)
        return DispatchResult(
            text=f"Unknown command `/{cmd_name}`. Type /help for the list.",
        )

    # Free text → Lead chat with source-platform session continuity.
    return _handle_lead_full(user_id, msg)


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


def _handle_lead_full(user_id: int, msg: InboundMessage) -> DispatchResult:
    """Full path: returns DispatchResult carrying text + artifacts."""
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
        return DispatchResult(text=f"⚠️ Sorry — Lead call failed: {e}")

    new_thread_id = resp.get("thread_id")
    if new_thread_id and not existing:
        db.execute(
            "UPDATE lead_conversations "
            "SET source_platform = %s, source_external_id = %s "
            "WHERE thread_id = %s",
            (msg.platform, msg.external_id, new_thread_id),
        )

    reply = (resp.get("response") or "").strip()
    # Strip fences from the text — artifacts ride separately below.
    reply = _FENCE_STRIP_RE.sub("", reply).strip()
    reply = _PROPOSAL_STRIP_RE.sub(
        "\n_[proposal — see Holons web UI to accept]_", reply,
    ).strip()

    artifacts = resp.get("artifacts") or []
    if not reply and not artifacts:
        reply = "(Lead returned an empty reply — try again.)"
    return DispatchResult(text=reply or None, artifacts=artifacts)


# Back-compat shim: older tests (pre-DispatchResult) import _handle_lead
# and expect it to return `str | None`. Keep both names so those tests
# don't need to change.
def _handle_lead(user_id: int, msg: InboundMessage) -> str | None:
    r = _handle_lead_full(user_id, msg)
    return r.text
