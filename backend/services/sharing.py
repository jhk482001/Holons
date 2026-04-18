"""Agent sharing / multi-tenancy service.

Three visibility modes:
    private   — only owner can see/use
    user_list — specific users (by id) can see/use
    org_wide  — all users in the same deployment can see/use

Plus external federation via external_agent_links (cross-deployment).

Also: agent profile export/import (JSON bundle for transferring between
deployments or as a "template" for new agents).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from .. import db


# ============================================================================
# Visibility / access check
# ============================================================================

def user_can_access_agent(user_id: int, agent_id: int) -> bool:
    """Return True if `user_id` has any access to `agent_id`."""
    a = db.fetch_one("SELECT * FROM agents WHERE id = %s", (agent_id,))
    if not a:
        return False

    # Owner always can
    if a["user_id"] == user_id:
        return True

    # Explicit share record always grants access regardless of visibility
    share = db.fetch_one(
        """
        SELECT id FROM agent_shares
        WHERE agent_id = %s AND borrower_user_id = %s
          AND revoked_at IS NULL
          AND (expires_at IS NULL OR expires_at > NOW())
        LIMIT 1
        """,
        (agent_id, user_id),
    )
    if share:
        return True

    # Visibility rules
    if a["visibility"] == "org_wide":
        return True
    if a["visibility"] == "user_list":
        allowed = a.get("visible_user_ids") or []
        if isinstance(allowed, str):
            try:
                allowed = json.loads(allowed)
            except json.JSONDecodeError:
                allowed = []
        return user_id in allowed

    return False


def set_visibility(user_id: int, agent_id: int, visibility: str,
                   visible_user_ids: list[int] | None = None) -> None:
    if visibility not in ("private", "user_list", "org_wide"):
        raise ValueError(f"invalid visibility: {visibility}")
    db.execute(
        """
        UPDATE agents
        SET visibility = %s, visible_user_ids = %s::jsonb, updated_at = NOW()
        WHERE id = %s AND user_id = %s
        """,
        (visibility, json.dumps(visible_user_ids or []), agent_id, user_id),
    )


# ============================================================================
# Explicit shares (owner grants to specific borrower)
# ============================================================================

def share_agent(
    owner_id: int, agent_id: int, borrower_id: int,
    *, scope: str = "invoke", price_per_call_usd: float = 0,
    max_calls_per_day: int | None = None, expires_at: datetime | None = None,
) -> int:
    a = db.fetch_one("SELECT user_id FROM agents WHERE id = %s", (agent_id,))
    if not a or a["user_id"] != owner_id:
        raise PermissionError("not the owner")
    return db.execute_returning(
        """
        INSERT INTO agent_shares
            (agent_id, owner_user_id, borrower_user_id, scope,
             price_per_call_usd, max_calls_per_day, expires_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (agent_id, owner_id, borrower_id, scope, price_per_call_usd,
         max_calls_per_day, expires_at),
    )


def revoke_share(owner_id: int, share_id: int) -> None:
    db.execute(
        "UPDATE agent_shares SET revoked_at = NOW() WHERE id = %s AND owner_user_id = %s",
        (share_id, owner_id),
    )


def list_shares_out(user_id: int) -> list[dict]:
    """List agents this user has shared with others."""
    return db.fetch_all(
        """
        SELECT s.*, a.name AS agent_name, u.username AS borrower_username
        FROM agent_shares s
        JOIN agents a ON a.id = s.agent_id
        JOIN as_users u ON u.id = s.borrower_user_id
        WHERE s.owner_user_id = %s AND s.revoked_at IS NULL
        ORDER BY s.created_at DESC
        """,
        (user_id,),
    )


def list_shares_in(user_id: int) -> list[dict]:
    """List agents others have shared with this user."""
    return db.fetch_all(
        """
        SELECT s.*, a.name AS agent_name, a.role_title, u.username AS owner_username
        FROM agent_shares s
        JOIN agents a ON a.id = s.agent_id
        JOIN as_users u ON u.id = s.owner_user_id
        WHERE s.borrower_user_id = %s AND s.revoked_at IS NULL
          AND (s.expires_at IS NULL OR s.expires_at > NOW())
        ORDER BY s.created_at DESC
        """,
        (user_id,),
    )


