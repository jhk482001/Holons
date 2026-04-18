"""Symmetric encryption for asset credentials (MCP auth headers, RAG API keys).

Uses Fernet (AES-128-CBC + HMAC) with a single symmetric key loaded from
`config.ASSET_ENCRYPTION_KEY`. If the key is missing, the first call to
`encrypt()` generates a fresh one and appends it to `env.config` so it
survives a restart. A warning is logged in that case — the operator is
expected to back up the file and keep it out of version control.

Payload format stored in `asset_items.credential_encrypted`::

    <fernet-token-base64>

Decrypt with `decrypt(ciphertext)` which returns the plaintext string.
Errors bubble up as `cryptography.fernet.InvalidToken` — callers that
suspect a rotated key should catch and treat the credential as lost.
"""
from __future__ import annotations

import logging
import threading

from cryptography.fernet import Fernet

from .. import config

log = logging.getLogger("agent_company.asset_crypto")

_lock = threading.Lock()
_fernet: Fernet | None = None


def _append_key_to_env_config(key: str) -> None:
    """Persist a freshly generated key to env.config so the next restart
    picks it up. Creates the file if it doesn't exist."""
    path = config.ENV_CONFIG_PATH
    line = f"ASSET_ENCRYPTION_KEY={key}\n"
    try:
        existing = path.read_text(encoding="utf-8") if path.exists() else ""
        # Don't duplicate the key if the line is already there
        if "ASSET_ENCRYPTION_KEY=" in existing:
            return
        # Make sure the file ends with a newline before we append
        if existing and not existing.endswith("\n"):
            existing += "\n"
        path.write_text(existing + line, encoding="utf-8")
        path.chmod(0o600)
    except OSError as exc:
        log.warning("failed to persist ASSET_ENCRYPTION_KEY to env.config: %s", exc)


def _get_fernet() -> Fernet:
    global _fernet
    with _lock:
        if _fernet is not None:
            return _fernet
        key = (config.ASSET_ENCRYPTION_KEY or "").strip()
        if not key:
            # First boot with no configured key — generate one, persist it,
            # and loudly log what happened so the operator can rotate later.
            generated = Fernet.generate_key().decode("utf-8")
            _append_key_to_env_config(generated)
            config.ASSET_ENCRYPTION_KEY = generated  # update in-memory cfg
            log.warning(
                "ASSET_ENCRYPTION_KEY was missing; generated a new one and wrote "
                "it to %s. Back that file up — losing the key permanently "
                "destroys all stored MCP / RAG credentials.",
                config.ENV_CONFIG_PATH,
            )
            key = generated
        _fernet = Fernet(key.encode("utf-8"))
        return _fernet


def encrypt(plaintext: str | None) -> str | None:
    """Encrypt a plaintext string. Returns None for None/empty input so
    callers can pass-through 'no credential' without special-casing."""
    if plaintext is None or plaintext == "":
        return None
    return _get_fernet().encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt(ciphertext: str | None) -> str | None:
    """Decrypt a ciphertext string. Returns None for None/empty input."""
    if ciphertext is None or ciphertext == "":
        return None
    return _get_fernet().decrypt(ciphertext.encode("utf-8")).decode("utf-8")


# Test helper: reset the cached Fernet instance so a test that mutates
# config.ASSET_ENCRYPTION_KEY picks up the new value on the next call.
def _reset_for_tests() -> None:
    global _fernet
    with _lock:
        _fernet = None
