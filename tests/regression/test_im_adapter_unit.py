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
    reply = router.dispatch(_inbound("/help"), user_id=1).text
    assert reply is not None
    assert "/start" in reply and "/help" in reply


def test_start_command_persists_external_id():
    """Exercises the DB path. Verifies that /start calls UPDATE on the
    binding so subsequent sends know where to push replies."""
    with patch.object(router.db, "execute") as mock_execute, \
         patch.object(router.db, "fetch_one", return_value={"display_name": "Q"}):
        reply = router.dispatch(_inbound("/start", external_id="chat-42"), user_id=7).text
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
        reply = router.dispatch(_inbound("/runs"), user_id=1).text
    assert "run #1" in reply
    assert "done" in reply
    assert "Hourly mail triage" in reply


def test_status_command_flags_busy_agents():
    agents = [
        {"name": "Ava",   "role_title": "Lead",  "status": "active", "busy": 2},
        {"name": "Ethan", "role_title": "Sales", "status": "active", "busy": 0},
    ]
    with patch.object(router.db, "fetch_all", return_value=agents):
        reply = router.dispatch(_inbound("/status"), user_id=1).text
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
        reply = router.dispatch(_inbound("hi"), user_id=3).text
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
    reply = router.dispatch(_inbound("/bogus"), user_id=1).text
    assert "/bogus" in reply.lower()
    assert "/help" in reply.lower()


def test_command_registry_lists_non_admin_commands():
    from backend.services.im_channels import commands as cmd_mod
    names = {c.name for c in cmd_mod.list_commands()}
    # Core built-ins expected at M2 + advanced commands at M5
    assert {"help", "start", "runs", "status",
            "workflows", "run", "run_status",
            "projects", "project", "hire"}.issubset(names)


def test_run_command_rejects_bad_args():
    reply = router.dispatch(_inbound("/run"), user_id=1).text
    assert "Usage" in reply
    reply = router.dispatch(_inbound("/run abc"), user_id=1).text
    assert "Usage" in reply


def test_run_command_workflow_not_yours():
    with patch.object(router.db, "fetch_one", return_value=None):
        reply = router.dispatch(_inbound("/run 42"), user_id=1).text
    assert "not found" in reply


def test_run_command_dispatches_to_engine():
    wf = {"id": 42, "name": "my workflow"}
    with patch.object(router.db, "fetch_one", return_value=wf), \
         patch("backend.engine.dispatch_workflow", return_value=99) as dispatch:
        reply = router.dispatch(_inbound("/run 42 hello world"), user_id=7).text
    assert "Dispatched run #99" in reply
    assert "my workflow" in reply
    # engine was called with the right args
    _, kwargs = dispatch.call_args
    assert kwargs["workflow_id"] == 42
    assert kwargs["user_id"] == 7
    assert kwargs["initial_input"] == "hello world"
    assert kwargs["trigger_source"] == "chat"


def test_project_command_not_found():
    with patch.object(router.db, "fetch_one", return_value=None):
        reply = router.dispatch(_inbound("/project 999"), user_id=1).text
    assert "not found" in reply


def test_project_command_formats_status():
    project = {"id": 1, "name": "Kestrel", "status": "active", "goal": "G"}
    def _fetch(sql, params):
        if "FROM projects" in sql: return project
        if "project_members" in sql: return {"c": 5}
        if "FROM runs" in sql: return {"total": 12, "running": 1, "done": 10, "cost": 3.45}
        if "project_artifacts" in sql: return {"c": 3}
        return None
    with patch.object(router.db, "fetch_one", side_effect=_fetch):
        reply = router.dispatch(_inbound("/project 1"), user_id=7).text
    assert "Kestrel" in reply
    assert "5" in reply            # member count
    assert "12" in reply and "10" in reply  # run stats
    assert "3" in reply            # artifact count
    assert "$3.45" in reply


def test_workflows_command_lists_names():
    rows = [{"id": 5, "name": "Hourly mail triage"},
            {"id": 3, "name": "Daily report"}]
    with patch.object(router.db, "fetch_all", return_value=rows):
        reply = router.dispatch(_inbound("/workflows"), user_id=1).text
    assert "#5" in reply and "Hourly mail triage" in reply
    assert "#3" in reply and "Daily report" in reply


def test_workflows_alias_wf():
    with patch.object(router.db, "fetch_all", return_value=[]):
        reply_a = router.dispatch(_inbound("/wf"), user_id=1)
        reply_b = router.dispatch(_inbound("/workflows"), user_id=1)
    assert reply_a == reply_b


def test_hire_command_shortcuts_through_lead():
    """/hire X should end up calling lead_agent.chat with a prompt that
    asks for a hire proposal — so the web UI card flow works as normal."""
    from backend.services.im_channels import router as _r
    lead_response = {"thread_id": "t1", "response": "Proposing..."}
    with patch.object(_r.db, "fetch_one", return_value=None), \
         patch.object(_r.lead_agent, "chat", return_value=lead_response) as lead_call, \
         patch.object(_r.db, "execute"):
        reply = router.dispatch(_inbound("/hire data scientist"), user_id=3).text
    # lead_agent.chat was called (through _handle_lead) with our enhanced prompt
    lead_call.assert_called_once()
    _, kwargs = lead_call.call_args
    # The enhanced prompt asks Lead for a hire fence
    passed_text = lead_call.call_args[0][1]  # second positional = user_text
    assert "hire" in passed_text.lower()
    assert "data scientist" in passed_text.lower()


