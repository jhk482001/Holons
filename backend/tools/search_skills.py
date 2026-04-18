"""search_skills — look up approved skills for the current user's agents.

Safe: read-only. Returns a small list of matching skills with name +
description so the agent can cite or chain prompts off an existing skill.
"""
from __future__ import annotations

from .. import db
from . import register


SPEC = {
    "name": "search_skills",
    "description": (
        "Search the user's approved agent skills. Use this when you need to "
        "check what capabilities exist in the team before designing a "
        "workflow or deciding how to solve a task. Returns name, owning "
        "agent, short description, and usage count."
    ),
    "inputSchema": {
        "json": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Free-text keywords to match against skill name/description.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max results to return (1–20, default 5).",
                    "minimum": 1,
                    "maximum": 20,
                },
            },
            "required": ["query"],
        },
    },
}


def handler(args: dict, ctx: dict) -> dict:
    query = (args.get("query") or "").strip()
    if not query:
        return {"skills": [], "note": "empty query"}
    limit = int(args.get("limit") or 5)
    limit = max(1, min(20, limit))
    user_id = ctx.get("agent_user_id")
    if not user_id:
        return {"skills": [], "note": "no user context"}

    # Match by simple ILIKE across name + description. Scoped to the
    # user's own agents (skills are per-agent) and approved skills only.
    like = f"%{query}%"
    rows = db.fetch_all(
        """
        SELECT s.name, s.description, s.slug, s.times_used,
               a.name AS agent_name, a.id AS agent_id
        FROM agent_skills s
        JOIN agents a ON a.id = s.agent_id
        WHERE a.user_id = %s
          AND s.approved_by_user = TRUE
          AND (s.name ILIKE %s OR s.description ILIKE %s OR s.slug ILIKE %s)
        ORDER BY s.times_used DESC, s.id DESC
        LIMIT %s
        """,
        (user_id, like, like, like, limit),
    )
    return {
        "query": query,
        "count": len(rows),
        "skills": [
            {
                "name": r["name"],
                "description": r.get("description") or "",
                "slug": r["slug"],
                "owning_agent": r["agent_name"],
                "times_used": r["times_used"],
            }
            for r in rows
        ],
    }


register("search_skills", SPEC, handler)
