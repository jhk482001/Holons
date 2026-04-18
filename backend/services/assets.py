"""Asset library service — CRUD + grants + audit + usage tracking.

An "asset" is any reusable thing an agent can call: a skill, a built-in
tool, an MCP server, or a RAG source. Assets live at library level (owned
by a user, optionally granted to other users) and are bound to specific
agents via agent_assets rows.

This module owns all mutations of the asset_* tables so that the audit log
stays consistent — every create/update/delete/grant/assign goes through
one of these functions.

Usage statistics (`asset_usage_log`) are written by the engine (one row per
tool call) via `record_usage()`.
"""
from __future__ import annotations

import json
from typing import Any

from .. import db
from . import asset_crypto


VALID_KINDS = ("skill", "tool", "mcp", "rag")
VALID_ACTIONS = (
    "create", "update", "delete",
    "enable", "disable",
    "grant", "revoke",
    "assign", "unassign",
)

# Minimal fields the frontend sees for any asset row. Credential_encrypted
# is never returned — only a boolean `has_credential`.
_LIST_COLS = (
    "id, kind, name, description, owner_user_id, enabled, "
    "config, metadata, created_at, updated_at, "
    "(credential_encrypted IS NOT NULL) AS has_credential"
)


# ============================================================================
# Audit + usage log helpers
# ============================================================================

def _audit(
    actor_user_id: int | None,
    asset_id: int | None,
    action: str,
    before: dict | None = None,
    after: dict | None = None,
) -> None:
    """Append one row to asset_audit_log. `before` / `after` are JSONB
    blobs; pass None to skip. Datetimes in the payload are serialized as
    ISO strings via `default=str` — JSON has no native datetime type."""
    assert action in VALID_ACTIONS, f"invalid audit action {action!r}"
    db.execute(
        """
        INSERT INTO asset_audit_log
          (actor_user_id, asset_id, action, before_state, after_state)
        VALUES (%s, %s, %s, %s::jsonb, %s::jsonb)
        """,
        (
            actor_user_id,
            asset_id,
            action,
            json.dumps(before, default=str) if before is not None else None,
            json.dumps(after, default=str) if after is not None else None,
        ),
    )


def list_audit(asset_id: int, limit: int = 50) -> list[dict]:
    return db.fetch_all(
        """
        SELECT a.id, a.asset_id, a.actor_user_id, u.username AS actor_username,
               a.action, a.before_state, a.after_state, a.created_at
        FROM asset_audit_log a
        LEFT JOIN as_users u ON u.id = a.actor_user_id
        WHERE a.asset_id = %s
        ORDER BY a.created_at DESC
        LIMIT %s
        """,
        (asset_id, limit),
    )


