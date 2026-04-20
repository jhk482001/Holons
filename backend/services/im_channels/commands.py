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


@command("workflows", description="List your workflows (id + name)",
         aliases=["wf"])
def _cmd_workflows(user_id: int, args: str, msg: InboundMessage) -> str:
    rows = db.fetch_all(
        "SELECT id, name FROM workflows WHERE user_id = %s ORDER BY id DESC LIMIT 20",
        (user_id,),
    )
    if not rows:
        return "No workflows yet. Create one from the web UI or ask Lead to propose."
    lines = ["*Your workflows*"]
    for r in rows:
        lines.append(f"  • #{r['id']}  {r['name'][:70]}")
    lines.append("\nRun one with `/run <id>` (optionally + initial input).")
    return "\n".join(lines)


@command("run", description="Run a workflow: /run <workflow_id> [initial input]",
         aliases=["r"])
def _cmd_run(user_id: int, args: str, msg: InboundMessage) -> str:
    parts = args.split(None, 1)
    if not parts or not parts[0].isdigit():
        return "Usage: `/run <workflow_id> [initial input]`\nUse /workflows to see ids."
    wid = int(parts[0])
    initial = parts[1] if len(parts) > 1 else ""
    wf = db.fetch_one(
        "SELECT id, name FROM workflows WHERE id = %s AND user_id = %s",
        (wid, user_id),
    )
    if not wf:
        return f"Workflow #{wid} not found (or not yours)."
    from ... import engine
    try:
        run_id = engine.dispatch_workflow(
            workflow_id=wid, user_id=user_id, initial_input=initial,
            trigger_source="chat",
            trigger_context={"im_platform": msg.platform, "im_chat": msg.external_id},
            priority="normal",
        )
    except Exception as e:
        return f"⚠️ Failed to dispatch: {e}"
    return (f"✅ Dispatched run #{run_id} on *{wf['name']}*.\n"
            "Use `/runs` to see status or `/run_status {run_id}` for detail.")


@command("run_status", description="Detail for a specific run: /run_status <run_id>",
         aliases=["rs"])
def _cmd_run_status(user_id: int, args: str, msg: InboundMessage) -> str:
    parts = args.split()
    if not parts or not parts[0].isdigit():
        return "Usage: `/run_status <run_id>`"
    rid = int(parts[0])
    r = db.fetch_one(
        "SELECT r.id, r.status, r.total_cost_usd, r.started_at, r.finished_at, "
        "       w.name AS workflow_name, r.final_output "
        "FROM runs r LEFT JOIN workflows w ON w.id = r.workflow_id "
        "WHERE r.id = %s AND r.user_id = %s",
        (rid, user_id),
    )
    if not r:
        return f"Run #{rid} not found (or not yours)."
    lines = [
        f"*Run #{r['id']}* — {r['workflow_name']}",
        f"Status: `{r['status']}`",
        f"Cost: ${float(r['total_cost_usd'] or 0):.4f}",
        f"Started: {r['started_at']}",
    ]
    if r.get("finished_at"):
        lines.append(f"Finished: {r['finished_at']}")
    fo = r.get("final_output") or ""
    if fo:
        snip = fo[:400] + ("…" if len(fo) > 400 else "")
        lines.append(f"\nOutput preview:\n```\n{snip}\n```")
    return "\n".join(lines)


@command("projects", description="List active projects")
def _cmd_projects(user_id: int, args: str, msg: InboundMessage) -> str:
    rows = db.fetch_all(
        "SELECT id, name, status FROM projects WHERE user_id = %s AND status != 'done' "
        "ORDER BY id DESC LIMIT 20",
        (user_id,),
    )
    if not rows:
        return "No active projects."
    lines = ["*Active projects*"]
    for r in rows:
        lines.append(f"  • #{r['id']}  [{r['status']}]  {r['name'][:70]}")
    lines.append("\n`/project <id>` for details.")
    return "\n".join(lines)


@command("project", description="Project status: /project <id>")
def _cmd_project(user_id: int, args: str, msg: InboundMessage) -> str:
    parts = args.split()
    if not parts or not parts[0].isdigit():
        return "Usage: `/project <id>`"
    pid = int(parts[0])
    p = db.fetch_one(
        "SELECT id, name, status, goal FROM projects WHERE id = %s AND user_id = %s",
        (pid, user_id),
    )
    if not p:
        return f"Project #{pid} not found (or not yours)."
    member_count = db.fetch_one(
        "SELECT COUNT(*) AS c FROM project_members WHERE project_id = %s", (pid,),
    )
    run_stats = db.fetch_one(
        "SELECT COUNT(*) AS total, "
        "       SUM(CASE WHEN status='running' THEN 1 ELSE 0 END) AS running, "
        "       SUM(CASE WHEN status='done' THEN 1 ELSE 0 END) AS done, "
        "       SUM(total_cost_usd)::float AS cost "
        "FROM runs WHERE project_id = %s",
        (pid,),
    )
    artifact_count = db.fetch_one(
        "SELECT COUNT(*) AS c FROM project_artifacts WHERE project_id = %s", (pid,),
    )
    lines = [
        f"*Project #{p['id']}* — {p['name']}",
        f"Status: `{p['status']}`",
    ]
    if p.get("goal"):
        lines.append(f"Goal: {p['goal'][:200]}")
    lines.extend([
        f"Members: {member_count['c']}",
        f"Runs: {run_stats['total'] or 0} total · "
        f"{run_stats['running'] or 0} running · "
        f"{run_stats['done'] or 0} done · "
        f"${float(run_stats['cost'] or 0):.2f} spent",
        f"Artifacts: {artifact_count['c']}",
    ])
    return "\n".join(lines)


@command("hire", description="Ask Lead to propose a hire: /hire <role description>")
def _cmd_hire(user_id: int, args: str, msg: InboundMessage) -> str:
    """Shortcut: routes a hire ask through the normal Lead chat path
    but with a prompt that nudges Lead to emit a ```hire``` fence
    block. The user still accepts via the web UI card — IM just kicks
    off the proposal."""
    if not args.strip():
        return "Usage: `/hire <role description>`  e.g. /hire need a data scientist"
    prompt = (
        f"Please propose a new agent hire for this role: {args.strip()}. "
        f"Emit a ```hire``` fenced block with name, role_title, description, "
        f"system_prompt, and rationale. Keep system_prompt under ~250 words."
    )
    # Reuse free-text path so session continuity + thread tagging apply.
    forwarded = InboundMessage(
        platform=msg.platform, external_id=msg.external_id,
        sender_display=msg.sender_display, text=prompt, raw=msg.raw,
    )
    # We can't import router at top-level (circular — router imports
    # this module). Late import + call the free-text helper directly.
    from .router import _handle_lead
    return _handle_lead(user_id, forwarded)


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
