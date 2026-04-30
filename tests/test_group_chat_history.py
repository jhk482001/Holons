"""Tests for the include_history flag on group_chat send paths and for
the GroupChat "+ Add member" UI's API path (PUT /api/groups/{id}
member-list replacement is what the merge happens on top of).

The history flag is the backend half of the new "Fresh context — ignore
prior conversation" checkbox in GroupChat.tsx. We verify that:

  - include_history=True (default) feeds the full thread snapshot to
    each member, just like before this flag existed.
  - include_history=False clips the snapshot to messages with id >=
    the just-inserted user message id, so members see ONLY this turn
    (plus in-round prior agents in sequential mode).

Run with:
    cd Holons && python3 -m pytest tests/test_group_chat_history.py -v
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
from werkzeug.security import generate_password_hash

from backend import db
from backend.services import group_chat


def _hash(pwd: str) -> str:
    # scrypt isn't always linked in Python 3.9 on macOS — fall back to pbkdf2
    return generate_password_hash(pwd, method="pbkdf2:sha256")


# ---------------------------------------------------------------------------
# Fixtures — same shape as tests/test_services.py but extended to cover
# the group_chat_* tables which that file's truncate doesn't touch.
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session", autouse=True)
def init_db():
    db.init()
    yield
    db.close()


@pytest.fixture(autouse=True)
def clean_state():
    from tests.conftest import truncate_with_retry
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            truncate_with_retry(cur, """
                TRUNCATE group_chat_messages, group_chat_threads,
                         group_members, groups_tbl,
                         agents, as_users
                RESTART IDENTITY CASCADE
            """)
    yield


@pytest.fixture
def user_id():
    return db.execute_returning(
        "INSERT INTO as_users (username, password_hash) VALUES ('u', 'x') RETURNING id"
    )


def _make_agent(user_id: int, name: str) -> int:
    return db.execute_returning(
        """
        INSERT INTO agents (user_id, owner_user_id, name, role_title, system_prompt, status)
        VALUES (%s, %s, %s, 'role', 'sys', 'active') RETURNING id
        """,
        (user_id, user_id, name),
    )


def _make_group(user_id: int, mode: str, member_ids: list[int]) -> tuple[int, int]:
    """Returns (group_id, thread_id)."""
    gid = db.execute_returning(
        """
        INSERT INTO groups_tbl (user_id, name, mode, aggregator_agent_id)
        VALUES (%s, 'G', %s, NULL) RETURNING id
        """,
        (user_id, mode),
    )
    for i, aid in enumerate(member_ids):
        db.execute(
            "INSERT INTO group_members (group_id, agent_id, position) VALUES (%s, %s, %s)",
            (gid, aid, i),
        )
    tid = group_chat.get_or_create_thread(user_id, gid)
    return gid, tid


def _seed_history(thread_id: int, agent_id: int, lines: list[tuple[str, str]]) -> None:
    """Seed the thread with a list of (role, content) tuples."""
    for role, content in lines:
        if role == "user":
            db.execute(
                "INSERT INTO group_chat_messages (thread_id, role, content) VALUES (%s, 'user', %s)",
                (thread_id, content),
            )
        else:
            db.execute(
                "INSERT INTO group_chat_messages (thread_id, role, agent_id, content) VALUES (%s, 'agent', %s, %s)",
                (thread_id, agent_id, content),
            )


# ---------------------------------------------------------------------------
# A streaming-LLM stub that captures the user_text it was called with so
# we can assert exactly which prior messages each agent saw.
# ---------------------------------------------------------------------------

def _make_streaming_stub(captured: list[dict]):
    def _stub(*, agent_id, model_key, system_prompt, user_text, user_id, kind, **_kw):
        captured.append({
            "agent_id": agent_id,
            "user_text": user_text,
            "system_prompt": system_prompt,
        })
        # Simulate the (kind, payload) tuple stream invoke_streaming_for_agent yields.
        yield ("chunk", "ok")
        yield ("complete", {
            "text": "ok",
            "input_tokens": 1, "output_tokens": 1,
            "cost_usd": 0.0, "model_id": "stub",
        })
    return _stub


def _make_batch_stub(captured: list[dict]):
    def _stub(*, agent_id, model_key, system_prompt, user_text, user_id, kind, **_kw):
        captured.append({
            "agent_id": agent_id,
            "user_text": user_text,
        })
        return {
            "text": "ok", "input_tokens": 1, "output_tokens": 1,
            "cost_usd": 0.0, "model_id": "stub",
        }
    return _stub


# ---------------------------------------------------------------------------
# Tests — streaming path
# ---------------------------------------------------------------------------

class TestStreamingIncludeHistory:
    def test_include_history_true_sees_prior_turns(self, user_id):
        agent = _make_agent(user_id, "Alice")
        gid, tid = _make_group(user_id, "parallel", [agent])
        _seed_history(tid, agent, [
            ("user", "earlier ask"),
            ("agent", "earlier reply"),
        ])

        captured: list[dict] = []
        with patch(
            "backend.llm_clients.invoke_streaming_for_agent",
            side_effect=_make_streaming_stub(captured),
        ):
            list(group_chat.send_user_message_streaming(
                user_id, gid, tid, "new ask", include_history=True,
            ))

        assert len(captured) == 1
        prompt = captured[0]["user_text"]
        # Prior turns should be visible to the agent.
        assert "earlier ask" in prompt
        assert "earlier reply" in prompt
        assert "new ask" in prompt

    def test_include_history_false_omits_prior_turns(self, user_id):
        agent = _make_agent(user_id, "Alice")
        gid, tid = _make_group(user_id, "parallel", [agent])
        _seed_history(tid, agent, [
            ("user", "earlier ask"),
            ("agent", "earlier reply"),
        ])

        captured: list[dict] = []
        with patch(
            "backend.llm_clients.invoke_streaming_for_agent",
            side_effect=_make_streaming_stub(captured),
        ):
            list(group_chat.send_user_message_streaming(
                user_id, gid, tid, "fresh ask", include_history=False,
            ))

        assert len(captured) == 1
        prompt = captured[0]["user_text"]
        # The agent must still see the user's just-sent message.
        assert "fresh ask" in prompt
        # But prior conversation must be gone.
        assert "earlier ask" not in prompt
        assert "earlier reply" not in prompt

    def test_include_history_false_persists_user_message(self, user_id):
        """The user message is still saved to the thread when context is dropped —
        the toggle only affects what the LLM sees, not what's stored."""
        agent = _make_agent(user_id, "Alice")
        gid, tid = _make_group(user_id, "parallel", [agent])

        captured: list[dict] = []
        with patch(
            "backend.llm_clients.invoke_streaming_for_agent",
            side_effect=_make_streaming_stub(captured),
        ):
            list(group_chat.send_user_message_streaming(
                user_id, gid, tid, "fresh ask", include_history=False,
            ))

        rows = db.fetch_all(
            "SELECT role, content FROM group_chat_messages WHERE thread_id = %s ORDER BY id",
            (tid,),
        )
        kinds = [(r["role"], r["content"]) for r in rows]
        # User message + agent reply both persisted.
        assert ("user", "fresh ask") in kinds
        assert any(r["role"] == "agent" for r in rows)

    def test_sequential_mode_fresh_context_still_lets_later_agents_see_earlier(self, user_id):
        """In sequential mode with include_history=False, agent #2 should
        still see agent #1's reply from this round (in-round visibility
        is what defines sequential mode), but NOT prior conversation."""
        a1 = _make_agent(user_id, "Alice")
        a2 = _make_agent(user_id, "Bob")
        gid, tid = _make_group(user_id, "sequential", [a1, a2])
        _seed_history(tid, a1, [
            ("user", "earlier ask"),
            ("agent", "earlier reply from alice"),
        ])

        captured: list[dict] = []

        # Each call returns a slightly different reply so we can assert
        # whether agent #2 sees agent #1's reply.
        call_idx = {"i": 0}
        def _stub(*, agent_id, system_prompt, user_text, **_kw):
            captured.append({"agent_id": agent_id, "user_text": user_text})
            replies = ["alice-reply-this-round", "bob-reply-this-round"]
            text = replies[call_idx["i"]]
            call_idx["i"] += 1
            yield ("chunk", text)
            yield ("complete", {
                "text": text, "input_tokens": 1, "output_tokens": 1,
                "cost_usd": 0.0, "model_id": "stub",
            })

        with patch(
            "backend.llm_clients.invoke_streaming_for_agent",
            side_effect=_stub,
        ):
            list(group_chat.send_user_message_streaming(
                user_id, gid, tid, "fresh ask", include_history=False,
            ))

        assert len(captured) == 2
        alice_prompt, bob_prompt = captured[0]["user_text"], captured[1]["user_text"]
        # Neither agent should see the older conversation.
        assert "earlier ask" not in alice_prompt
        assert "earlier reply from alice" not in alice_prompt
        assert "earlier reply from alice" not in bob_prompt
        # Bob (agent #2 in sequential mode) MUST see Alice's in-round reply.
        assert "alice-reply-this-round" in bob_prompt
        # Both see the user's fresh message.
        assert "fresh ask" in alice_prompt
        assert "fresh ask" in bob_prompt


