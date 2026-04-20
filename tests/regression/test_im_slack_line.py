"""Slack + LINE adapter unit tests.

Both are HTTP-only (no polling), so we don't need to exercise poll_once.
We verify: parse_update turns a real-looking platform envelope into an
InboundMessage, and send hits the right URL with the right body.
"""
from __future__ import annotations

import json
import os
import sys
from unittest.mock import patch, MagicMock

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, REPO)

from backend.services.im_channels import slack as sl  # noqa: E402
from backend.services.im_channels import line as ln   # noqa: E402


def _fake_http(payload: dict):
    body = json.dumps(payload).encode("utf-8")
    resp = MagicMock()
    resp.__enter__ = lambda self: self
    resp.__exit__ = lambda *a: False
    resp.read = MagicMock(return_value=body)
    return resp


# ============================================================================
# Slack
# ============================================================================

def _slack():
    return sl.SlackAdapter({
        "id": 1, "user_id": 99, "platform": "slack",
        "external_id": None, "secret_encrypted": None, "metadata": {},
        "secret": "xoxb-fake",
    })


def test_slack_parse_event_callback():
    adapter = _slack()
    payload = {
        "type": "event_callback",
        "event": {
            "type": "message",
            "channel": "C123",
            "user": "U456",
            "text": "hi lead",
        },
    }
    msg = adapter.parse_update(payload)
    assert msg is not None
    assert msg.platform == "slack"
    assert msg.external_id == "C123"
    assert msg.text == "hi lead"


def test_slack_skips_bot_message_to_avoid_loop():
    adapter = _slack()
    payload = {
        "type": "event_callback",
        "event": {"type": "message", "channel": "C1", "user": "U1",
                  "text": "echo", "bot_id": "B999"},
    }
    assert adapter.parse_update(payload) is None


def test_slack_skips_message_edits():
    adapter = _slack()
    payload = {
        "type": "event_callback",
        "event": {"type": "message", "channel": "C1", "user": "U1",
                  "text": "edit", "subtype": "message_changed"},
    }
    assert adapter.parse_update(payload) is None


def test_slack_send_calls_chat_postmessage():
    adapter = _slack()
    captured = {}

    def _capture(req, timeout=None):
        captured["url"] = req.full_url
        captured["body"] = json.loads(req.data.decode("utf-8"))
        captured["auth"] = req.headers.get("Authorization")
        return _fake_http({"ok": True})

    with patch("urllib.request.urlopen", side_effect=_capture):
        adapter.send("C999", "hello")
    assert captured["url"].endswith("/chat.postMessage")
    assert captured["body"] == {"channel": "C999", "text": "hello", "mrkdwn": True}
    assert captured["auth"] == "Bearer xoxb-fake"


def test_slack_verify_token_ok():
    with patch("urllib.request.urlopen",
               return_value=_fake_http({"ok": True, "team": "Acme", "user": "bot_user"})):
        info = sl.verify_token("xoxb-test")
    assert info["team"] == "Acme"


def test_slack_verify_token_rejects_error():
    import pytest
    with patch("urllib.request.urlopen",
               return_value=_fake_http({"ok": False, "error": "invalid_auth"})):
        with pytest.raises(ValueError) as exc:
            sl.verify_token("xoxb-bad")
    assert "invalid_auth" in str(exc.value)


# ============================================================================
# LINE
# ============================================================================

def _line():
    return ln.LineAdapter({
        "id": 2, "user_id": 99, "platform": "line",
        "external_id": None, "secret_encrypted": None, "metadata": {},
        "secret": "line-channel-token",
    })


def test_line_parse_multiple_events_in_one_webhook():
    adapter = _line()
    payload = {
        "destination": "Uxxx",
        "events": [
            {"type": "message", "source": {"userId": "U1", "type": "user"},
             "message": {"type": "text", "text": "first"}},
            {"type": "message", "source": {"userId": "U2", "type": "user"},
             "message": {"type": "text", "text": "second"}},
            # Non-text message should be skipped.
            {"type": "message", "source": {"userId": "U3", "type": "user"},
             "message": {"type": "image", "id": "img1"}},
            # Non-message event skipped too.
            {"type": "follow", "source": {"userId": "U4", "type": "user"}},
        ],
    }
    msgs = adapter.parse_update(payload)
    assert len(msgs) == 2
    assert msgs[0].external_id == "U1"
    assert msgs[0].text == "first"
    assert msgs[1].external_id == "U2"
    assert msgs[1].text == "second"


def test_line_send_uses_push_endpoint():
    adapter = _line()
    captured = {}

    def _capture(req, timeout=None):
        captured["url"] = req.full_url
        captured["body"] = json.loads(req.data.decode("utf-8"))
        captured["auth"] = req.headers.get("Authorization")
        return _fake_http({})

    with patch("urllib.request.urlopen", side_effect=_capture):
        adapter.send("Uabc", "hello from holons")
    assert captured["url"].endswith("/v2/bot/message/push")
    assert captured["body"]["to"] == "Uabc"
    assert captured["body"]["messages"][0]["text"] == "hello from holons"
    assert captured["auth"] == "Bearer line-channel-token"


def test_line_verify_token_rejects_http_error():
    import pytest
    import urllib.error
    err = urllib.error.HTTPError("u", 401, "Unauthorized", None,
                                  MagicMock(read=lambda: b"{}"))
    with patch("urllib.request.urlopen", side_effect=err):
        with pytest.raises(ValueError) as exc:
            ln.verify_token("bad")
    assert "401" in str(exc.value)
