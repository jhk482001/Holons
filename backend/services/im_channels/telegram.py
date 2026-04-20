"""Telegram adapter — bot token + long polling via stdlib urllib.

Deliberately zero external deps: the Bot API is plain HTTPS + JSON. The
adapter tracks the last seen `update_id` per binding so restarts don't
replay history, and uses Telegram's own long-poll timeout so the thread
stays idle most of the time instead of hot-looping.

One binding = one bot = one user. Each Holons user creates their own bot
via @BotFather and pastes the token into Settings.
"""
from __future__ import annotations

import json
import logging
import urllib.parse
import urllib.request
from typing import Iterable

from .base import BasePlatformAdapter, InboundMessage

log = logging.getLogger("agent_company.im.telegram")

API_ROOT = "https://api.telegram.org"
LONG_POLL_TIMEOUT = 25  # seconds
HTTP_TIMEOUT = LONG_POLL_TIMEOUT + 10  # a bit longer than TG's own timeout


class TelegramAdapter(BasePlatformAdapter):
    platform = "telegram"

    def __init__(self, binding: dict):
        super().__init__(binding)
        meta = binding.get("metadata") or {}
        self._last_update_id = int(meta.get("last_update_id") or 0)

    # ------------------------------------------------------------------
    def _call(self, method: str, params: dict | None = None,
              timeout: float = HTTP_TIMEOUT) -> dict:
        """POST to /bot<token>/<method>. Returns the `result` field.
        Raises on network or API failure."""
        if not self.secret:
            raise RuntimeError("telegram binding has no bot token")
        url = f"{API_ROOT}/bot{self.secret}/{method}"
        body = json.dumps(params or {}).encode("utf-8")
        req = urllib.request.Request(
            url, data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
        msg = json.loads(raw.decode("utf-8"))
        if not msg.get("ok"):
            raise RuntimeError(f"telegram API error: {msg.get('description')}")
        return msg.get("result")

    # ------------------------------------------------------------------
    def poll_once(self) -> Iterable[InboundMessage]:
        """Fetch updates via long-polling. Advances the local cursor in
        memory; the manager persists it back to `im_bindings.metadata`
        after a batch is processed."""
        try:
            updates = self._call("getUpdates", {
                "offset": self._last_update_id + 1,
                "timeout": LONG_POLL_TIMEOUT,
                "allowed_updates": ["message"],
            })
        except Exception as e:
            log.warning("getUpdates failed for user %s: %s", self.user_id, e)
            return []

        out: list[InboundMessage] = []
        for u in updates or []:
            self._last_update_id = max(self._last_update_id, int(u["update_id"]))
            m = u.get("message")
            if not m:
                continue
            chat = m.get("chat") or {}
            text = (m.get("text") or "").strip()
            if not text:
                continue  # skip stickers/photos/etc for now
            sender = chat.get("first_name") or chat.get("username") or f"chat:{chat.get('id')}"
            out.append(InboundMessage(
                platform=self.platform,
                external_id=str(chat.get("id")),
                sender_display=sender,
                text=text,
                raw=m,
            ))
        return out

    # ------------------------------------------------------------------
    def send(self, external_id: str, text: str) -> None:
        # Telegram hard-limit is 4096 chars per message. Split if needed.
        MAX = 4000
        chunks = [text[i:i + MAX] for i in range(0, len(text), MAX)] or [""]
        for chunk in chunks:
            try:
                self._call("sendMessage", {
                    "chat_id": external_id,
                    "text": chunk,
                    "parse_mode": "Markdown",
                    "disable_web_page_preview": True,
                })
            except Exception as e:
                log.warning("sendMessage failed for chat %s: %s", external_id, e)
                # Retry without Markdown in case a stray asterisk broke parsing
                try:
                    self._call("sendMessage", {"chat_id": external_id, "text": chunk})
                except Exception as e2:
                    log.error("sendMessage retry failed: %s", e2)
                    return

    def send_typing(self, external_id: str) -> None:
        try:
            self._call("sendChatAction", {"chat_id": external_id, "action": "typing"},
                       timeout=5)
        except Exception:
            pass  # typing is purely decorative

    @property
    def last_update_id(self) -> int:
        return self._last_update_id


def verify_token(token: str) -> dict:
    """Call `getMe` with the supplied token to validate it. Returns the
    bot info dict on success, raises on failure. Used by the API layer
    when a user saves a new token."""
    url = f"{API_ROOT}/bot{token}/getMe"
    req = urllib.request.Request(url, method="POST")
    with urllib.request.urlopen(req, timeout=10) as r:
        msg = json.loads(r.read().decode("utf-8"))
    if not msg.get("ok"):
        raise ValueError(msg.get("description") or "invalid token")
    return msg["result"]
