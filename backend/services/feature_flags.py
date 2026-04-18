"""System feature flags.

Each flag has a stable string key (referenced by enforcement code) and an
`admin_only` boolean that toggles whether regular users can access the
corresponding feature. The set of flags is fixed in `DEFAULTS` below — the
seed_flags() helper upserts the default row for any missing key on startup,
so new releases can introduce flags without a separate migration step.

Enforcement call pattern (in app.py routes)::

    from .services import feature_flags

    @app.route("/api/some/feature", methods=["POST"])
    @login_required
    def some_feature_route():
        if feature_flags.is_admin_only("create_mcp_server") and not _is_admin():
            return jsonify({"error": "admin only"}), 403
        ...

The helper `require_feature(feature_key)` wraps that pattern as a decorator.
"""
from __future__ import annotations

from functools import wraps
from typing import Callable

from flask import jsonify, session

from .. import db


# ============================================================================
# The canonical set of flags. Default admin-only booleans reflect the spec:
#   - view_audit_log, manage_user_quota → default OPEN (all users can)
#   - create_mcp_server, create_rag_source, grant_mcp_rag → default ADMIN ONLY
#
# To add a new flag in a later phase, append a row here and the next app
# startup will upsert it into the table without touching existing rows.
# ============================================================================

DEFAULTS: list[dict] = [
    {
        "feature": "view_audit_log",
        "label": "View audit log",
        "description": "Allow users to see system browsing / API call history.",
        "admin_only": False,
    },
    {
        "feature": "manage_user_quota",
        "label": "Set user quotas",
        "description": "Allow users to set token / cost quotas for themselves or others.",
        "admin_only": False,
    },
    {
        "feature": "create_mcp_server",
        "label": "Add MCP server",
        "description": "Allow users to add custom MCP servers to the asset library.",
        "admin_only": True,
    },
    {
        "feature": "create_rag_source",
        "label": "Add RAG knowledge base",
        "description": "Allow users to create or upload RAG knowledge bases.",
        "admin_only": True,
    },
    {
        "feature": "grant_mcp_rag",
        "label": "Share MCP / RAG with other users",
        "description": "Allow users to share their MCP or RAG assets with other users.",
        "admin_only": True,
    },
    {
        "feature": "default_language",
        "label": "Default Language",
        "description": "Default UI language for new users (en / zh-TW).",
        "admin_only": False,
        "value": "en",
    },
    {
        "feature": "lead_max_steps_default",
        "label": "Lead Max Steps (Default)",
        "description": "Default max workflow steps for new users.",
        "admin_only": False,
        "value": "10",
    },
    {
        "feature": "lead_max_steps_hard_limit",
        "label": "Lead Max Steps (Hard Limit)",
        "description": "Hard upper limit for max workflow steps — users cannot exceed this.",
        "admin_only": False,
        "value": "1000",
    },
    {
        "feature": "lead_max_tokens_default",
        "label": "Lead Max Tokens (Default)",
        "description": "Default max tokens per workflow run for new users.",
        "admin_only": False,
        "value": "50000",
    },
    {
        "feature": "lead_max_tokens_hard_limit",
        "label": "Lead Max Tokens (Hard Limit)",
        "description": "Hard upper limit for max tokens per workflow run.",
        "admin_only": False,
        "value": "500000",
    },
]


def seed_flags() -> None:
    """Upsert every default row. Only inserts rows that don't exist; never
    overwrites an admin's existing toggle choice."""
    for row in DEFAULTS:
        db.execute(
            """
            INSERT INTO system_feature_flags (feature, label, description, admin_only)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (feature) DO UPDATE
                SET label = EXCLUDED.label,
                    description = EXCLUDED.description
            """,
            (row["feature"], row["label"], row["description"], row["admin_only"]),
        )


def list_flags() -> list[dict]:
    return db.fetch_all(
        "SELECT feature, label, description, admin_only, updated_at "
        "FROM system_feature_flags ORDER BY feature"
    )


def get_flag(feature: str) -> dict | None:
    return db.fetch_one(
        "SELECT feature, label, description, admin_only, value FROM system_feature_flags "
        "WHERE feature = %s",
        (feature,),
    )


def get_value(feature: str) -> str | None:
    """Return the `value` column for a feature flag, or None if not found."""
    row = get_flag(feature)
    return row["value"] if row and row.get("value") is not None else None


def set_admin_only(feature: str, admin_only: bool) -> bool:
    """Return True if the row existed and was updated, False if no such
    feature key is registered."""
    result = db.fetch_one(
        "UPDATE system_feature_flags SET admin_only = %s, updated_at = NOW() "
        "WHERE feature = %s RETURNING feature",
        (admin_only, feature),
    )
    return result is not None


def is_admin_only(feature: str) -> bool:
    """Read the flag. Unknown features default to False (open) — a new
    flag that hasn't been seeded yet should not accidentally block access."""
    row = get_flag(feature)
    return bool(row and row["admin_only"])


def _current_role() -> str | None:
    uid = session.get("user_id")
    if not uid:
        return None
    row = db.fetch_one("SELECT role FROM as_users WHERE id = %s", (uid,))
    return row.get("role") if row else None


def require_feature(feature: str) -> Callable:
    """Route decorator that enforces a feature flag at request time.

    If the flag is set to admin_only and the caller isn't admin → 403.
    If the caller isn't authenticated at all → 401.
    """
    def deco(f: Callable) -> Callable:
        @wraps(f)
        def wrapper(*args, **kwargs):
            uid = session.get("user_id")
            if not uid:
                return jsonify({"error": "not authenticated"}), 401
            if is_admin_only(feature):
                if _current_role() != "admin":
                    return jsonify({
                        "error": f"feature '{feature}' is admin-only",
                    }), 403
            return f(*args, **kwargs)
        return wrapper
    return deco
