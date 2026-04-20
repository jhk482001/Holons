"""IM command registry.

Single source of truth for slash commands across every IM adapter. One
`@command` decorator registers a handler; the router, the /help text,
and (future) per-platform slash-command menus all pull from this
registry.

Design intent: when we add a 2nd platform (Slack, LINE, Discord), we
shouldn't have to re-declare the command list. Each platform adapter
can introspect `COMMAND_REGISTRY` to auto-register its native command
menu (Telegram's BotCommand, Slack's slash-command manifest, etc.).

Command handler signature:
    handler(user_id: int, args: str, msg: InboundMessage) -> str | None
- `args`  is the rest of the message after the command name (may be "")
- returns the text to send back; `None` means "don't send anything"
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, List

from .. import lead_agent  # noqa: F401  (may be used by commands)
from ... import db
from .base import InboundMessage

CommandHandler = Callable[[int, str, InboundMessage], "str | None"]


@dataclass
class CommandDef:
    name: str
    description: str
    handler: CommandHandler
    aliases: List[str] = field(default_factory=list)
    admin_only: bool = False


COMMAND_REGISTRY: dict[str, CommandDef] = {}


def command(name: str, *, description: str, aliases: List[str] | None = None,
            admin_only: bool = False):
    """Decorator: register a command handler.

    >>> @command("runs", description="Recent runs")
    >>> def cmd_runs(user_id, args, msg): ...
    """
    def _wrap(fn: CommandHandler) -> CommandHandler:
        cmd = CommandDef(name=name, description=description, handler=fn,
                         aliases=aliases or [], admin_only=admin_only)
        COMMAND_REGISTRY[name] = cmd
        for a in cmd.aliases:
            COMMAND_REGISTRY[a] = cmd
        return fn
    return _wrap


def list_commands() -> list[CommandDef]:
    """Unique CommandDefs (aliases collapsed) in registration order."""
    seen = set()
    out: list[CommandDef] = []
    for cmd in COMMAND_REGISTRY.values():
        if cmd.name in seen:
            continue
        seen.add(cmd.name)
        out.append(cmd)
    return out


def dispatch(cmd_name: str, args: str, user_id: int,
             msg: InboundMessage) -> str | None:
    """Look up by name or alias, run handler. Returns None for unknown
    command — caller decides how to respond (usually an 'unknown command'
    hint with a pointer to /help)."""
    cmd = COMMAND_REGISTRY.get(cmd_name.lower())
    if not cmd:
        return None
    return cmd.handler(user_id, args, msg)


# ============================================================================
# Built-in commands
# ============================================================================

@command("help", description="Show this help", aliases=["h", "?"])
def _cmd_help(user_id: int, args: str, msg: InboundMessage) -> str:
    lines = ["*Holons bot*", "", "Send any text to talk to Lead. Commands:"]
    for cmd in list_commands():
        if cmd.admin_only:
            continue
        aliases = f" (aliases: /{', /'.join(cmd.aliases)})" if cmd.aliases else ""
        lines.append(f"/{cmd.name} — {cmd.description}{aliases}")
    return "\n".join(lines)


@command("start", description="Link this chat to your Holons account")
def _cmd_start(user_id: int, args: str, msg: InboundMessage) -> str:
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


@command("runs", description="Today's run history (last 10)")
def _cmd_runs(user_id: int, args: str, msg: InboundMessage) -> str:
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


@command("status", description="Agent team load snapshot")
def _cmd_status(user_id: int, args: str, msg: InboundMessage) -> str:
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
