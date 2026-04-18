"""Tests for Phase A+B project + quota + coordinator flows.

Run with:
    cd agent_company && DB_BACKEND=postgres python3 -m pytest tests/test_projects.py -v

The tests exercise DB-backed logic without firing LLM calls. Worker +
network side effects are monkeypatched where they'd otherwise block.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
from werkzeug.security import generate_password_hash

from backend import db


def _hash(pwd: str) -> str:
    return generate_password_hash(pwd, method="pbkdf2:sha256")


@pytest.fixture(scope="session", autouse=True)
def init_db():
    db.init()
    yield
    db.close()


@pytest.fixture(autouse=True)
def clean_state():
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                TRUNCATE project_events, project_reports, project_milestones,
                         project_members, projects,
                         auto_topup_events,
                         agent_tasks, run_steps, runs,
                         workflow_nodes, workflows,
                         group_members, groups_tbl,
                         agent_quotas,
                         lead_messages, lead_conversations,
                         api_tokens
                         RESTART IDENTITY CASCADE
            """)
            cur.execute("DELETE FROM as_users WHERE username LIKE 'testu_%'")
            cur.execute("DELETE FROM agents WHERE name LIKE 'testa_%'")
        conn.commit()


@pytest.fixture
def user_id():
    uid = db.execute_returning(
        "INSERT INTO as_users (username, password_hash, display_name, role) "
        "VALUES ('testu_quota', %s, 'Tester', 'admin') RETURNING id",
        (_hash("x"),),
    )
    return uid


@pytest.fixture
def agents(user_id):
    """Two simple active agents that tests can use as project members."""
    ids = []
    for name in ("testa_writer", "testa_reviewer"):
        aid = db.execute_returning(
            """INSERT INTO agents (user_id, owner_user_id, name, role_title,
                                   system_prompt, status)
               VALUES (%s, %s, %s, 'role', 'prompt', 'active') RETURNING id""",
            (user_id, user_id, name),
        )
        ids.append(aid)
    return ids


@pytest.fixture
def workflow_id(user_id):
    """A bare workflow row — needed because runs.workflow_id is NOT NULL."""
    return db.execute_returning(
        "INSERT INTO workflows (user_id, name) VALUES (%s, 'testwf') RETURNING id",
        (user_id,),
    )


def _client(user_id: int):
    from backend.app import app
    app.config["TESTING"] = True
    app.config["SECRET_KEY"] = "test-secret"
    c = app.test_client()
    with c.session_transaction() as s:
        s["user_id"] = user_id
    return c


# ---------------------------------------------------------------------------
# Project CRUD + attribution
# ---------------------------------------------------------------------------

def test_create_project_and_list(user_id, agents):
    c = _client(user_id)
    r = c.post("/api/projects", json={
        "name": "Test Project",
        "description": "d",
        "goal": "g",
        "coordinator_agent_id": agents[0],
        "members": [
            {"agent_id": agents[0], "daily_alloc_pct": 100, "monthly_alloc_pct": 100},
            {"agent_id": agents[1], "daily_alloc_pct": 50, "monthly_alloc_pct": 100},
        ],
    })
    assert r.status_code == 200
    pid = r.get_json()["id"]

    listing = c.get("/api/projects").get_json()
    assert any(p["id"] == pid and p["member_count"] == 2 for p in listing)

    detail = c.get(f"/api/projects/{pid}").get_json()
    assert detail["name"] == "Test Project"
    assert len(detail["members"]) == 2
    assert detail["coordinator_agent_id"] == agents[0]


def test_project_status_change_writes_event(user_id, agents):
    c = _client(user_id)
    pid = c.post("/api/projects", json={
        "name": "Flip", "members": [{"agent_id": agents[0]}]
    }).get_json()["id"]
    c.put(f"/api/projects/{pid}", json={"status": "paused"})
    events = c.get(f"/api/projects/{pid}/events").get_json()
    assert any(e["event_type"] == "status_changed" for e in events)


# ---------------------------------------------------------------------------
# Quota enforcement
# ---------------------------------------------------------------------------

def test_quota_can_run_ok_when_under_cap(user_id, agents):
    from backend.services import quotas
    # Set a $2 daily cap via agent_quotas row.
    db.execute(
        """INSERT INTO agent_quotas (agent_id, name, window_type, max_cost_usd,
                                     hard_limit, enabled)
           VALUES (%s, 'daily', 'daily', 2.0, TRUE, TRUE)""",
        (agents[0],),
    )
    d = quotas.can_run(agents[0])
    assert d["ok"] is True


def test_quota_can_run_blocked_when_exceeded(user_id, agents):
    from backend.services import quotas
    db.execute(
        """INSERT INTO agent_quotas (agent_id, name, window_type, max_cost_usd,
                                     hard_limit, enabled, current_cost_usd)
           VALUES (%s, 'daily', 'daily', 2.0, TRUE, TRUE, 2.5)""",
        (agents[0],),
    )
    d = quotas.can_run(agents[0])
    assert d["ok"] is False
    assert "quota" in d["reason"].lower()