def test_command_alias_routes_to_same_handler():
    """/h and /? should both resolve to the /help handler."""
    reply_h = router.dispatch(_inbound("/h"), user_id=1)
    reply_help = router.dispatch(_inbound("/help"), user_id=1)
    # Both return the help text (same handler, identical output)
    assert reply_h == reply_help


def test_free_text_strips_artifact_and_proposal_fences():
    """Artifact fences are now stripped from the text entirely (the
    artifact rides separately via result.artifacts). Proposal fences
    (workflow/hire/project) still get breadcrumbed in the text so the
    user knows to check the web UI for the card."""
    with patch.object(router.db, "fetch_one", return_value=None), \
         patch.object(router.lead_agent, "chat", return_value={
             "thread_id": "t1",
             "response": (
                 "Here's your deck:\n\n"
                 "```artifact-html Deck\n<html>…</html>\n```\n\n"
                 "And a hire proposal:\n\n"
                 "```hire\n{\"name\":\"X\"}\n```\n\nDone."
             ),
             "artifacts": [{"kind": "html", "title": "Deck", "html": "<html>…</html>"}],
         }), \
         patch.object(router.db, "execute"):
        result = router.dispatch(_inbound("do the thing"), user_id=3)
    # text: no raw HTML, no hire JSON, proposal breadcrumb present
    assert result.text is not None
    assert "proposal" in result.text.lower()
    assert "<html>" not in result.text
    assert '"name":"X"' not in result.text
    # artifact: separated out for rich delivery
    assert len(result.artifacts) == 1
    assert result.artifacts[0]["kind"] == "html"


def test_dispatch_returns_dispatch_result_for_slash_commands():
    from backend.services.im_channels.base import DispatchResult
    result = router.dispatch(_inbound("/help"), user_id=1)
    assert isinstance(result, DispatchResult)
    assert result.text is not None
    assert result.artifacts == []


def test_send_artifact_html_uploads_as_document():
    """HTML artifacts get uploaded as .html files via sendDocument."""
    adapter = tg.TelegramAdapter({
        "id": 1, "user_id": 99, "platform": "telegram",
        "external_id": "555", "secret_encrypted": None, "metadata": {},
        "secret": "fake-token",
    })
    captured = {}

    def _capture(req, timeout=None):
        captured["url"] = req.full_url
        captured["body"] = req.data
        captured["ctype"] = req.headers.get("Content-type")
        return _fake_http({"ok": True, "result": {}})

    artifact = {"kind": "html", "title": "My Deck", "html": "<html>hi</html>"}
    with patch("urllib.request.urlopen", side_effect=_capture):
        handled = adapter.send_artifact("555", artifact)
    assert handled is True
    assert captured["url"].endswith("/sendDocument")
    # multipart body includes the filename + html content
    body = captured["body"]
    assert b'filename="My_Deck.html"' in body
    assert b"<html>hi</html>" in body
    assert captured["ctype"].startswith("multipart/form-data")


def test_send_artifact_short_markdown_sends_inline():
    """Short markdown bypasses the upload path and goes as regular text
    (friendlier than forcing a download for a 100-char note)."""
    adapter = tg.TelegramAdapter({
        "id": 1, "user_id": 99, "platform": "telegram",
        "external_id": "555", "secret_encrypted": None, "metadata": {},
        "secret": "fake-token",
    })
    captured = {}

    def _capture(req, timeout=None):
        captured["url"] = req.full_url
        captured["body"] = json.loads(req.data.decode("utf-8"))
        return _fake_http({"ok": True, "result": {}})

    artifact = {"kind": "markdown", "title": "Quick note",
                "markdown": "Hello **world** — short."}
    with patch("urllib.request.urlopen", side_effect=_capture):
        handled = adapter.send_artifact("555", artifact)
    assert handled is True
    assert captured["url"].endswith("/sendMessage")
    assert "Quick note" in captured["body"]["text"]
    assert "Hello" in captured["body"]["text"]


def test_send_artifact_file_as_image():
    """A file artifact with image mime → sendPhoto, not sendDocument."""
    adapter = tg.TelegramAdapter({
        "id": 1, "user_id": 99, "platform": "telegram",
        "external_id": "555", "secret_encrypted": None, "metadata": {},
        "secret": "fake-token",
    })
    captured = {}

    def _capture(req, timeout=None):
        captured["url"] = req.full_url
        return _fake_http({"ok": True, "result": {}})

    import base64
    png_bytes = b"\x89PNG\r\n\x1a\nfake"
    artifact = {
        "kind": "file", "filename": "pic.png", "mime": "image/png",
        "encoding": "base64",
        "content": base64.b64encode(png_bytes).decode(),
    }
    with patch("urllib.request.urlopen", side_effect=_capture):
        handled = adapter.send_artifact("555", artifact)
    assert handled is True
    assert captured["url"].endswith("/sendPhoto")


def test_send_artifact_base_class_default_returns_false():
    """Adapters that don't override send_artifact must return False so
    the manager falls back to a text breadcrumb."""
    from backend.services.im_channels.base import BasePlatformAdapter

    class NoArtifactAdapter(BasePlatformAdapter):
        platform = "noop"
        def poll_once(self): return []
        def send(self, e, t): pass
        def send_typing(self, e): pass

    a = NoArtifactAdapter({"user_id": 1})
    assert a.send_artifact("x", {"kind": "html", "html": "<p/>"}) is False
