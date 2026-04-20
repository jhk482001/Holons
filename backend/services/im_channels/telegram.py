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
    def parse_update(self, update: dict) -> InboundMessage | None:
        """Parse one Telegram Update into an InboundMessage. Returns
        None for non-text updates. Shared by poll_once() and the
        webhook receiver."""
        m = update.get("message")
        if not m:
            return None
        chat = m.get("chat") or {}
        text = (m.get("text") or "").strip()
        if not text:
            return None
        sender = chat.get("first_name") or chat.get("username") or f"chat:{chat.get('id')}"
        return InboundMessage(
            platform=self.platform,
            external_id=str(chat.get("id")),
            sender_display=sender,
            text=text,
            raw=m,
        )

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
            parsed = self.parse_update(u)
            if parsed:
                out.append(parsed)
        return out

    # ------------------------------------------------------------------
    def set_webhook(self, public_url: str) -> None:
        """Register `public_url` as Telegram's callback for this bot.
        Telegram will POST every Update there. `public_url` must be
        https:// and reachable from the public internet."""
        self._call("setWebhook", {
            "url": public_url,
            "allowed_updates": ["message"],
            "drop_pending_updates": False,
        })

    def delete_webhook(self) -> None:
        """Revert to polling mode — tell Telegram to stop pushing."""
        self._call("deleteWebhook", {"drop_pending_updates": False})

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

    # ------------------------------------------------------------------
    def _multipart(self, fields: dict, file_field: str, filename: str,
                   data: bytes, mime: str) -> tuple[bytes, str]:
        """Build a multipart/form-data body with one file part. Pure
        stdlib — no requests / urllib3 dep."""
        import uuid
        import io
        boundary = f"holons-{uuid.uuid4().hex}"
        body = io.BytesIO()
        for k, v in fields.items():
            body.write(f"--{boundary}\r\n".encode())
            body.write(f'Content-Disposition: form-data; name="{k}"\r\n\r\n'.encode())
            body.write(str(v).encode("utf-8"))
            body.write(b"\r\n")
        body.write(f"--{boundary}\r\n".encode())
        body.write(
            f'Content-Disposition: form-data; name="{file_field}"; '
            f'filename="{filename}"\r\n'.encode()
        )
        body.write(f"Content-Type: {mime}\r\n\r\n".encode())
        body.write(data)
        body.write(f"\r\n--{boundary}--\r\n".encode())
        return body.getvalue(), f"multipart/form-data; boundary={boundary}"

    def _upload(self, method: str, fields: dict, file_field: str,
                filename: str, data: bytes, mime: str) -> None:
        if not self.secret:
            raise RuntimeError("telegram binding has no bot token")
        url = f"{API_ROOT}/bot{self.secret}/{method}"
        body, ctype = self._multipart(fields, file_field, filename, data, mime)
        req = urllib.request.Request(
            url, data=body,
            headers={"Content-Type": ctype, "Accept": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=60) as r:
            msg = json.loads(r.read().decode("utf-8"))
        if not msg.get("ok"):
            raise RuntimeError(f"telegram API error: {msg.get('description')}")

    def send_artifact(self, external_id: str, artifact: dict) -> bool:
        """Rich delivery for html / slides / markdown / file artifacts.
        Uses sendDocument (up to 50 MB) for everything. Short markdown
        stays as a normal send() text message — inline is friendlier
        than forcing a file download."""
        kind = artifact.get("kind")
        title = (artifact.get("title") or artifact.get("filename")
                 or "artifact").strip()

        def _safe_name(name: str, ext: str) -> str:
            cleaned = "".join(c if c.isalnum() or c in "-_" else "_"
                              for c in name)[:80]
            cleaned = cleaned.strip("_") or "artifact"
            return f"{cleaned}.{ext}"

        try:
            if kind == "html":
                content = (artifact.get("html") or "").encode("utf-8")
                fname = _safe_name(title, "html")
                self._upload("sendDocument",
                             {"chat_id": external_id, "caption": f"📄 {title}"},
                             "document", fname, content, "text/html")
                return True
            if kind == "slides":
                content = (artifact.get("html") or "").encode("utf-8")
                fname = _safe_name(title, "html")
                self._upload("sendDocument",
                             {"chat_id": external_id, "caption": f"🎞 {title}"},
                             "document", fname, content, "text/html")
                return True
            if kind == "markdown":
                md = artifact.get("markdown") or ""
                # Small markdown → just send inline. The pill that follows
                # in the caller's text strip-then-send has already gone.
                if len(md) < 3500:
                    self.send(external_id, f"*{title}*\n\n{md}")
                    return True
                content = md.encode("utf-8")
                fname = _safe_name(title, "md")
                self._upload("sendDocument",
                             {"chat_id": external_id, "caption": f"📝 {title}"},
                             "document", fname, content, "text/markdown")
                return True
            if kind == "file":
                filename = artifact.get("filename") or _safe_name(title, "bin")
                mime = artifact.get("mime") or "application/octet-stream"
                content_raw = artifact.get("content") or ""
                if artifact.get("encoding") == "base64":
                    import base64
                    content = base64.b64decode(content_raw)
                else:
                    content = content_raw.encode("utf-8")
                method = "sendPhoto" if mime.startswith("image/") else "sendDocument"
                field = "photo" if method == "sendPhoto" else "document"
                self._upload(method,
                             {"chat_id": external_id, "caption": f"📎 {filename}"},
                             field, filename, content, mime)
                return True
        except Exception as e:
            log.warning("send_artifact(%s) failed: %s", kind, e)
            return False
        return False

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
