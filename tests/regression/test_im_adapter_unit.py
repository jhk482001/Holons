"""Unit tests for the Telegram adapter + router — no network, no real
bot token. Mocks urllib for the HTTP side and the Lead chat service for
the router side.

These live in tests/regression because they share the project's pytest
setup, but they don't need the backend HTTP server (unlike the other
files in this directory).
"""
from __future__ import annotations

import io
import json
import os
import sys
from unittest.mock import patch, MagicMock

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, REPO)

from backend.services.im_channels import telegram as tg  # noqa: E402
from backend.services.im_channels.base import InboundMessage  # noqa: E402
from backend.services.im_channels import router  # noqa: E402


# ============================================================================
# Telegram adapter: HTTP-layer behavior
# ============================================================================

def _fake_http(payload: dict):
    """Build a context-manager-compatible fake urlopen response."""
    body = json.dumps(payload).encode("utf-8")
    resp = MagicMock()
    resp.__enter__ = lambda self: self
    resp.__exit__ = lambda *a: False
    resp.read = MagicMock(return_value=body)
    return resp


def test_verify_token_accepts_ok_response():
    payload = {"ok": True, "result": {"id": 123, "username": "bot_x", "first_name": "Bot X"}}
    with patch("urllib.request.urlopen", return_value=_fake_http(payload)):
        info = tg.verify_token("fake-token")
    assert info["username"] == "bot_x"


def test_verify_token_rejects_api_error():
    payload = {"ok": False, "description": "Unauthorized"}
    with patch("urllib.request.urlopen", return_value=_fake_http(payload)):
        with pytest.raises(ValueError) as exc:
            tg.verify_token("fake-token")
    assert "Unauthorized" in str(exc.value)


def test_poll_once_parses_update_and_advances_cursor():
    adapter = tg.TelegramAdapter({
        "id": 1, "user_id": 99, "platform": "telegram",
        "external_id": None, "secret_encrypted": None, "metadata": {},
        "secret": "fake-token",
    })
    updates_payload = {
        "ok": True,
        "result": [{
            "update_id": 101,
            "message": {
                "message_id": 42,
                "chat": {"id": 555, "first_name": "Alice", "username": "alice"},
                "text": "hello",
            },
        }],
    }
    with patch("urllib.request.urlopen", return_value=_fake_http(updates_payload)):
        msgs = list(adapter.poll_once())
    assert len(msgs) == 1
    m = msgs[0]
    assert isinstance(m, InboundMessage)
    assert m.platform == "telegram"
    assert m.external_id == "555"
    assert m.text == "hello"
    assert m.sender_display == "Alice"
    assert adapter.last_update_id == 101  # cursor advanced


def test_poll_once_skips_non_text_messages():
    adapter = tg.TelegramAdapter({
        "id": 1, "user_id": 99, "platform": "telegram",
        "external_id": None, "secret_encrypted": None, "metadata": {},
        "secret": "fake-token",
    })
    # A photo-only message has no .text — should be ignored.
    payload = {"ok": True, "result": [{
        "update_id": 1,
        "message": {"message_id": 1, "chat": {"id": 1}, "photo": [{"file_id": "x"}]},
    }]}
    with patch("urllib.request.urlopen", return_value=_fake_http(payload)):
        assert list(adapter.poll_once()) == []


def test_poll_once_returns_empty_on_network_error():
    adapter = tg.TelegramAdapter({
        "id": 1, "user_id": 99, "platform": "telegram",
        "external_id": None, "secret_encrypted": None, "metadata": {},
        "secret": "fake-token",
    })
    with patch("urllib.request.urlopen", side_effect=OSError("boom")):
        assert list(adapter.poll_once()) == []


def test_send_splits_messages_longer_than_4000_chars():
    adapter = tg.TelegramAdapter({
        "id": 1, "user_id": 99, "platform": "telegram",
        "external_id": "555", "secret_encrypted": None, "metadata": {},
        "secret": "fake-token",
    })
    calls = []

    def _capture(req, timeout=None):
        # Record the POST body so we can assert chunks
        calls.append(json.loads(req.data.decode("utf-8")))
        return _fake_http({"ok": True, "result": {}})

    huge = "x" * 9000
    with patch("urllib.request.urlopen", side_effect=_capture):
        adapter.send("555", huge)
    # 9000-char string + 4000-char cap = 3 chunks
    assert len(calls) == 3
    for c in calls:
        assert len(c["text"]) <= 4000
        assert c["chat_id"] == "555"


# ============================================================================
# Router: command dispatch
# ============================================================================

def _inbound(text, external_id="555"):
    return InboundMessage(
        platform="telegram", external_id=external_id,
        sender_display="Alice", text=text, raw={},
    )


def test_help_command_returns_help_text():
    reply = router.dispatch(_inbound("/help"), user_id=1)
    assert reply is not None
    assert "/start" in reply and "/help" in reply


