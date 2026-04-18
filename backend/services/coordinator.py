"""Project coordinator — a project-scope Lead.

A coordinator is a regular agent that has been designated as the project's
`coordinator_agent_id`. When a user chats inside a project, coordinator
responses use the same plumbing as `lead_agent.chat()` but with the roster
restricted to project members and the system prompt enriched with the
project goal, milestones, and each member's remaining allocation.

We intentionally reuse `lead_agent.chat()` rather than forking, so prompt
changes (workflow JSON shape, decomposition rules, etc.) stay in one place.

Thread model: project chats live in `lead_conversations` like ordinary
Lead threads, tagged by writing the project_id into `lead_messages.metadata`
on each user message. The `get_or_create_thread` helper creates a thread
per (user, project) pair and persists the association via a fixed-format
thread id prefix (`proj-<pid>-`).
"""
from __future__ import annotations

import uuid

from .. import db
from . import lead_agent


def _thread_id_prefix(project_id: int) -> str:
    return f"proj-{project_id}-"


def get_or_create_thread(user_id: int, project_id: int) -> str:
    """Return an active thread id for this (user, project), creating one
    if none. We identify project threads by their id prefix to keep things
    schema-light for Phase A — lead_conversations doesn't need a new column.
    """
    row = db.fetch_one(
        """
        SELECT thread_id FROM lead_conversations
        WHERE user_id = %s AND status = 'active'
          AND thread_id LIKE %s
        ORDER BY id DESC LIMIT 1
        """,
        (user_id, f"{_thread_id_prefix(project_id)}%"),
    )
    if row:
        return row["thread_id"]
    new_id = _thread_id_prefix(project_id) + uuid.uuid4().hex[:12]
    db.execute(
        """
        INSERT INTO lead_conversations (user_id, thread_id, status)
        VALUES (%s, %s, 'active')
        """,
        (user_id, new_id),
    )
    return new_id


def chat(user_id: int, project_id: int, user_message: str,
         thread_id: str | None = None) -> dict:
    """Send a message to the project's coordinator. Identical contract to
    `lead_agent.chat` but scoped to this project.
    """
    if not thread_id:
        thread_id = get_or_create_thread(user_id, project_id)
    return lead_agent.chat(
        user_id, user_message,
        thread_id=thread_id,
        project_id=project_id,
    )


def list_messages(thread_id: str, limit: int = 50) -> list[dict]:
    return db.fetch_all(
        """
        SELECT id, role, content, proposed_workflow_id, metadata, created_at
        FROM lead_messages
        WHERE thread_id = %s AND cancelled = FALSE
        ORDER BY created_at ASC, id ASC
        LIMIT %s
        """,
        (thread_id, limit),
    )
