"""Task queue built on top of Postgres using FOR UPDATE SKIP LOCKED.

Each agent has its own logical queue (`agent_tasks` rows with that agent_id).
Workers poll `claim_next_task(agent_id)` which atomically picks the highest
priority queued/paused task and marks it running.

Priority system:
    priority_num:  1=low, 2=normal, 3=high, 4=critical, 5=urgent
    ORDER BY priority_num DESC, created_at ASC
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

from . import db

log = logging.getLogger("agent_company.queue")


PRIORITY_NUM = {
    "low": 1,
    "normal": 2,
    "high": 3,
    "critical": 4,
    "urgent": 5,
}


class QueueError(Exception):
    pass


class AgentUnavailable(QueueError):
    """Agent is paused / offline / budget_exceeded / quota_exceeded."""


class QueueFull(QueueError):
    """Agent or user queue depth limit reached."""


# ============================================================================
# Enqueue
# ============================================================================

def enqueue_task(
    agent_id: int,
    payload: dict,
    *,
    run_id: int | None = None,
    step_id: int | None = None,
    parent_task_id: int | None = None,
    task_type: str = "workflow_step",
    priority: str = "normal",
    source: str = "auto",
    skip_checks: bool = False,
) -> int:
    """Enqueue a task for the given agent. Returns the new task id.

    Raises AgentUnavailable / QueueFull if checks fail.
    Set `skip_checks=True` to bypass (used when re-queueing paused tasks).
    """
    if not skip_checks:
        _precheck(agent_id, source=source)

    priority_num = PRIORITY_NUM.get(priority, 2)
    task_id = db.execute_returning(
        """
        INSERT INTO agent_tasks
            (agent_id, run_id, step_id, parent_task_id, task_type,
             priority, priority_num, status, payload)
        VALUES (%s, %s, %s, %s, %s, %s, %s, 'queued', %s::jsonb)
        RETURNING id
        """,
        (
            agent_id, run_id, step_id, parent_task_id, task_type,
            priority, priority_num, json.dumps(payload),
        ),
    )
    log.info("enqueued task %s agent=%s pri=%s", task_id, agent_id, priority)
    return task_id


def _precheck(agent_id: int, source: str = "auto") -> None:
    """Verify agent is available and queue depths are within limits."""
    agent = db.fetch_one(
        "SELECT id, user_id, status, max_queue_depth FROM agents WHERE id = %s",
        (agent_id,),
    )
    if agent is None:
        raise AgentUnavailable(f"agent {agent_id} not found")

    if agent["status"] in ("paused", "offline", "budget_exceeded", "quota_exceeded"):
        raise AgentUnavailable(f"agent {agent_id} status={agent['status']}")

    # Agent-level queue depth
    agent_qd = db.fetch_one(
        "SELECT COUNT(*) AS c FROM agent_tasks WHERE agent_id = %s AND status IN ('queued','running','paused')",
        (agent_id,),
    )["c"]
    if agent_qd >= agent["max_queue_depth"]:
        raise QueueFull(f"agent {agent_id} queue {agent_qd}/{agent['max_queue_depth']}")

    # User-level total queue depth
    user = db.fetch_one(
        "SELECT max_total_queue_depth FROM as_users WHERE id = %s",
        (agent["user_id"],),
    )
    if user:
        user_total = db.fetch_one(
            """
            SELECT COUNT(*) AS c
            FROM agent_tasks t
            JOIN agents a ON a.id = t.agent_id
            WHERE a.user_id = %s AND t.status IN ('queued','running','paused')
            """,
            (agent["user_id"],),
        )["c"]
        if user_total >= user["max_total_queue_depth"]:
            raise QueueFull(f"user {agent['user_id']} total queue {user_total}/{user['max_total_queue_depth']}")


# ============================================================================
# Claim (atomic dequeue)
# ============================================================================

def claim_next_task(agent_id: int) -> dict | None:
    """Atomically pick the next task for this agent and mark it running.

    Uses FOR UPDATE SKIP LOCKED so multiple workers on the same agent
    (shouldn't happen with concurrency=1 but safe) don't race.

    Returns the task row (as dict) or None if nothing queued.
    """
    with db.txn_cursor() as (conn, cur):
        cur.execute(
            """
            SELECT *
            FROM agent_tasks
            WHERE agent_id = %s AND status IN ('queued','paused')
            ORDER BY priority_num DESC, created_at ASC
            LIMIT 1
            FOR UPDATE SKIP LOCKED
            """,
            (agent_id,),
        )
        row = cur.fetchone()
        if row is None:
            return None

        cur.execute(
            """
            UPDATE agent_tasks
            SET status = 'running', started_at = NOW()
            WHERE id = %s
            """,
            (row["id"],),
        )
        row["status"] = "running"
        row["started_at"] = datetime.utcnow()
        return row


# ============================================================================
# Mark done / failed / paused / cancelled
# ============================================================================

def mark_done(task_id: int, result: dict | None = None) -> None:
    db.execute(
        """
        UPDATE agent_tasks
        SET status = 'done', finished_at = NOW(), result = %s::jsonb
        WHERE id = %s
        """,
        (json.dumps(result) if result is not None else None, task_id),
    )


def mark_failed(task_id: int, error: str) -> None:
    db.execute(
        """
        UPDATE agent_tasks
        SET status = 'failed', finished_at = NOW(), error_message = %s
        WHERE id = %s
        """,
        (error, task_id),
    )


def mark_paused(task_id: int, snapshot: dict) -> None:
    """Mark a running task as paused, saving progress.

    Used by urgent-interrupt flow — worker gets signalled, pauses current task,
    processes urgent, then resumes.
    """
    db.execute(
        """
        UPDATE agent_tasks
        SET status = 'paused', progress_snapshot = %s::jsonb
        WHERE id = %s
        """,
        (json.dumps(snapshot), task_id),
    )


def mark_cancelled(task_id: int, reason: str = "") -> None:
    db.execute(
        """
        UPDATE agent_tasks
        SET status = 'cancelled', finished_at = NOW(), error_message = %s
        WHERE id = %s
        """,
        (reason or "cancelled", task_id),
    )


# ============================================================================
# Hot stop
# ============================================================================

def cancel_run(run_id: int) -> int:
    """Hot-stop: mark run as cancelling and cancel all queued tasks.

    Currently-running tasks will naturally abort on their next HotStopMiddleware
    check (before their next step). Already-completed tasks stay done.

    If after cancelling there are no running/paused tasks left, immediately
    finalize the run as cancelled (handles the race where cancel fires
    before the worker picked anything up).
    """
    db.execute("UPDATE runs SET status = 'cancelling' WHERE id = %s AND status = 'running'", (run_id,))
    rc = db.execute(
        """
        UPDATE agent_tasks
        SET status = 'cancelled', finished_at = NOW(), error_message = 'run cancelled by user'
        WHERE run_id = %s AND status = 'queued'
        """,
        (run_id,),
    )

    # If no running/paused tasks remain, finalize immediately
    row = db.fetch_one(
        "SELECT COUNT(*) AS c FROM agent_tasks WHERE run_id = %s AND status IN ('running','paused')",
        (run_id,),
    )
    if row and row["c"] == 0:
        db.execute(
            "UPDATE runs SET status='cancelled', finished_at=NOW() WHERE id = %s AND status = 'cancelling'",
            (run_id,),
        )
    return rc


# ============================================================================
# Queries for UI / dashboard
# ============================================================================

def queue_for_agent(agent_id: int, limit: int = 50) -> list[dict]:
    return db.fetch_all(
        """
        SELECT id, agent_id, run_id, task_type, priority, status, created_at, started_at, payload
        FROM agent_tasks
        WHERE agent_id = %s
        ORDER BY
            CASE status WHEN 'running' THEN 1 WHEN 'paused' THEN 2 WHEN 'queued' THEN 3 ELSE 4 END,
            priority_num DESC,
            created_at ASC
        LIMIT %s
        """,
        (agent_id, limit),
    )


def queue_depth(agent_id: int) -> int:
    row = db.fetch_one(
        "SELECT COUNT(*) AS c FROM agent_tasks WHERE agent_id = %s AND status IN ('queued','paused')",
        (agent_id,),
    )
    return row["c"] if row else 0


def running_task(agent_id: int) -> dict | None:
    return db.fetch_one(
        "SELECT * FROM agent_tasks WHERE agent_id = %s AND status = 'running' LIMIT 1",
        (agent_id,),
    )


def get_task(task_id: int) -> dict | None:
    return db.fetch_one("SELECT * FROM agent_tasks WHERE id = %s", (task_id,))


# ============================================================================
# Resume paused task (after urgent interrupt)
# ============================================================================

def resume_paused(task_id: int) -> None:
    """Mark a paused task back to queued so the worker picks it up again."""
    db.execute(
        "UPDATE agent_tasks SET status = 'queued' WHERE id = %s AND status = 'paused'",
        (task_id,),
    )
