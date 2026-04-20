"""LINE Messaging API adapter — webhook-only.

Auth: channel access token (long-lived). Stored encrypted in
`secret_encrypted`. LINE channel_secret for HMAC verification could be
stored in metadata for production; MVP relies on the URL-path secret.

What LINE pushes: `events[]`, each with `type == "message"` and
`message.type == "text"` for what we care about. Sends use the push
message endpoint keyed on `userId`.
"""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from typing import Iterable

from .base import BasePlatformAdapter, InboundMessage

log = logging.getLogger("agent_company.im.line")

API_ROOT = "https://api.line.me/v2/bot"


class LineAdapter(BasePlatformAdapter):
    platform = "line"

    # --------------------------------------------------------------
    def _call(self, path: str, payload: dict | None = None,
              method: str = "POST") -> dict:
        if not self.secret:
            raise RuntimeError("line binding has no channel access token")
        url = f"{API_ROOT}{path}"
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(
            url, data=data,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.secret}",
            },
            method=method,
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            raw = r.read()
        if not raw:
            return {}
        try:
            return json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            return {}

    # --------------------------------------------------------------
    def poll_once(self) -> Iterable[InboundMessage]:
        return []  # webhook only

    def parse_update(self, payload: dict) -> list[InboundMessage]:
        """LINE bundles multiple events in one webhook POST. Returns
        zero-to-many InboundMessage objects."""
        out: list[InboundMessage] = []
        for ev in payload.get("events") or []:
            if ev.get("type") != "message":
                continue
            msg = ev.get("message") or {}
            if msg.get("type") != "text":
                continue
            text = (msg.get("text") or "").strip()
            if not text:
                continue
            src = ev.get("source") or {}
            user_id = src.get("userId") or src.get("groupId") or src.get("roomId")
            if not user_id:
                continue
            out.append(InboundMessage(
                platform=self.platform,
                external_id=str(user_id),
                sender_display=str(user_id)[:8],
                text=text,
                raw=ev,
            ))
        return out

    def send(self, external_id: str, text: str) -> None:
        # LINE enforces 5000-char per message; split just in case.
        MAX = 4800
        chunks = [text[i:i + MAX] for i in range(0, len(text), MAX)] or [""]
        for chunk in chunks:
            try:
                self._call("/message/push", {
                    "to": external_id,
                    "messages": [{"type": "text", "text": chunk}],
                })
            except Exception as e:
                log.warning("line push failed for user %s: %s", external_id, e)
                return

    def send_typing(self, external_id: str) -> None:
        # LINE has loading animation but only during reply (not push).
        pass


def verify_token(token: str) -> dict:
    """Hit /info with the channel access token. Returns basic bot info
    on success, raises ValueError on failure."""
    req = urllib.request.Request(
        f"{API_ROOT}/info",
        headers={"Authorization": f"Bearer {token}"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace") if e.fp else ""
        raise ValueError(f"HTTP {e.code} from LINE: {body[:200]}") from e
