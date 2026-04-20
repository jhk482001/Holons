"""Lifecycle for IM channel pollers.

On backend startup, spin up one thread per enabled `im_bindings` row.
Each thread sits in a loop: `poll_once` → for each message,
`router.dispatch` → if there's a reply, `send` it back. The last
`update_id` is persisted into `im_bindings.metadata` periodically so
restarts don't replay history.

`reload_user(uid)` stops and restarts any threads owned by that user —
called by the API layer after a binding is created, updated, or
deleted.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from typing import Dict

from ... import db
from ..asset_crypto import decrypt
from .base import BasePlatformAdapter
from . import router, telegram as telegram_mod

log = logging.getLogger("agent_company.im.manager")

# Per-binding-id: the thread + its stop event. Access guarded by _lock.
_workers: Dict[int, "_Worker"] = {}
_lock = threading.Lock()

POLL_ERROR_BACKOFF = 10  # seconds after a poll failure before retrying


def _adapter_for(binding: dict) -> BasePlatformAdapter | None:
    """Hydrate a binding row into a concrete adapter. Returns None for
    unknown platforms (they get skipped)."""
    binding = dict(binding)  # mutate-safe copy
    binding["secret"] = decrypt(binding.get("secret_encrypted"))
    platform = binding["platform"]
    if platform == "telegram":
        return telegram_mod.TelegramAdapter(binding)
    log.warning("unknown IM platform %r — skipping binding #%s",
                platform, binding.get("id"))
    return None


class _Worker(threading.Thread):
    """Polls one IM binding and routes messages through the Lead."""

    def __init__(self, binding_id: int, adapter: BasePlatformAdapter):
        super().__init__(daemon=True, name=f"im-{adapter.platform}-{binding_id}")
        self.binding_id = binding_id
        self.adapter = adapter
        self.stop_event = threading.Event()

    def run(self):
        log.info("worker started for binding #%s (%s, user %s)",
                 self.binding_id, self.adapter.platform, self.adapter.user_id)
        while not self.stop_event.is_set():
            try:
                messages = list(self.adapter.poll_once())
            except Exception as e:
                log.warning("poll failed for binding #%s: %s", self.binding_id, e)
                self.stop_event.wait(POLL_ERROR_BACKOFF)
                continue

            for m in messages:
                if self.stop_event.is_set():
                    break
                try:
                    # Show a typing indicator while Lead thinks — nice UX.
                    self.adapter.send_typing(m.external_id)
                    reply = router.dispatch(m, self.adapter.user_id)
                    if reply:
                        self.adapter.send(m.external_id, reply)
                except Exception:
                    log.exception("dispatch failed for binding #%s", self.binding_id)

            # Persist the polling cursor so a restart doesn't replay.
            self._persist_cursor()

        try:
            self.adapter.close()
        except Exception:
            pass
        log.info("worker stopped for binding #%s", self.binding_id)

    def _persist_cursor(self):
        """Save adapter-specific state back to the binding's metadata.
        For Telegram, that's the last_update_id."""
        meta_update: dict = {}
        last = getattr(self.adapter, "last_update_id", None)
        if last is not None:
            meta_update["last_update_id"] = int(last)
        if not meta_update:
            return
        row = db.fetch_one(
            "SELECT metadata FROM im_bindings WHERE id = %s", (self.binding_id,),
        )
        current = (row or {}).get("metadata") or {}
        if {k: current.get(k) for k in meta_update} == meta_update:
            return  # nothing changed
        merged = {**current, **meta_update}
        db.execute(
            "UPDATE im_bindings SET metadata = %s::jsonb WHERE id = %s",
            (json.dumps(merged), self.binding_id),
        )


# ============================================================================
# Public API
# ============================================================================

def start_all() -> int:
    """Called at backend startup. Spin up a polling worker per enabled
    `transport='polling'` binding. Webhook bindings don't need a thread
    — they receive updates at the /api/im/webhook/<platform>/<secret>
    endpoint instead."""
    rows = db.fetch_all(
        "SELECT id, user_id, platform, external_id, secret_encrypted, "
        "       metadata, transport "
        "FROM im_bindings WHERE enabled = TRUE AND transport = 'polling'",
    )
    n = 0
    with _lock:
        for r in rows:
            if r["id"] in _workers:
                continue
            adapter = _adapter_for(r)
            if not adapter:
                continue
            w = _Worker(r["id"], adapter)
            w.start()
            _workers[r["id"]] = w
            n += 1
    log.info("IM manager started %d polling worker(s)", n)
    return n


def stop_all(timeout: float = 5) -> None:
    with _lock:
        workers = list(_workers.values())
        _workers.clear()
    for w in workers:
        w.stop_event.set()
    for w in workers:
        w.join(timeout=timeout)


def reload_user(user_id: int) -> int:
    """Stop any running workers owned by this user and (re-)start from
    current DB state. Returns the number of workers running after."""
    with _lock:
        owned = [bid for bid, w in _workers.items()
                 if w.adapter.user_id == user_id]
        stopped_workers = []
        for bid in owned:
            w = _workers.pop(bid, None)
            if w:
                w.stop_event.set()
                stopped_workers.append(w)
    for w in stopped_workers:
        w.join(timeout=3)

    rows = db.fetch_all(
        "SELECT id, user_id, platform, external_id, secret_encrypted, "
        "       metadata, transport "
        "FROM im_bindings WHERE user_id = %s AND enabled = TRUE "
        "  AND transport = 'polling'",
        (user_id,),
    )
    started = 0
    with _lock:
        for r in rows:
            adapter = _adapter_for(r)
            if not adapter:
                continue
            w = _Worker(r["id"], adapter)
            w.start()
            _workers[r["id"]] = w
            started += 1
    return started