# ============================================================================
# Agent profile export / import
# ============================================================================

def export_agent_profile(agent_id: int) -> dict:
    """Export an agent as a transferable JSON bundle.
    Includes basic profile + internalized (approved) skills.
    """
    a = db.fetch_one("SELECT * FROM agents WHERE id = %s", (agent_id,))
    if not a:
        raise ValueError(f"agent {agent_id} not found")

    from . import skill_extractor
    skills_bundle = skill_extractor.export_skills(agent_id)

    return {
        "schema_version": "1.0",
        "profile": {
            "name": a["name"],
            "role_title": a.get("role_title"),
            "description": a.get("description"),
            "avatar_config": a.get("avatar_config") or {},
        },
        "behavior": {
            "system_prompt": a.get("system_prompt") or "",
            "few_shot_examples": a.get("few_shot") or "",
            "internalized_skills": skills_bundle.get("skills", []),
        },
        "model": {
            "primary": a.get("primary_model_id"),
            "fallback": a.get("fallback_model_id"),
        },
        "metadata": {
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "source_agent_id": agent_id,
            "source_user_id": a["user_id"],
        },
    }


def import_agent_profile(user_id: int, bundle: dict, *, name_suffix: str = "") -> int:
    """Import an agent profile into this user's account. Returns new agent_id."""
    profile = bundle.get("profile", {})
    behavior = bundle.get("behavior", {})
    model = bundle.get("model", {})

    new_name = (profile.get("name") or "Imported Agent") + (f" {name_suffix}" if name_suffix else "")

    agent_id = db.execute_returning(
        """
        INSERT INTO agents
            (user_id, owner_user_id, name, role_title, description,
             system_prompt, few_shot, primary_model_id, fallback_model_id,
             avatar_config, external_origin)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s)
        RETURNING id
        """,
        (
            user_id, user_id,
            new_name,
            profile.get("role_title"),
            profile.get("description"),
            behavior.get("system_prompt"),
            behavior.get("few_shot_examples"),
            (model or {}).get("primary"),
            (model or {}).get("fallback"),
            json.dumps(profile.get("avatar_config") or {}),
            f"imported from user {bundle.get('metadata', {}).get('source_user_id')}",
        ),
    )

    # Import skills if present
    skills = behavior.get("internalized_skills") or []
    if skills:
        from . import skill_extractor
        skill_extractor.import_skills(agent_id, {"skills": skills})

    return agent_id


# ============================================================================
# Skill guardrails (user + org level)
# ============================================================================

def add_guardrail(scope: str, user_id: int | None, rule_type: str,
                  rule_value: str, description: str = "") -> int:
    return db.execute_returning(
        """
        INSERT INTO skill_guardrails
            (scope, user_id, rule_type, rule_value, description)
        VALUES (%s, %s, %s, %s, %s)
        RETURNING id
        """,
        (scope, user_id, rule_type, rule_value, description),
    )


def list_guardrails(user_id: int | None = None) -> list[dict]:
    if user_id is None:
        return db.fetch_all("SELECT * FROM skill_guardrails WHERE scope = 'org' ORDER BY id DESC")
    return db.fetch_all(
        "SELECT * FROM skill_guardrails WHERE scope = 'org' OR (scope = 'user' AND user_id = %s) ORDER BY id DESC",
        (user_id,),
    )


def delete_guardrail(guardrail_id: int, user_id: int | None) -> None:
    if user_id is None:
        db.execute("DELETE FROM skill_guardrails WHERE id = %s AND scope = 'org'", (guardrail_id,))
    else:
        db.execute(
            "DELETE FROM skill_guardrails WHERE id = %s AND scope = 'user' AND user_id = %s",
            (guardrail_id, user_id),
        )
