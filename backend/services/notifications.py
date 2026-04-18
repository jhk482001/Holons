"""Notifications — bell/notification center backed by `notifications` table."""
from __future__ import annotations

import json
from typing import Any

from .. import db


VALID_TYPES = {
    "queue_conflict", "budget_warning", "budget_exceeded",
    "agent_off_duty", "skill_suggested", "workflow_failed",
    "share_request", "lead_proposal", "escalation",
}


def emit(
    user_id: int,
    type: str,
    *,
    title: str,
    body: str = "",
    severity: str = "info",
    action_payload: dict | None = None,
    related_run_id: int | None = None,
    related_agent_id: int | None = None,
    related_workflow_id: int | None = None,
    related_escalation_id: int | None = None,
) -> int:
    if type not in VALID_TYPES:
        raise ValueError(f"invalid notification type: {type}")
    return db.execute_returning(
        """
        INSERT INTO notifications
            (user_id, type, severity, title, body, action_payload,
             related_run_id, related_agent_id, related_workflow_id, related_escalation_id)
        VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s)
        RETURNING id
        """,
        (
            user_id, type, severity, title, body,
            json.dumps(action_payload or {}),
            related_run_id, related_agent_id, related_workflow_id, related_escalation_id,
        ),
    )


def list_notifications(user_id: int, *, status: str | None = None, limit: int = 50) -> list[dict]:
    if status:
        rows = db.fetch_all(
            """
            SELECT * FROM notifications
            WHERE user_id = %s AND status = %s
            ORDER BY created_at DESC
            LIMIT %s
            """,
            (user_id, status, limit),
        )
    else:
        rows = db.fetch_all(
            "SELECT * FROM notifications WHERE user_id = %s ORDER BY created_at DESC LIMIT %s",
            (user_id, limit),
        )
    return rows


def unread_count(user_id: int) -> int:
    row = db.fetch_one(
        "SELECT COUNT(*) AS c FROM notifications WHERE user_id = %s AND status = 'unread'",
        (user_id,),
    )
    return row["c"] if row else 0


def mark_read(user_id: int, notif_id: int) -> None:
    db.execute(
        "UPDATE notifications SET status = 'read' WHERE id = %s AND user_id = %s",
        (notif_id, user_id),
    )


def mark_all_read(user_id: int) -> int:
    """Mark every unread notification for this user as read. Returns the
    number of rows touched. Used by the bell dropdown's auto-read-on-open
    behaviour."""
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE notifications SET status = 'read' "
                "WHERE user_id = %s AND status = 'unread'",
                (user_id,),
            )
            return cur.rowcount


def resolve(user_id: int, notif_id: int, resolution: str) -> None:
    db.execute(
        """
        UPDATE notifications
        SET status = 'resolved', resolution = %s, resolved_at = NOW()
        WHERE id = %s AND user_id = %s
        """,
        (resolution, notif_id, user_id),
    )


def dismiss(user_id: int, notif_id: int) -> None:
    db.execute(
        "UPDATE notifications SET status = 'dismissed' WHERE id = %s AND user_id = %s",
        (notif_id, user_id),
    )
