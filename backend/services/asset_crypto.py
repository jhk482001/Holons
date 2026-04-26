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
import pathlib
import threading

from cryptography.fernet import Fernet

from .. import config

log = logging.getLogger("agent_company.asset_crypto")

_lock = threading.Lock()
_fernet: Fernet | None = None

# Persistent key location for desktop / personal mode. PyInstaller's
# `_MEIPASS` directory (where `config.ENV_CONFIG_PATH` resolves at
# runtime) is a per-launch tempdir that disappears when the process
# exits, so a key written there is gone next launch — and any
# credential encrypted with it becomes undecryptable.
# `~/.agent_company/` is created on first launch and outlives the
# binary, so it's the right home for the desktop-mode key.
_PERSISTENT_KEY_PATH = pathlib.Path.home() / ".agent_company" / ".encryption-key"


def _read_persistent_key() -> str | None:
    try:
        if _PERSISTENT_KEY_PATH.exists():
            txt = _PERSISTENT_KEY_PATH.read_text(encoding="utf-8").strip()
            return txt or None
    except OSError as exc:
        log.warning("failed to read %s: %s", _PERSISTENT_KEY_PATH, exc)
    return None


def _write_persistent_key(key: str) -> bool:
    try:
        _PERSISTENT_KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
        _PERSISTENT_KEY_PATH.write_text(key, encoding="utf-8")
        _PERSISTENT_KEY_PATH.chmod(0o600)
        return True
    except OSError as exc:
        log.warning("failed to write %s: %s", _PERSISTENT_KEY_PATH, exc)
        return False


def _append_key_to_env_config(key: str) -> bool:
    """Persist a freshly generated key to env.config so the next restart
    picks it up. Creates the file if it doesn't exist. Returns True if
    successful — caller falls back to the persistent home-dir key if not.
    """
    path = config.ENV_CONFIG_PATH
    line = f"ASSET_ENCRYPTION_KEY={key}\n"
    try:
        existing = path.read_text(encoding="utf-8") if path.exists() else ""
        # Don't duplicate the key if the line is already there
        if "ASSET_ENCRYPTION_KEY=" in existing:
            return True
        # Make sure the file ends with a newline before we append
        if existing and not existing.endswith("\n"):
            existing += "\n"
        path.write_text(existing + line, encoding="utf-8")
        path.chmod(0o600)
        return True
    except OSError as exc:
        log.warning("failed to persist ASSET_ENCRYPTION_KEY to env.config: %s", exc)
        return False


def _get_fernet() -> Fernet:
    global _fernet
    with _lock:
        if _fernet is not None:
            return _fernet
        key = (config.ASSET_ENCRYPTION_KEY or "").strip()
        if not key:
            # Second-chance lookup: did a previous launch persist a key
            # to ~/.agent_company/.encryption-key? PyInstaller bundles
            # can't write env.config persistently, so this is the only
            # location that survives a relaunch in desktop mode.
            persistent = _read_persistent_key()
            if persistent:
                key = persistent
                config.ASSET_ENCRYPTION_KEY = key
        if not key:
            # First boot with no configured key anywhere — generate one,
            # try env.config first (dev mode), then fall back to the
            # persistent home-dir file (desktop mode), so subsequent
            # launches can decrypt credentials they wrote.
            generated = Fernet.generate_key().decode("utf-8")
            wrote_env = _append_key_to_env_config(generated)
            if not wrote_env:
                _write_persistent_key(generated)
            else:
                # Mirror to the persistent location anyway so a later
                # repackage doesn't lose access (env.config may end up
                # inside a frozen archive).
                _write_persistent_key(generated)
            config.ASSET_ENCRYPTION_KEY = generated  # update in-memory cfg
            log.warning(
                "ASSET_ENCRYPTION_KEY was missing; generated a new one. "
                "Wrote env.config=%s, persistent=%s. Back the persistent "
                "file up — losing the key permanently destroys all stored "
                "MCP / RAG / model_client credentials.",
                wrote_env, _PERSISTENT_KEY_PATH,
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
