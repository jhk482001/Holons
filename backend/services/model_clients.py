"""Model client management — Phase 7.

A "model client" is a (provider, region/endpoint, credentials) bundle that
admins manage centrally. Agents reference a client by id and pick a specific
model_id from the client's `config.models` list. This module owns:

- CRUD on `model_clients`
- Grant management (`model_client_grants`)
- "Default for new users" book-keeping + auto-grant on user creation
- Seed of the initial Bedrock client + backfill of legacy agents
- Dispatch: the engine asks `get_client_for_agent(agent_id)` and gets back
  an `LLMClient` instance plus the resolved `model_id` to pass into it.

Credential storage reuses `asset_crypto.Fernet` keys so we don't proliferate
encryption mechanisms.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

from .. import db
from . import asset_crypto

log = logging.getLogger("agent_company.model_clients")


# ============================================================================
# Kind metadata — what fields each kind stores in config/credential
# ============================================================================

KIND_SCHEMAS: dict[str, dict[str, Any]] = {
    "bedrock": {
        "label": "AWS Bedrock",
        "credential_fields": ["access_key", "secret_key"],
        "config_fields": ["region", "models"],
        "hint": "region points to an AWS Bedrock-supported region; models is the list of model ids available through this connection.",
        "example_config": {
            "region": "us-east-1",
            "models": [
                "anthropic.claude-3-5-haiku-20241022-v1:0",
                "anthropic.claude-3-5-sonnet-20241022-v2:0",
            ],
        },
        "example_credential": {
            "access_key": "AKIAIOSFODNN7EXAMPLE",
            "secret_key": "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
        },
    },
    "claude_native": {
        "label": "Anthropic Claude (native API)",
        "credential_fields": ["api_key"],
        "config_fields": ["base_url", "models"],
        "hint": "base_url defaults to https://api.anthropic.com; API keys are formatted as sk-ant-…",
        "example_config": {
            "base_url": "https://api.anthropic.com",
            "models": ["claude-3-5-haiku-latest", "claude-sonnet-4-5-latest"],
        },
        "example_credential": {"api_key": "sk-ant-api03-XXXXXXXXXXXXXXXXXXXX"},
    },
    "openai": {
        "label": "OpenAI",
        "credential_fields": ["api_key"],
        "config_fields": ["base_url", "organization", "models"],
        "hint": "base_url defaults to https://api.openai.com/v1; organization is optional.",
        "example_config": {
            "base_url": "https://api.openai.com/v1",
            "organization": "org-optional-remove-if-unused",
            "models": ["gpt-4o-mini", "gpt-4o"],
        },
        "example_credential": {"api_key": "sk-proj-XXXXXXXXXXXXXXXXXXXX"},
    },
    "azure_openai": {
        "label": "Azure OpenAI",
        "credential_fields": ["api_key"],
        "config_fields": ["endpoint", "api_version", "deployments"],
        "hint": "endpoint example: https://<resource>.openai.azure.com; deployments is an array of Azure deployment names.",
        "example_config": {
            "endpoint": "https://your-resource.openai.azure.com",
            "api_version": "2024-06-01",
            "deployments": ["gpt-4o-mini-deployment", "gpt-4o-deployment"],
        },
        "example_credential": {"api_key": "XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX"},
    },
    "gemini": {
        "label": "Google Gemini",
        "credential_fields": ["api_key"],
        "config_fields": ["models"],
        "hint": "Uses your Google AI Studio API key; models is a list of gemini-* model ids.",
        "example_config": {"models": ["gemini-2.0-flash", "gemini-2.5-pro"]},
        "example_credential": {"api_key": "AIzaSyXXXXXXXXXXXXXXXXXXXX"},
    },
    "minimax": {
        "label": "Minimax",
        "credential_fields": ["api_key"],
        "config_fields": ["group_id", "models"],
        "hint": "Minimax chatcompletion v2 API; group_id is your account's groupId.",
        "example_config": {
            "group_id": "1234567890123456",
            "models": ["MiniMax-Text-01", "abab6.5s-chat"],
        },
        "example_credential": {"api_key": "eyJhbGciOiJSUzI1NiIs..."},
    },
    "local": {
        "label": "Local / OpenAI-compatible",
        "credential_fields": ["api_key"],
        "config_fields": ["base_url", "models"],
        "hint": "OpenAI-compatible endpoint (Ollama, LM Studio, vLLM, etc.). Example base_url: http://localhost:11434/v1; api_key may be blank or any placeholder string.",
        "example_config": {
            "base_url": "http://localhost:11434/v1",
            "models": ["llama3.1:8b", "qwen2.5:14b"],
        },
        "example_credential": {"api_key": "ollama"},
    },
}


# ============================================================================
# Row shaping helpers
# ============================================================================

def _row_to_dict(row: dict, *, include_grants: bool = False) -> dict:
    """Convert a DB row to the JSON shape we return from the API.

    We never return `credential_encrypted` — instead emit `has_credential`
    so the UI knows whether a value is set without exposing the ciphertext.
    Grants are optionally merged in when listing via the `grant_count` stats.
    """
    out = {
        "id": row["id"],
        "name": row["name"],
        "kind": row["kind"],
        "description": row.get("description"),
        "config": row.get("config") or {},
        "has_credential": bool(row.get("credential_encrypted")),
        "enabled": bool(row.get("enabled", True)),
        "default_for_new_users": bool(row.get("default_for_new_users", False)),
        "created_by": row.get("created_by"),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
        "last_test_at": row.get("last_test_at"),
        "last_test_status": row.get("last_test_status"),
        "last_test_message": row.get("last_test_message"),
    }
    if include_grants:
        out["grant_count"] = int(row.get("grant_count") or 0)
        out["agent_count"] = int(row.get("agent_count") or 0)
    return out


# ============================================================================
# Listing + get
# ============================================================================

def list_for_admin() -> list[dict]:
    """Return every client with grant/agent counts. Admin-only view."""
    rows = db.fetch_all(
        """
        SELECT c.*,
               (SELECT COUNT(*) FROM model_client_grants g WHERE g.client_id = c.id) AS grant_count,
               (SELECT COUNT(*) FROM agents a WHERE a.model_client_id = c.id) AS agent_count
        FROM model_clients c
        ORDER BY c.id
        """
    )
    return [_row_to_dict(r, include_grants=True) for r in rows]


def list_for_user(user_id: int) -> list[dict]:
    """Clients the given user is allowed to use. Admins see everything; normal
    users see only clients they have a grant for (plus `default_for_new_users`
    entries — see below)."""
    rows = db.fetch_all(
        """
        SELECT DISTINCT c.*
        FROM model_clients c
        LEFT JOIN model_client_grants g
          ON g.client_id = c.id AND g.user_id = %s
        WHERE c.enabled = TRUE
          AND (g.id IS NOT NULL OR c.default_for_new_users = TRUE)
        ORDER BY c.id
        """,
        (user_id,),
    )
    return [_row_to_dict(r) for r in rows]


def get(client_id: int) -> Optional[dict]:
    row = db.fetch_one("SELECT * FROM model_clients WHERE id = %s", (client_id,))
    return _row_to_dict(row) if row else None


def get_raw(client_id: int) -> Optional[dict]:
    """Internal use only — returns the row *with* decrypted credential.
    Used by the llm_clients factory to build provider clients."""
    row = db.fetch_one("SELECT * FROM model_clients WHERE id = %s", (client_id,))
    if not row:
        return None
    out = dict(row)
    enc = row.get("credential_encrypted")
    out["credential"] = _decrypt_credential(enc) if enc else {}
    return out


def _decrypt_credential(encrypted: str) -> dict:
    """Credential is always stored as a JSON blob (even for single-key
    providers) so the same code path handles all kinds."""
    try:
        raw = asset_crypto.decrypt(encrypted)
        if not raw:
            return {}
        return json.loads(raw)
    except Exception as e:
        log.warning("failed to decrypt model_client credential: %s", e)
        return {}


def _encrypt_credential(payload: dict | None) -> Optional[str]:
    if not payload:
        return None
    return asset_crypto.encrypt(json.dumps(payload))


# ============================================================================
# CRUD
# ============================================================================

def create(
    *,
    name: str,
    kind: str,
    description: str | None,
    config: dict,
    credential: dict | None,
    enabled: bool,
    default_for_new_users: bool,
    created_by: int,
) -> int:
    _validate_kind(kind)
    name = (name or "").strip()
    if not name:
        raise ValueError("name is required")

    row = db.fetch_one(
        """
        INSERT INTO model_clients
          (name, kind, description, config, credential_encrypted,
           enabled, default_for_new_users, created_by)
        VALUES (%s, %s, %s, %s::jsonb, %s, %s, %s, %s)
        RETURNING id
        """,
        (
            name,
            kind,
            description,
            json.dumps(config or {}),
            _encrypt_credential(credential),
            enabled,
            default_for_new_users,
            created_by,
        ),
    )
    return int(row["id"])


def update(
    client_id: int,
    *,
    name: str | None = None,
    description: str | None = None,
    config: dict | None = None,
    credential: dict | None = None,
    clear_credential: bool = False,
    enabled: bool | None = None,
    default_for_new_users: bool | None = None,
) -> None:
    """Patch-style update. Pass only the fields you want to change."""
    sets: list[str] = []
    params: list[Any] = []

    if name is not None:
        sets.append("name = %s")
        params.append(name.strip())
    if description is not None:
        sets.append("description = %s")
        params.append(description)
    if config is not None:
        sets.append("config = %s::jsonb")
        params.append(json.dumps(config))
    if clear_credential:
        sets.append("credential_encrypted = NULL")
    elif credential:
        sets.append("credential_encrypted = %s")
        params.append(_encrypt_credential(credential))
    if enabled is not None:
        sets.append("enabled = %s")
        params.append(enabled)
    if default_for_new_users is not None:
        sets.append("default_for_new_users = %s")
        params.append(default_for_new_users)

    if not sets:
        return

    sets.append("updated_at = NOW()")
    params.append(client_id)

    db.execute(
        f"UPDATE model_clients SET {', '.join(sets)} WHERE id = %s",
        tuple(params),
    )


def remove(client_id: int) -> None:
    """Delete a client. Agents referencing it will have
    `model_client_id` reset to NULL via FK ON DELETE SET NULL."""
    db.execute("DELETE FROM model_clients WHERE id = %s", (client_id,))


# ============================================================================
# Test — minimal round-trip to verify credentials + connectivity work.
# ============================================================================

def run_test(client_id: int) -> dict:
    """Fire a tiny "say OK" prompt through the client's LLM. Updates
    last_test_at / last_test_status / last_test_message on the row
    and returns the same status dict.

    Intentionally minimal: 1-2 input tokens, max_tokens=5 → roughly
    1/1000 of a cent per test on most providers. Safe to spam.
    """
    import time as _time
    row = get_raw(client_id)
    if not row:
        return {"ok": False, "message": "client not found"}
    result = {"ok": False, "message": "", "latency_ms": 0,
              "input_tokens": 0, "output_tokens": 0, "model": None}
    start = _time.time()
    try:
        from ..llm_clients import invoke_via_client
        models = (row.get("config") or {}).get("models") or []
        if not models:
            raise RuntimeError("client has no models configured")
        if not row.get("credential"):
            raise RuntimeError("client has no credential set")
        # `models` may be either a list of strings (simple shape) or a list
        # of {id, label, price_in, price_out} dicts (richer shape used by
        # the admin's Bedrock client). Extract the string id from either.
        first = models[0]
        model_id = first["id"] if isinstance(first, dict) else first
        resp = invoke_via_client(
            client_row=row,
            model_id=model_id,
            system_prompt="Respond with exactly: OK",
            messages=[{"role": "user", "content": [{"text": "ping"}]}],
            max_tokens=5,
            temperature=0.0,
        )
        if resp.get("error"):
            raise RuntimeError(resp["error"])
        result["ok"] = True
        result["model"] = model_id
        result["input_tokens"] = int(resp.get("input_tokens") or 0)
        result["output_tokens"] = int(resp.get("output_tokens") or 0)
        result["message"] = "ok"
    except Exception as e:
        import traceback
        import logging
        logging.getLogger("agent_company.model_clients").warning(
            "test failed for client %s: %s\n%s", client_id, e, traceback.format_exc(),
        )
        result["ok"] = False
        result["message"] = str(e)[:500] or type(e).__name__
    result["latency_ms"] = int((_time.time() - start) * 1000)

    db.execute(
        "UPDATE model_clients SET last_test_at = NOW(), "
        "last_test_status = %s, last_test_message = %s WHERE id = %s",
        ("ok" if result["ok"] else "fail", result["message"], client_id),
    )
    return result


# ============================================================================
# Grants
# ============================================================================

def list_grants(client_id: int) -> list[dict]:
    rows = db.fetch_all(
        """
        SELECT g.*, u.username, u.display_name
        FROM model_client_grants g
        JOIN as_users u ON u.id = g.user_id
        WHERE g.client_id = %s
        ORDER BY g.created_at
        """,
        (client_id,),
    )
    return [
        {
            "id": r["id"],
            "client_id": r["client_id"],
            "user_id": r["user_id"],
            "username": r["username"],
            "display_name": r.get("display_name"),
            "granted_by": r.get("granted_by"),
            "created_at": r.get("created_at"),
        }
        for r in rows
    ]


def grant(client_id: int, user_id: int, granted_by: int) -> None:
    db.execute(
        """
        INSERT INTO model_client_grants (client_id, user_id, granted_by)
        VALUES (%s, %s, %s)
        ON CONFLICT (client_id, user_id) DO NOTHING
        """,
        (client_id, user_id, granted_by),
    )


def revoke(client_id: int, user_id: int) -> None:
    db.execute(
        "DELETE FROM model_client_grants WHERE client_id = %s AND user_id = %s",
        (client_id, user_id),
    )


def on_user_created(user_id: int) -> None:
    """Auto-grant every `default_for_new_users = TRUE` client to a newly
    created user. Called from the user-creation route."""
    rows = db.fetch_all(
        "SELECT id FROM model_clients WHERE default_for_new_users = TRUE AND enabled = TRUE"
    )
    for row in rows:
        grant(row["id"], user_id, granted_by=user_id)


def user_can_use(client_id: int, user_id: int, *, is_admin: bool) -> bool:
    if is_admin:
        return True
    row = db.fetch_one(
        """
        SELECT 1 FROM model_clients c
        WHERE c.id = %s AND c.enabled = TRUE
          AND (
            c.default_for_new_users = TRUE
            OR EXISTS (
              SELECT 1 FROM model_client_grants g
              WHERE g.client_id = c.id AND g.user_id = %s
            )
          )
        """,
        (client_id, user_id),
    )
    return row is not None


# ============================================================================
# Dispatch: the engine uses these to actually call an LLM
# ============================================================================

def resolve_for_agent(agent_id: int) -> dict | None:
    """Return the raw model_client row (with decrypted credential) used by
    the given agent. Falls back to the first default client if the agent
    has no model_client_id set yet (legacy rows)."""
    agent = db.fetch_one(
        "SELECT id, model_client_id, primary_model_id FROM agents WHERE id = %s",
        (agent_id,),
    )
    if not agent:
        return None
    client_id = agent.get("model_client_id")
    if not client_id:
        client_id = _first_default_client_id()
    if not client_id:
        return None
    return get_raw(client_id)


def _first_default_client_id() -> int | None:
    row = db.fetch_one(
        "SELECT id FROM model_clients "
        "WHERE default_for_new_users = TRUE AND enabled = TRUE "
        "ORDER BY id LIMIT 1"
    )
    return row["id"] if row else None


# ============================================================================
# Seeds & backfill
# ============================================================================

def seed_default_client_and_backfill() -> None:
    """Ensure there's at least one Bedrock client and that every legacy agent
    has its model_client_id set to it. Idempotent — safe on every boot.

    The default client uses the env.config AWS credentials (credential JSON
    left empty means the bedrock llm_client falls back to process env)."""
    existing = db.fetch_one(
        "SELECT id FROM model_clients WHERE kind = 'bedrock' ORDER BY id LIMIT 1"
    )
    if existing:
        client_id = existing["id"]
    else:
        admin = db.fetch_one(
            "SELECT id FROM as_users WHERE role = 'admin' ORDER BY id LIMIT 1"
        )
        created_by = admin["id"] if admin else None
        default_config = {
            "region": "ap-northeast-1",
            "models": [
                {
                    "id": "jp.anthropic.claude-sonnet-4-6",
                    "label": "Claude Sonnet 4.6 (CRIS, default)",
                    "price_in": 0.003,
                    "price_out": 0.015,
                },
                {
                    "id": "global.anthropic.claude-opus-4-6-v1",
                    "label": "Claude Opus 4.6 (CRIS Global)",
                    "price_in": 0.015,
                    "price_out": 0.075,
                },
                {
                    "id": "jp.anthropic.claude-haiku-4-5-20251001-v1:0",
                    "label": "Claude Haiku 4.5 (CRIS)",
                    "price_in": 0.001,
                    "price_out": 0.005,
                },
            ],
        }
        row = db.fetch_one(
            """
            INSERT INTO model_clients
              (name, kind, description, config, credential_encrypted,
               enabled, default_for_new_users, created_by)
            VALUES (%s, %s, %s, %s::jsonb, NULL, TRUE, TRUE, %s)
            RETURNING id
            """,
            (
                "AWS Bedrock (ap-northeast-1) — default",
                "bedrock",
                "Default connection template. Fill in AWS credentials (access key / secret key) in the Connection details to activate. No credentials are pre-bundled with the app — you must enter your own.",
                json.dumps(default_config),
                created_by,
            ),
        )
        client_id = int(row["id"])
        log.info("model_clients: seeded default Bedrock client id=%s", client_id)

    # Backfill every agent with NULL model_client_id.
    db.execute(
        "UPDATE agents SET model_client_id = %s WHERE model_client_id IS NULL",
        (client_id,),
    )
    # Also migrate old friendly-key model IDs (e.g. 'claude-sonnet-4.6')
    # to actual Bedrock model IDs so the new dispatch path works. The
    # legacy bedrock_client.MODEL_REGISTRY maps old keys → real IDs.
    from ..bedrock_client import MODEL_REGISTRY
    for friendly_key, info in MODEL_REGISTRY.items():
        db.execute(
            "UPDATE agents SET primary_model_id = %s WHERE primary_model_id = %s",
            (info["model_id"], friendly_key),
        )
    # Any agent still without a model_id gets the default
    db.execute(
        """
        UPDATE agents
           SET primary_model_id = %s
         WHERE primary_model_id IS NULL OR primary_model_id = ''
        """,
        ("jp.anthropic.claude-sonnet-4-6",),
    )


# ============================================================================
# Validation
# ============================================================================

def _validate_kind(kind: str) -> None:
    if kind not in KIND_SCHEMAS:
        raise ValueError(f"unknown model client kind: {kind}")
