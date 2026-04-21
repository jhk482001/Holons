"""Agent escalation — when an agent is stuck, who does it ask?

Three-layer escalation:
    1. peer_consult → ask same-group/workflow peers
    2. lead → escalate to Lead agent
    3. user → ask the task owner directly

Policy is per-user (as_users.escalation_policy).
Task owner is always the `run.user_id`, NOT the agent owner (important
when agents are shared/rented).
"""
from __future__ import annotations

import json
from typing import Any

from .. import db, queue
from . import notifications


def raise_escalation(
    task_id: int,
    uncertainty: str,
    *,
    context: dict | None = None,
) -> dict:
    """Called by an agent (or its middleware) when it's stuck. Routes based on
    task owner's escalation_policy.

    Returns a dict describing what happened:
        {"route": "peer_consult|lead|user", "escalation_id": int, ...}
    """
    task = db.fetch_one("SELECT * FROM agent_tasks WHERE id = %s", (task_id,))
    if not task:
        raise ValueError(f"task {task_id} not found")

    run = db.fetch_one("SELECT user_id, workflow_id FROM runs WHERE id = %s", (task["run_id"],))
    if not run:
        raise ValueError(f"run {task['run_id']} not found")

    task_owner_id = run["user_id"]
    agent_id = task["agent_id"]

    # Load user's escalation policy
    user = db.fetch_one("SELECT escalation_policy FROM as_users WHERE id = %s", (task_owner_id,))
    policy = (user or {}).get("escalation_policy") or "lead_first"

    # Create escalation record
    esc_id = db.execute_returning(
        """
        INSERT INTO agent_escalations
            (task_id, run_id, raising_agent_id, task_owner_id, uncertainty, context, status)
        VALUES (%s, %s, %s, %s, %s, %s::jsonb, 'pending')
        RETURNING id
        """,
        (task_id, task["run_id"], agent_id, task_owner_id, uncertainty,
         json.dumps(context or {})),
    )

    # Try routing
    if policy == "autonomous":
        # Try peer consult first
        peer = _find_peer(run["workflow_id"], agent_id)
        if peer:
            _route_to_peer(esc_id, task, peer)
            return {"route": "peer_consult", "escalation_id": esc_id, "peer": peer["id"]}

    if policy in ("autonomous", "lead_first"):
        lead = db.fetch_one(
            "SELECT id, name FROM agents WHERE user_id = %s AND is_lead = TRUE LIMIT 1",
            (task_owner_id,),
        )
        if lead:
            _route_to_lead(esc_id, task, lead, uncertainty)
            return {"route": "lead", "escalation_id": esc_id, "lead_id": lead["id"]}

    # Fall through to user
    _route_to_user(esc_id, task, task_owner_id, uncertainty, context)
    return {"route": "user", "escalation_id": esc_id}


def _find_peer(workflow_id: int, raising_agent_id: int) -> dict | None:
    """Find a peer agent in the same workflow that could potentially help.
    Very simple: pick any other agent in the workflow's nodes.
    Future: use skill matching via LLM.
    """
    rows = db.fetch_all(
        """
        SELECT DISTINCT a.id, a.name, a.role_title, a.description, a.system_prompt
        FROM workflow_nodes wn
        LEFT JOIN agents a ON a.id = wn.agent_id
        WHERE wn.workflow_id = %s AND a.id IS NOT NULL AND a.id != %s
        LIMIT 5
        """,
        (workflow_id, raising_agent_id),
    )
    return rows[0] if rows else None


def _route_to_peer(esc_id: int, task: dict, peer: dict) -> None:
    db.execute(
        """
        UPDATE agent_escalations
        SET route = 'peer_consult', consulted_agent_id = %s
        WHERE id = %s
        """,
        (peer["id"], esc_id),
    )
    # Enqueue a peer consult task with higher priority
    queue.enqueue_task(
        agent_id=peer["id"],
        payload={
            "kind": "peer_consult",
            "escalation_id": esc_id,
            "raising_task_id": task["id"],
            "prompt": f"Please help evaluate:\n\n{task.get('payload', {}).get('prompt', '')}",
        },
        run_id=task["run_id"],
        task_type="peer_consult",
        priority="high",
        skip_checks=True,
    )


def _route_to_lead(esc_id: int, task: dict, lead: dict, uncertainty: str) -> None:
    db.execute(
        "UPDATE agent_escalations SET route = 'lead' WHERE id = %s",
        (esc_id,),
    )
    # For lead, we emit a notification that appears in the bell center
    notifications.emit(
        user_id=task.get("user_id") or 0,
        type="escalation",
        severity="warn",
        title=f"Agent needs help: {uncertainty[:50]}",
        body=uncertainty,
        related_escalation_id=esc_id,
        related_run_id=task["run_id"],
        related_agent_id=task["agent_id"],
    )


def _route_to_user(
    esc_id: int, task: dict, task_owner_id: int,
    uncertainty: str, context: dict | None,
) -> None:
    db.execute(
        "UPDATE agent_escalations SET route = 'user' WHERE id = %s",
        (esc_id,),
    )
    notifications.emit(
        user_id=task_owner_id,
        type="escalation",
        severity="warn",
        title=f"Agent awaiting your input: {uncertainty[:50]}",
        body=uncertainty,
        related_escalation_id=esc_id,
        related_run_id=task["run_id"],
        related_agent_id=task["agent_id"],
        action_payload={"context": context or {}},
    )


# ============================================================================
# Resolve (when someone answers)
# ============================================================================

def resolve(esc_id: int, resolution: str) -> None:
    db.execute(
        """
        UPDATE agent_escalations
        SET status = 'resolved', resolution = %s, resolved_at = NOW()
        WHERE id = %s
        """,
        (resolution, esc_id),
    )
    # Resume the blocked task
    esc = db.fetch_one("SELECT task_id FROM agent_escalations WHERE id = %s", (esc_id,))
    if esc:
        queue.resume_paused(esc["task_id"])
