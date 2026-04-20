"""Slack adapter — webhook-only (Slack's Events API pushes; no polling).

Auth: Slack bot token (xoxb-...). The webhook endpoint's URL-path
secret is our own authorisation; Slack's HMAC signing_secret is NOT
verified in this MVP — production deployments should add it. The
URL-path secret is sufficient as long as it isn't leaked.

What Slack pushes:
- url_verification on initial setup — must echo `challenge` back
- event_callback with event.type == "message" — what we dispatch

Sends use chat.postMessage.
"""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from typing import Iterable

from .base import BasePlatformAdapter, InboundMessage

log = logging.getLogger("agent_company.im.slack")

API_ROOT = "https://slack.com/api"


class SlackAdapter(BasePlatformAdapter):
    platform = "slack"

    # --------------------------------------------------------------
    def _call(self, method: str, payload: dict) -> dict:
        if not self.secret:
            raise RuntimeError("slack binding has no bot token")
        url = f"{API_ROOT}/{method}"
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url, data=body,
            headers={
                "Content-Type": "application/json; charset=utf-8",
                "Authorization": f"Bearer {self.secret}",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            msg = json.loads(r.read().decode("utf-8"))
        if not msg.get("ok"):
            raise RuntimeError(f"slack API error: {msg.get('error')}")
        return msg

    # --------------------------------------------------------------
    def poll_once(self) -> Iterable[InboundMessage]:
        # Slack doesn't support polling for bot events. Always empty.
        return []

    def parse_update(self, payload: dict) -> InboundMessage | None:
        """Parse one Slack event envelope. Returns None for bot-
        originated or non-message events so we don't talk to ourselves."""
        event = payload.get("event") or {}
        if event.get("type") != "message":
            return None
        # Skip bot's own replies (bot_id present) and message edits
        if event.get("bot_id") or event.get("subtype"):
            return None
        text = (event.get("text") or "").strip()
        if not text:
            return None
        channel = event.get("channel")
        user = event.get("user") or "unknown"
        return InboundMessage(
            platform=self.platform,
            external_id=str(channel),
            sender_display=str(user),
            text=text,
            raw=event,
        )

    def send(self, external_id: str, text: str) -> None:
        try:
            self._call("chat.postMessage", {
                "channel": external_id,
                "text": text,
                "mrkdwn": True,
            })
        except Exception as e:
            log.warning("chat.postMessage failed for channel %s: %s",
                        external_id, e)

    def send_typing(self, external_id: str) -> None:
        # Slack has no first-class typing indicator for bots.
        pass


def verify_token(token: str) -> dict:
    """Call auth.test with the bot token. Returns {ok, team, user, ...}
    on success, raises ValueError on failure."""
    req = urllib.request.Request(
        f"{API_ROOT}/auth.test",
        headers={"Authorization": f"Bearer {token}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            msg = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise ValueError(f"HTTP {e.code} from Slack: {e.reason}") from e
    if not msg.get("ok"):
        raise ValueError(msg.get("error") or "token rejected by slack")
    return msg
