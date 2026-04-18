"""Agent quota enforcement + rolling window accounting.

Each agent can have multiple quota entries (hourly/daily/weekly/monthly).
Middleware calls `check_and_consume()` before/after each LLM call.

If any quota is exceeded, the agent's status flips to `quota_exceeded` and
a notification is emitted to the owner.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from .. import db
from . import notifications


WINDOW_SECONDS = {
    "hourly":  3600,
    "daily":   86400,
    "weekly":  604800,
    "monthly": 2592000,
}


def _reset_window_if_stale(quota: dict, now: datetime) -> dict:
    """If the quota window has rolled over, reset counters.
    If the window has never started, just seed `current_window_started_at`
    without resetting counters (since the existing counters are fresh).
    """
    if quota["window_type"] in ("project", "lifetime"):
        return quota

    duration = WINDOW_SECONDS.get(quota["window_type"])
    if duration is None:
        return quota

    started = quota.get("current_window_started_at")
    if started is None:
        # First use of this quota — seed start time, don't wipe counters
        db.execute(
            "UPDATE agent_quotas SET current_window_started_at = %s WHERE id = %s",
            (now, quota["id"]),
        )
        quota["current_window_started_at"] = now
        return quota

    # Ensure tz-awareness for subtraction
    if hasattr(started, "tzinfo") and started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)

    if (now - started).total_seconds() >= duration:
        db.execute(
            """
            UPDATE agent_quotas
            SET current_tokens = 0,
                current_cost_usd = 0,
                current_window_started_at = %s
            WHERE id = %s
            """,
            (now, quota["id"]),
        )
        quota["current_tokens"] = 0
        quota["current_cost_usd"] = 0
        quota["current_window_started_at"] = now
    return quota


def check_before(agent_id: int) -> dict | None:
    """Check every active quota for this agent. Returns a breach dict if any
    limit is already exceeded, else None (meaning OK to proceed).
    """
    now = datetime.now(timezone.utc)
    quotas = db.fetch_all(
        "SELECT * FROM agent_quotas WHERE agent_id = %s AND enabled = TRUE",
        (agent_id,),
    )
    for q in quotas:
        q = _reset_window_if_stale(q, now)
        max_tokens = q.get("max_tokens")
        max_cost = q.get("max_cost_usd")
        cur_tokens = int(q.get("current_tokens") or 0)
        cur_cost = float(q.get("current_cost_usd") or 0)

        if max_tokens and cur_tokens >= max_tokens:
            return {"type": "tokens", "quota_id": q["id"], "name": q["name"],
                    "used": cur_tokens, "limit": max_tokens}
        if max_cost and cur_cost >= float(max_cost):
            return {"type": "cost", "quota_id": q["id"], "name": q["name"],
                    "used": cur_cost, "limit": float(max_cost)}
    return None


def consume(agent_id: int, input_tokens: int, output_tokens: int, cost_usd: float) -> None:
    """Record usage against all enabled quotas for this agent."""
    total_tokens = int(input_tokens or 0) + int(output_tokens or 0)
    cost = float(cost_usd or 0)
    db.execute(
        """
        UPDATE agent_quotas
        SET current_tokens = current_tokens + %s,
            current_cost_usd = current_cost_usd + %s
        WHERE agent_id = %s AND enabled = TRUE
        """,
        (total_tokens, cost, agent_id),
    )
    # Check for post-consume breach and flip agent status
    now = datetime.now(timezone.utc)
    quotas = db.fetch_all(
        "SELECT * FROM agent_quotas WHERE agent_id = %s AND enabled = TRUE",
        (agent_id,),
    )
    agent = db.fetch_one("SELECT user_id, name, status FROM agents WHERE id = %s", (agent_id,))
    if not agent:
        return

    for q in quotas:
        q = _reset_window_if_stale(q, now)
        max_tokens = q.get("max_tokens")
        max_cost = q.get("max_cost_usd")
        over_tokens = max_tokens and q["current_tokens"] >= max_tokens
        over_cost = max_cost and q["current_cost_usd"] >= float(max_cost)

        if (over_tokens or over_cost) and q.get("hard_limit") and agent["status"] == "active":
            db.execute(
                "UPDATE agents SET status = 'quota_exceeded' WHERE id = %s",
                (agent_id,),
            )
            notifications.emit(
                agent["user_id"],
                "budget_exceeded",
                severity="error",
                title=f"{agent['name']} 超出預算",
                body=f"Quota「{q['name']}」已觸頂，agent 已暫停接新任務。",
                related_agent_id=agent_id,
                action_payload={"quota_id": q["id"]},
            )
            return

        # Warn at 80%
        pct_tokens = (q["current_tokens"] / max_tokens) if max_tokens else 0
        pct_cost = (float(q["current_cost_usd"]) / float(max_cost)) if max_cost else 0
        if max(pct_tokens, pct_cost) >= 0.8 and max(pct_tokens, pct_cost) < 1.0:
            already_warned = db.fetch_one(
                """
                SELECT 1 FROM notifications
                WHERE user_id = %s AND type = 'budget_warning'
                  AND related_agent_id = %s
                  AND status = 'unread'
                  AND created_at > NOW() - INTERVAL '1 hour'
                LIMIT 1
                """,
                (agent["user_id"], agent_id),
            )
            if not already_warned:
                notifications.emit(
                    agent["user_id"],
                    "budget_warning",
                    severity="warn",
                    title=f"{agent['name']} 接近預算上限",
                    body=f"Quota「{q['name']}」已使用 {int(max(pct_tokens, pct_cost) * 100)}%",
                    related_agent_id=agent_id,
                )


# ============================================================================
# CRUD
# ============================================================================

def create_quota(agent_id: int, data: dict) -> int:
    return db.execute_returning(
        """
        INSERT INTO agent_quotas
            (agent_id, name, window_type, max_tokens, max_tpm, max_rpm,
             max_cost_usd, hard_limit, enabled)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, TRUE)
        RETURNING id
        """,
        (
            agent_id,
            data.get("name"),
            data.get("window_type", "monthly"),
            data.get("max_tokens"),
            data.get("max_tpm"),
            data.get("max_rpm"),
            data.get("max_cost_usd"),
            bool(data.get("hard_limit", True)),
        ),
    )


def list_quotas(agent_id: int) -> list[dict]:
    return db.fetch_all(
        "SELECT * FROM agent_quotas WHERE agent_id = %s",
        (agent_id,),
    )


def delete_quota(quota_id: int) -> None:
    db.execute("DELETE FROM agent_quotas WHERE id = %s", (quota_id,))


# ============================================================================
# Auto-topup — tracked in the auto_topup_events ledger so we can rate-limit
# properly and show the user what fired.
# ============================================================================

MAX_AUTO_TOPUP_PER_DAY = 10
MAX_AUTO_TOPUP_COST_USD = 5.0


def maybe_autotopup(user_id: int, agent_id: int) -> float:
    """If the user has auto-topup enabled and today's count is under the cap,
    record one event and return the extra allowed cost. Returns 0 otherwise.
    Intended to be called from the existing `check_before`/`consume` flow
    when an agent-level cost quota is about to block a request.
    """
    u = db.fetch_one(
        "SELECT auto_topup_enabled, auto_topup_per_topup_cost, auto_topup_max_per_day "
        "FROM as_users WHERE id = %s",
        (user_id,),
    ) or {}
    if not u.get("auto_topup_enabled"):
        return 0.0

    per = min(float(u.get("auto_topup_per_topup_cost") or 0),
              MAX_AUTO_TOPUP_COST_USD)
    user_cap = min(int(u.get("auto_topup_max_per_day") or 0),
                   MAX_AUTO_TOPUP_PER_DAY)
    if per <= 0 or user_cap <= 0:
        return 0.0

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    used = db.fetch_one(
        "SELECT COUNT(*) AS n FROM auto_topup_events "
        "WHERE user_id = %s AND event_date = %s",
        (user_id, today),
    ) or {}
    if int(used.get("n") or 0) >= user_cap:
        return 0.0

    db.execute(
        """INSERT INTO auto_topup_events
           (user_id, agent_id, amount_cost_usd, event_date)
           VALUES (%s, %s, %s, %s)""",
        (user_id, agent_id, per, today),
    )
    return per


def list_autotopup_events(user_id: int, days: int = 14) -> list[dict]:
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime(
        "%Y-%m-%d"
    )
    return db.fetch_all(
        """SELECT e.id, e.agent_id, a.name AS agent_name,
                  e.amount_cost_usd, e.event_date, e.created_at
           FROM auto_topup_events e
           LEFT JOIN agents a ON a.id = e.agent_id
           WHERE e.user_id = %s AND e.event_date >= %s
           ORDER BY e.created_at DESC""",
        (user_id, since),
    )


# ============================================================================
# Phase A — project allocation layer
# ----------------------------------------------------------------------------
# On top of the rolling-window agent quotas above, each agent can be a
# member of one or more projects, each with a daily / monthly allocation
# percentage. When a step is attributed to a project, it must fit within
# *both* the agent quota AND the project slice. "Last request may exceed"
# rule is implemented as a strict `>=` check — once usage reaches the cap,
# further attempts are blocked.
# ============================================================================

def _proj_usage(agent_id: int, project_id: int, hours: int) -> dict:
    """Tokens + cost spent by this agent on this project in the last N hrs."""
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    row = db.fetch_one(
        """
        SELECT COALESCE(SUM(cost_usd), 0)::float              AS cost,
               COALESCE(SUM(input_tokens + output_tokens), 0) AS tokens
        FROM run_steps
        WHERE agent_id = %s AND project_id = %s AND started_at >= %s
        """,
        (agent_id, project_id, since),
    ) or {}
    return {
        "cost": float(row.get("cost") or 0),
        "tokens": int(row.get("tokens") or 0),
    }


def _effective_agent_caps(agent_id: int) -> dict:
    """Largest (cost, tokens) caps across this agent's active daily quotas.
    Used to compute the % allocation slice for a project. A project that
    says `30%` means 30% of these caps per day.
    """
    rows = db.fetch_all(
        """
        SELECT window_type, max_tokens, max_cost_usd
        FROM agent_quotas
        WHERE agent_id = %s AND enabled = TRUE
        """,
        (agent_id,),
    )
    daily_tokens = None
    daily_cost = None
    monthly_tokens = None
    monthly_cost = None
    for r in rows:
        w = r.get("window_type")
        if w == "daily":
            if r.get("max_tokens"):
                daily_tokens = max(daily_tokens or 0, int(r["max_tokens"]))
            if r.get("max_cost_usd"):
                daily_cost = max(daily_cost or 0.0, float(r["max_cost_usd"]))
        elif w == "monthly":
            if r.get("max_tokens"):
                monthly_tokens = max(monthly_tokens or 0, int(r["max_tokens"]))
            if r.get("max_cost_usd"):
                monthly_cost = max(monthly_cost or 0.0, float(r["max_cost_usd"]))
    return {
        "daily_tokens": daily_tokens,
        "daily_cost": daily_cost,
        "monthly_tokens": monthly_tokens,
        "monthly_cost": monthly_cost,
    }


def check_project_allocation(agent_id: int, project_id: int) -> dict | None:
    """Return a breach dict if the project slice is exceeded, else None."""
    row = db.fetch_one(
        """
        SELECT pm.daily_alloc_pct, pm.monthly_alloc_pct,
               p.name AS project_name, p.status AS project_status
        FROM project_members pm
        JOIN projects p ON p.id = pm.project_id
        WHERE pm.project_id = %s AND pm.agent_id = %s
        """,
        (project_id, agent_id),
    )
    if not row:
        return {
            "type": "not_member",
            "project_id": project_id,
            "message": f"agent is not a member of project {project_id}",
        }
    if row["project_status"] in ("paused", "archived", "done"):
        return {
            "type": "project_status",
            "project_id": project_id,
            "status": row["project_status"],
            "message": f"project '{row['project_name']}' is {row['project_status']}",
        }

    caps = _effective_agent_caps(agent_id)
    daily_pct = float(row.get("daily_alloc_pct") or 100.0) / 100.0
    monthly_pct = float(row.get("monthly_alloc_pct") or 100.0) / 100.0
    used_day = _proj_usage(agent_id, project_id, hours=24)
    used_month = _proj_usage(agent_id, project_id, hours=24 * 30)

    if caps["daily_cost"] is not None:
        slice_cap = caps["daily_cost"] * daily_pct
        if used_day["cost"] >= slice_cap:
            return {
                "type": "project_daily_cost",
                "project_id": project_id,
                "project_name": row["project_name"],
                "used": used_day["cost"],
                "limit": slice_cap,
                "message": (
                    f"project '{row['project_name']}' daily cost slice "
                    f"${used_day['cost']:.2f}/${slice_cap:.2f} "
                    f"({int(daily_pct*100)}% of agent cap)"
                ),
            }
    if caps["daily_tokens"] is not None:
        slice_cap_t = int(caps["daily_tokens"] * daily_pct)
        if used_day["tokens"] >= slice_cap_t:
            return {
                "type": "project_daily_tokens",
                "project_id": project_id,
                "project_name": row["project_name"],
                "used": used_day["tokens"],
                "limit": slice_cap_t,
                "message": (
                    f"project '{row['project_name']}' daily token slice "
                    f"{used_day['tokens']:,}/{slice_cap_t:,}"
                ),
            }
    if caps["monthly_cost"] is not None:
        slice_cap = caps["monthly_cost"] * monthly_pct
        if used_month["cost"] >= slice_cap:
            return {
                "type": "project_monthly_cost",
                "project_id": project_id,
                "project_name": row["project_name"],
                "used": used_month["cost"],
                "limit": slice_cap,
                "message": (
                    f"project '{row['project_name']}' monthly cost slice hit "
                    f"${used_month['cost']:.2f}/${slice_cap:.2f}"
                ),
            }
    return None


def can_run(agent_id: int, project_id: int | None = None) -> dict:
    """Combined gate for enqueue / dispatch.

    Returns: {ok: bool, reason: str, agent_breach: dict|None,
              project_breach: dict|None}
    """
    agent_breach = check_before(agent_id)
    project_breach = check_project_allocation(agent_id, project_id) if project_id else None

    reasons: list[str] = []
    if agent_breach:
        reasons.append(
            f"agent quota '{agent_breach.get('name','?')}' hit: "
            f"{agent_breach.get('used')}/{agent_breach.get('limit')} "
            f"({agent_breach.get('type')})"
        )
    if project_breach:
        reasons.append(project_breach.get("message", str(project_breach)))
    return {
        "ok": not reasons,
        "reason": "; ".join(reasons),
        "agent_breach": agent_breach,
        "project_breach": project_breach,
    }


def quota_headroom_summary(agent_id: int, project_id: int | None = None) -> str:
    """Short human line for injecting into a Lead / coordinator prompt so
    it knows how much each agent can still spend today.
    """
    d = can_run(agent_id, project_id=project_id)
    if not d["ok"]:
        return f"BLOCKED ({d['reason']})"
    caps = _effective_agent_caps(agent_id)
    if caps["daily_cost"]:
        used = _usage_today_cost(agent_id)
        return f"${used:.2f}/${caps['daily_cost']:.2f} today"
    if caps["daily_tokens"]:
        used = _usage_today_tokens(agent_id)
        return f"{used:,}/{caps['daily_tokens']:,} tokens today"
    return "no daily cap"


def _usage_today_cost(agent_id: int) -> float:
    since = (datetime.now(timezone.utc) - timedelta(hours=24)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    row = db.fetch_one(
        "SELECT COALESCE(SUM(cost_usd), 0)::float AS c FROM run_steps "
        "WHERE agent_id = %s AND started_at >= %s",
        (agent_id, since),
    ) or {}
    return float(row.get("c") or 0)


def _usage_today_tokens(agent_id: int) -> int:
    since = (datetime.now(timezone.utc) - timedelta(hours=24)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    row = db.fetch_one(
        "SELECT COALESCE(SUM(input_tokens + output_tokens), 0) AS t FROM run_steps "
        "WHERE agent_id = %s AND started_at >= %s",
        (agent_id, since),
    ) or {}
    return int(row.get("t") or 0)