# ---------------------------------------------------------------------------
# Tests — batch (non-streaming) path. Same flag, same semantics, but
# different code path so worth a smaller sanity check.
# ---------------------------------------------------------------------------

class TestBatchIncludeHistory:
    def test_batch_path_respects_flag(self, user_id):
        agent = _make_agent(user_id, "Alice")
        gid, tid = _make_group(user_id, "parallel", [agent])
        _seed_history(tid, agent, [
            ("user", "old"),
            ("agent", "old reply"),
        ])

        captured: list[dict] = []
        with patch(
            "backend.services.group_chat.llm_invoke",
            side_effect=_make_batch_stub(captured),
        ):
            group_chat.send_user_message(
                user_id, gid, tid, "new", include_history=False,
            )

        assert len(captured) == 1
        prompt = captured[0]["user_text"]
        assert "new" in prompt
        assert "old" not in prompt
        assert "old reply" not in prompt


# ---------------------------------------------------------------------------
# Tests — PUT /api/groups/{id} member replacement (the API path the
# new "+ Add member" modal in GroupChat.tsx hits). The frontend builds
# `merged = unique([...current, ...picked])` and PUTs that whole list;
# the route DELETE+INSERTs to match. We confirm the merge round-trips.
# ---------------------------------------------------------------------------