def record_usage(
    asset_id: int,
    user_id: int,
    *,
    agent_id: int | None = None,
    run_id: int | None = None,
    turn: int | None = None,
    duration_ms: int | None = None,
    ok: bool = True,
    error: str | None = None,
) -> None:
    """Record a single asset invocation — called by the engine after a
    tool call completes. Failures here are swallowed (usage tracking must
    never break a run)."""
    try:
        db.execute(
            """
            INSERT INTO asset_usage_log
              (asset_id, user_id, agent_id, run_id, turn, duration_ms, ok, error)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (asset_id, user_id, agent_id, run_id, turn, duration_ms, ok, error),
        )
    except Exception:  # noqa: BLE001
        pass


def usage_timeseries(asset_id: int, *, hours: int = 24) -> list[dict]:
    """Return hourly usage counts for the last `hours` hours. Empty hours
    are included (count=0) so the chart has no gaps."""
    return db.fetch_all(
        """
        WITH slots AS (
            SELECT generate_series(
                date_trunc('hour', NOW() - (%s || ' hours')::interval),
                date_trunc('hour', NOW()),
                '1 hour'::interval
            ) AS bucket
        )
        SELECT
            slots.bucket AS bucket,
            COALESCE(COUNT(u.id), 0) AS n
        FROM slots
        LEFT JOIN asset_usage_log u
            ON u.asset_id = %s
           AND date_trunc('hour', u.called_at) = slots.bucket
        GROUP BY slots.bucket
        ORDER BY slots.bucket ASC
        """,
        (hours, asset_id),
    )


def usage_summary(asset_id: int) -> dict:
    row = db.fetch_one(
        """
        SELECT
            COUNT(*) AS total_calls,
            COUNT(DISTINCT user_id) AS distinct_users,
            COUNT(DISTINCT agent_id) FILTER (WHERE agent_id IS NOT NULL) AS distinct_agents,
            MAX(called_at) AS last_used_at
        FROM asset_usage_log
        WHERE asset_id = %s
        """,
        (asset_id,),
    ) or {}
    return {
        "total_calls": int(row.get("total_calls") or 0),
        "distinct_users": int(row.get("distinct_users") or 0),
        "distinct_agents": int(row.get("distinct_agents") or 0),
        "last_used_at": row.get("last_used_at"),
    }


# ============================================================================
# CRUD
# ============================================================================

def list_assets(
    *,
    kind: str | None = None,
    viewer_user_id: int | None = None,
    include_granted: bool = True,
) -> list[dict]:
    """List assets visible to `viewer_user_id`.

    * Admin (viewer_user_id=None) sees every asset.
    * Non-admin sees assets they own PLUS assets granted to them if
      `include_granted`.

    Each row is augmented with cheap stats: how many users it's granted
    to, how many agents have it assigned, total calls so far, last used at.
    """
    where = []
    params: list[Any] = []
    if kind:
        where.append("a.kind = %s")
        params.append(kind)
    if viewer_user_id is not None:
        if include_granted:
            where.append(
                "(a.owner_user_id = %s OR EXISTS ("
                "  SELECT 1 FROM asset_grants g "
                "  WHERE g.asset_id = a.id AND g.grantee_user_id = %s"
                "))"
            )
            params.extend([viewer_user_id, viewer_user_id])
        else:
            where.append("a.owner_user_id = %s")
            params.append(viewer_user_id)

    where_sql = " WHERE " + " AND ".join(where) if where else ""
    sql = f"""
        SELECT
            a.id, a.kind, a.name, a.description, a.owner_user_id, a.enabled,
            a.config, a.metadata, a.created_at, a.updated_at,
            (a.credential_encrypted IS NOT NULL) AS has_credential,
            u.username AS owner_username,
            u.display_name AS owner_display_name,
            (SELECT COUNT(*) FROM asset_grants g WHERE g.asset_id = a.id)
                AS grant_count,
            (SELECT COUNT(*) FROM agent_assets aa WHERE aa.asset_id = a.id)
                AS assigned_agent_count,
            (SELECT COUNT(*) FROM asset_usage_log l WHERE l.asset_id = a.id)
                AS total_calls,
            (SELECT MAX(called_at) FROM asset_usage_log l WHERE l.asset_id = a.id)
                AS last_used_at
        FROM asset_items a
        LEFT JOIN as_users u ON u.id = a.owner_user_id
        {where_sql}
        ORDER BY a.kind, a.name
    """
    return db.fetch_all(sql, tuple(params))


def get_asset(asset_id: int) -> dict | None:
    return db.fetch_one(
        f"SELECT {_LIST_COLS} FROM asset_items WHERE id = %s",
        (asset_id,),
    )


def create_asset(
    *,
    actor_user_id: int,
    kind: str,
    name: str,
    description: str | None = None,
    owner_user_id: int | None = None,
    config: dict | None = None,
    metadata: dict | None = None,
    credential_plaintext: str | None = None,
    enabled: bool = True,
) -> int:
    if kind not in VALID_KINDS:
        raise ValueError(f"invalid asset kind {kind!r}")
    if not name or not name.strip():
        raise ValueError("asset name is required")
    owner = owner_user_id if owner_user_id is not None else actor_user_id
    new_id = db.execute_returning(
        """
        INSERT INTO asset_items
          (kind, name, description, owner_user_id, enabled,
           config, metadata, credential_encrypted)
        VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s)
        RETURNING id
        """,
        (
            kind,
            name.strip(),
            description,
            owner,
            enabled,
            json.dumps(config or {}),
            json.dumps(metadata or {}),
            asset_crypto.encrypt(credential_plaintext),
        ),
    )
    _audit(
        actor_user_id,
        new_id,
        "create",
        after={
            "kind": kind, "name": name, "owner_user_id": owner,
            "enabled": enabled, "config": config or {},
        },
    )
    return new_id


def update_asset(
    asset_id: int,
    actor_user_id: int,
    *,
    name: str | None = None,
    description: str | None = None,
    config: dict | None = None,
    metadata: dict | None = None,
    credential_plaintext: str | None = None,
    credential_clear: bool = False,
    enabled: bool | None = None,
) -> dict | None:
    """Patch-style update. Pass `credential_clear=True` to wipe an existing
    credential; pass a non-None `credential_plaintext` to replace it."""
    before = get_asset(asset_id)
    if not before:
        return None

    sets, params = [], []
    if name is not None:
        sets.append("name = %s")
        params.append(name.strip())
    if description is not None:
        sets.append("description = %s")
        params.append(description)
    if config is not None:
        sets.append("config = %s::jsonb")
        params.append(json.dumps(config))
    if metadata is not None:
        sets.append("metadata = %s::jsonb")
        params.append(json.dumps(metadata))
    if enabled is not None:
        sets.append("enabled = %s")
        params.append(enabled)
    if credential_clear:
        sets.append("credential_encrypted = NULL")
    elif credential_plaintext is not None:
        sets.append("credential_encrypted = %s")
        params.append(asset_crypto.encrypt(credential_plaintext))

    if not sets:
        return before
    sets.append("updated_at = NOW()")
    params.append(asset_id)
    db.execute(
        f"UPDATE asset_items SET {', '.join(sets)} WHERE id = %s",
        tuple(params),
    )
    after = get_asset(asset_id)
    _audit(actor_user_id, asset_id, "update", before=_scrub(before), after=_scrub(after))
    # Synthesize separate enable/disable audit entries when that field changed
    if enabled is not None and bool(before.get("enabled")) != enabled:
        _audit(
            actor_user_id, asset_id,
            "enable" if enabled else "disable",
        )
    return after


def delete_asset(asset_id: int, actor_user_id: int) -> bool:
    before = get_asset(asset_id)
    if not before:
        return False
    # Cascade will wipe grants/agent_assets/usage/audit — but record the
    # delete event first so the audit row survives the cascade of audit
    # entries that belongs to this asset_id.
    _audit(actor_user_id, asset_id, "delete", before=_scrub(before))
    db.execute("DELETE FROM asset_items WHERE id = %s", (asset_id,))
    return True


def _scrub(row: dict | None) -> dict | None:
    """Strip credential / raw fernet tokens from a row before it's written
    into an audit log or returned to the frontend."""
    if not row:
        return row
    out = dict(row)
    out.pop("credential_encrypted", None)
    return out


# ============================================================================
# Grants
# ============================================================================

def list_grants(asset_id: int) -> list[dict]:
    return db.fetch_all(
        """
        SELECT g.id, g.asset_id, g.grantee_user_id, g.granted_by, g.created_at,
               u.username AS grantee_username, u.display_name AS grantee_display_name
        FROM asset_grants g
        JOIN as_users u ON u.id = g.grantee_user_id
        WHERE g.asset_id = %s
        ORDER BY u.username
        """,
        (asset_id,),
    )


def grant(asset_id: int, grantee_user_id: int, actor_user_id: int) -> int:
    gid = db.execute_returning(
        """
        INSERT INTO asset_grants (asset_id, grantee_user_id, granted_by)
        VALUES (%s, %s, %s)
        ON CONFLICT (asset_id, grantee_user_id) DO UPDATE
            SET granted_by = EXCLUDED.granted_by,
                created_at = NOW()
        RETURNING id
        """,
        (asset_id, grantee_user_id, actor_user_id),
    )
    _audit(
        actor_user_id, asset_id, "grant",
        after={"grantee_user_id": grantee_user_id},
    )
    return gid


def revoke(asset_id: int, grantee_user_id: int, actor_user_id: int) -> bool:
    rows = db.execute(
        "DELETE FROM asset_grants WHERE asset_id = %s AND grantee_user_id = %s",
        (asset_id, grantee_user_id),
    )
    if rows:
        _audit(
            actor_user_id, asset_id, "revoke",
            before={"grantee_user_id": grantee_user_id},
        )
    return bool(rows)


def visible_to_user(asset_id: int, user_id: int) -> bool:
    """Is this asset owned by or granted to the user? Admins ignore this."""
    row = db.fetch_one(
        """
        SELECT 1 FROM asset_items WHERE id = %s AND owner_user_id = %s
        UNION
        SELECT 1 FROM asset_grants WHERE asset_id = %s AND grantee_user_id = %s
        """,
        (asset_id, user_id, asset_id, user_id),
    )
    return bool(row)


# ============================================================================
# Agent assignment
# ============================================================================

def assign_to_agent(
    asset_id: int, agent_id: int, actor_user_id: int, *, enabled: bool = True,
) -> int:
    aid = db.execute_returning(
        """
        INSERT INTO agent_assets (agent_id, asset_id, enabled)
        VALUES (%s, %s, %s)
        ON CONFLICT (agent_id, asset_id) DO UPDATE
            SET enabled = EXCLUDED.enabled
        RETURNING id
        """,
        (agent_id, asset_id, enabled),
    )
    _audit(
        actor_user_id, asset_id, "assign",
        after={"agent_id": agent_id, "enabled": enabled},
    )
    return aid


def unassign_from_agent(asset_id: int, agent_id: int, actor_user_id: int) -> bool:
    rows = db.execute(
        "DELETE FROM agent_assets WHERE agent_id = %s AND asset_id = %s",
        (agent_id, asset_id),
    )
    if rows:
        _audit(
            actor_user_id, asset_id, "unassign",
            before={"agent_id": agent_id},
        )
    return bool(rows)


def list_agent_assignments(asset_id: int) -> list[dict]:
    return db.fetch_all(
        """
        SELECT aa.id, aa.agent_id, aa.enabled, aa.created_at,
               ag.name AS agent_name, ag.user_id AS agent_owner_id
        FROM agent_assets aa
        JOIN agents ag ON ag.id = aa.agent_id
        WHERE aa.asset_id = %s
        ORDER BY ag.name
        """,
        (asset_id,),
    )


def list_assets_for_agent(agent_id: int, kind: str | None = None) -> list[dict]:
    """Return assets currently bound to this agent, filtered by kind."""
    params: list[Any] = [agent_id]
    kind_filter = ""
    if kind:
        kind_filter = " AND a.kind = %s"
        params.append(kind)
    return db.fetch_all(
        f"""
        SELECT a.id, a.kind, a.name, a.description, a.owner_user_id,
               a.enabled, a.config, a.metadata, a.created_at, a.updated_at,
               (a.credential_encrypted IS NOT NULL) AS has_credential
        FROM asset_items a
        JOIN agent_assets aa ON aa.asset_id = a.id
        WHERE aa.agent_id = %s AND aa.enabled = TRUE AND a.enabled = TRUE{kind_filter}
        ORDER BY a.kind, a.name
        """,
        tuple(params),
    )