def test_start_command_persists_external_id():
    """Exercises the DB path. Verifies that /start calls UPDATE on the
    binding so subsequent sends know where to push replies."""
    with patch.object(router.db, "execute") as mock_execute, \
         patch.object(router.db, "fetch_one", return_value={"display_name": "Q"}):
        reply = router.dispatch(_inbound("/start", external_id="chat-42"), user_id=7)
    assert "Q" in reply  # greets by display name
    # Confirm we issued an UPDATE with the external_id + user + platform
    mock_execute.assert_called_once()
    args = mock_execute.call_args[0]
    assert "UPDATE im_bindings" in args[0]
    assert args[1] == ("chat-42", 7, "telegram")


def test_runs_command_formats_markdown_table():
    rows = [
        {"id": 1, "status": "done", "total_cost_usd": 0.0123, "name": "Hourly mail triage"},
        {"id": 2, "status": "error", "total_cost_usd": 0, "name": None},
    ]
    with patch.object(router.db, "fetch_all", return_value=rows):
        reply = router.dispatch(_inbound("/runs"), user_id=1)
    assert "run #1" in reply
    assert "done" in reply
    assert "Hourly mail triage" in reply


def test_status_command_flags_busy_agents():
    agents = [
        {"name": "Ava",   "role_title": "Lead",  "status": "active", "busy": 2},
        {"name": "Ethan", "role_title": "Sales", "status": "active", "busy": 0},
    ]
    with patch.object(router.db, "fetch_all", return_value=agents):
        reply = router.dispatch(_inbound("/status"), user_id=1)
    assert "Ava" in reply and "busy" in reply.lower()
    assert "Ethan" in reply  # but no busy flag


def test_free_text_calls_lead_chat_and_tags_thread():
    """Free text must hit lead_agent.chat and — if this is the first
    message from this platform — tag the thread with source_platform +
    source_external_id so session continuity works next time."""
    lead_response = {
        "thread_id": "new-thread-xyz",
        "response": "Hello!",
    }
    with patch.object(router.db, "fetch_one", return_value=None) as _existing, \
         patch.object(router.lead_agent, "chat", return_value=lead_response) as lead_call, \
         patch.object(router.db, "execute") as tag_call:
        reply = router.dispatch(_inbound("hi"), user_id=3)
    assert reply == "Hello!"
    lead_call.assert_called_once()
    # The tagging UPDATE was issued
    assert any(
        "source_platform" in c.args[0] and "source_external_id" in c.args[0]
        for c in tag_call.call_args_list
    )


def test_free_text_reuses_existing_thread():
    """If the user already has a TG-sourced active thread, the router
    must pass its thread_id to lead_agent.chat."""
    existing = {"thread_id": "ongoing-thread"}
    lead_response = {"thread_id": "ongoing-thread", "response": "continuing"}
    with patch.object(router.db, "fetch_one", return_value=existing), \
         patch.object(router.lead_agent, "chat", return_value=lead_response) as lead_call, \
         patch.object(router.db, "execute"):
        router.dispatch(_inbound("next message"), user_id=3)
    # thread_id kw arg matches the existing thread
    _, kwargs = lead_call.call_args
    assert kwargs["thread_id"] == "ongoing-thread"


def test_unknown_slash_command_gets_hint():
    reply = router.dispatch(_inbound("/bogus"), user_id=1)
    assert "/bogus" in reply.lower()
    assert "/help" in reply.lower()


def test_command_registry_lists_non_admin_commands():
    from backend.services.im_channels import commands as cmd_mod
    names = {c.name for c in cmd_mod.list_commands()}
    # Core built-ins expected at M2
    assert {"help", "start", "runs", "status"}.issubset(names)


def test_command_alias_routes_to_same_handler():
    """/h and /? should both resolve to the /help handler."""
    reply_h = router.dispatch(_inbound("/h"), user_id=1)
    reply_help = router.dispatch(_inbound("/help"), user_id=1)
    # Both return the help text (same handler, identical output)
    assert reply_h == reply_help


def test_free_text_strips_artifact_and_proposal_fences():
    """Artifact / hire / project fences are for the web UI — replace
    them with a breadcrumb in the IM reply so the user gets a pointer
    rather than 20KB of raw HTML."""
    with patch.object(router.db, "fetch_one", return_value=None), \
         patch.object(router.lead_agent, "chat", return_value={
             "thread_id": "t1",
             "response": (
                 "Here's your deck:\n\n"
                 "```artifact-html Deck\n<html>…</html>\n```\n\n"
                 "And a hire proposal:\n\n"
                 "```hire\n{\"name\":\"X\"}\n```\n\nDone."
             ),
         }), \
         patch.object(router.db, "execute"):
        reply = router.dispatch(_inbound("do the thing"), user_id=3)
    assert "artifact" in reply.lower()
    assert "proposal" in reply.lower()
    # The fence bodies should NOT be in the user-visible reply
    assert "<html>" not in reply
    assert '"name":"X"' not in reply