@pytest.fixture
def app_client():
    with patch("backend.worker.WorkerRegistry.start_agent", return_value=None), \
         patch("backend.worker.WorkerRegistry.stop_agent", return_value=None), \
         patch("backend.worker.WorkerRegistry.start_all_active", return_value=0):
        from backend import app as app_module
        app = app_module.app
        app.config["TESTING"] = True
        yield app.test_client()


@pytest.fixture
def auth_client(app_client):
    pwd_hash = _hash("secret")
    db.execute_returning(
        "INSERT INTO as_users (username, password_hash, display_name, role) "
        "VALUES ('tester', %s, 'Tester', 'user') RETURNING id",
        (pwd_hash,),
    )
    res = app_client.post("/api/login", json={"username": "tester", "password": "secret"})
    assert res.status_code == 200, res.get_json()
    return app_client


class TestAddMemberFlow:
    def test_add_member_via_put_appends_without_dropping_existing(self, auth_client):
        # Three agents — start with two as members, then "add" a third
        # the same way GroupChat.tsx does: PUT with the merged list.
        a1 = auth_client.post("/api/agents", json={"name": "A1"}).get_json()["id"]
        a2 = auth_client.post("/api/agents", json={"name": "A2"}).get_json()["id"]
        a3 = auth_client.post("/api/agents", json={"name": "A3"}).get_json()["id"]
        gid = auth_client.post("/api/groups", json={
            "name": "G", "mode": "parallel",
            "member_agent_ids": [a1, a2],
        }).get_json()["id"]

        # Mirror the frontend's merge: `unique([...current, ...picked])`.
        current = [a1, a2]
        picked = [a3]
        merged = list(dict.fromkeys([*current, *picked]))
        res = auth_client.put(f"/api/groups/{gid}", json={"member_agent_ids": merged})
        assert res.status_code == 200, res.get_json()

        body = auth_client.get(f"/api/groups/{gid}").get_json()
        member_ids = sorted(m["agent_id"] for m in body["members"])
        assert member_ids == sorted([a1, a2, a3])

    def test_add_member_with_duplicate_picked_is_idempotent(self, auth_client):
        # If somehow a stale "picked" set contained an existing member
        # (defensive — the modal filters them out, but the merge
        # de-dupes anyway), the resulting member list must not double up.
        a1 = auth_client.post("/api/agents", json={"name": "A1"}).get_json()["id"]
        a2 = auth_client.post("/api/agents", json={"name": "A2"}).get_json()["id"]
        gid = auth_client.post("/api/groups", json={
            "name": "G", "mode": "parallel", "member_agent_ids": [a1],
        }).get_json()["id"]

        merged = list(dict.fromkeys([a1, a1, a2]))  # duplicate a1 deliberately
        res = auth_client.put(f"/api/groups/{gid}", json={"member_agent_ids": merged})
        assert res.status_code == 200

        body = auth_client.get(f"/api/groups/{gid}").get_json()
        ids = [m["agent_id"] for m in body["members"]]
        assert ids.count(a1) == 1
        assert ids.count(a2) == 1