def test_project_slice_respects_allocation(user_id, agents, workflow_id):
    """50% daily slice + $2 agent cap → project-scoped cap is $1.
    Spending $1.10 on this project should block further project-attributed
    steps even though the agent overall has room.
    """
    from backend.services import quotas
    db.execute(
        """INSERT INTO agent_quotas (agent_id, name, window_type, max_cost_usd,
                                     hard_limit, enabled)
           VALUES (%s, 'daily', 'daily', 2.0, TRUE, TRUE)""",
        (agents[0],),
    )
    pid = db.execute_returning(
        """INSERT INTO projects (user_id, name, status) VALUES (%s, 'P', 'active')
           RETURNING id""",
        (user_id,),
    )
    db.execute(
        """INSERT INTO project_members (project_id, agent_id, daily_alloc_pct,
                                         monthly_alloc_pct)
           VALUES (%s, %s, 50, 100)""",
        (pid, agents[0]),
    )
    # Simulate $1.10 spent on this project via run_steps.
    run_id = db.execute_returning(
        "INSERT INTO runs (user_id, status, project_id, workflow_id) "
        "VALUES (%s, 'done', %s, %s) RETURNING id",
        (user_id, pid, workflow_id),
    )
    db.execute(
        """INSERT INTO run_steps (run_id, agent_id, project_id, cost_usd,
                                  input_tokens, output_tokens, started_at)
           VALUES (%s, %s, %s, 1.10, 100, 100, NOW())""",
        (run_id, agents[0], pid),
    )
    d = quotas.can_run(agents[0], project_id=pid)
    assert d["ok"] is False
    assert "project" in d["reason"].lower()


# ---------------------------------------------------------------------------
# Usage aggregation endpoint
# ---------------------------------------------------------------------------

def test_usage_daily_groups_by_project(user_id, agents, workflow_id):
    c = _client(user_id)
    pid = db.execute_returning(
        "INSERT INTO projects (user_id, name, status) VALUES (%s, 'Pgood', 'active') RETURNING id",
        (user_id,),
    )
    run_id = db.execute_returning(
        "INSERT INTO runs (user_id, status, project_id, workflow_id) "
        "VALUES (%s, 'done', %s, %s) RETURNING id",
        (user_id, pid, workflow_id),
    )
    db.execute(
        """INSERT INTO run_steps (run_id, agent_id, project_id, cost_usd,
                                  input_tokens, output_tokens, started_at)
           VALUES (%s, %s, %s, 0.42, 500, 200, NOW() - INTERVAL '2 hours')""",
        (run_id, agents[0], pid),
    )
    r = c.get("/api/usage/daily?group_by=project&days=7").get_json()
    rows = r["rows"]
    assert any(row["key"] == pid and row["cost"] >= 0.41 for row in rows)


# ---------------------------------------------------------------------------
# Coordinator chat thread creation
# ---------------------------------------------------------------------------

def test_coordinator_chat_thread_isolated(user_id, agents):
    c = _client(user_id)
    pid = c.post("/api/projects", json={
        "name": "Coord",
        "coordinator_agent_id": agents[0],
        "members": [{"agent_id": agents[0]}],
    }).get_json()["id"]
    r = c.get(f"/api/projects/{pid}/chat/thread").get_json()
    tid = r["thread_id"]
    # Project thread uses proj-* prefix and is therefore NOT returned by the
    # main Lead /api/lead/threads list.
    assert tid.startswith(f"proj-{pid}-")
    lead_threads = c.get("/api/lead/threads").get_json()
    assert not any(t["thread_id"].startswith("proj-") for t in lead_threads)


# ---------------------------------------------------------------------------
# Auto-topup ledger
# ---------------------------------------------------------------------------

def test_autotopup_ledger_increments(user_id, agents):
    from backend.services import quotas
    db.execute(
        "UPDATE as_users SET auto_topup_enabled = TRUE, "
        "auto_topup_per_topup_cost = 1.0, auto_topup_max_per_day = 2 "
        "WHERE id = %s",
        (user_id,),
    )
    granted1 = quotas.maybe_autotopup(user_id, agents[0])
    granted2 = quotas.maybe_autotopup(user_id, agents[0])
    granted3 = quotas.maybe_autotopup(user_id, agents[0])
    assert granted1 == 1.0 and granted2 == 1.0 and granted3 == 0.0  # daily cap of 2


# ---------------------------------------------------------------------------
# API tokens
# ---------------------------------------------------------------------------

def test_api_token_roundtrip(user_id, agents):
    c = _client(user_id)
    create = c.post("/api/me/api-tokens", json={"name": "test"}).get_json()
    raw = create["token"]
    # New client without session — protected endpoint should 401
    from backend.app import app
    c2 = app.test_client()
    assert c2.get("/api/projects").status_code == 401
    # With the bearer header — should work
    r = c2.get("/api/projects", headers={"Authorization": f"Bearer {raw}"})
    assert r.status_code == 200
    # Delete
    c.delete(f"/api/me/api-tokens/{create['id']}")
    assert c2.get("/api/projects",
                  headers={"Authorization": f"Bearer {raw}"}).status_code == 401
