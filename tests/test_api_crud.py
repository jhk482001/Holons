"""Backend API CRUD scenario tests.

Uses the Flask test client to exercise the full create → read → list →
update → delete lifecycle for agents, workflows, notifications, schedules,
and runs. Workers are monkeypatched out so these tests don't race against
the real background workers.

Run with:
    cd agent_company && python3 -m pytest tests/test_api_crud.py -v
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
from werkzeug.security import generate_password_hash

from backend import db


def _hash(pwd: str) -> str:
    # scrypt isn't always linked in Python 3.9 on macOS — fall back to pbkdf2
    return generate_password_hash(pwd, method="pbkdf2:sha256")
from backend.services import notifications as notif_service


# ============================================================================
# Fixtures
# ============================================================================

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
                TRUNCATE agent_tasks, run_steps, runs,
                         workflow_nodes, workflows,
                         group_members, groups_tbl,
                         agent_quotas, agent_skills, agent_shares,
                         skill_guardrails, agent_escalations,
                         notifications, schedules,
                         lead_conversations, lead_messages,
                         asset_usage_log, asset_audit_log, asset_grants,
                         agent_assets, rag_documents, asset_items,
                         audit_log,
                         agents, as_users
                RESTART IDENTITY CASCADE
            """)
    yield


@pytest.fixture
def app_client():
    """Flask test client with worker start/stop patched out.

    The real app imports backend.worker and tries to spawn a thread per
    agent on create/delete. In tests we don't want that — patch the
    registry methods to no-ops.
    """
    with patch("backend.worker.WorkerRegistry.start_agent", return_value=None), \
         patch("backend.worker.WorkerRegistry.stop_agent", return_value=None), \
         patch("backend.worker.WorkerRegistry.start_all_active", return_value=0):
        from backend import app as app_module
        app = app_module.app
        app.config["TESTING"] = True
        client = app.test_client()
        yield client


@pytest.fixture
def auth_client(app_client):
    """Client already logged in as a fresh regular (non-admin) user."""
    pwd_hash = _hash("secret")
    uid = db.execute_returning(
        "INSERT INTO as_users (username, password_hash, display_name, role) "
        "VALUES ('tester', %s, 'Tester', 'user') RETURNING id",
        (pwd_hash,),
    )
    res = app_client.post("/api/login", json={"username": "tester", "password": "secret"})
    assert res.status_code == 200, res.get_json()
    app_client.user_id = uid  # type: ignore
    return app_client


@pytest.fixture
def admin_client(app_client):
    """Client already logged in as a fresh admin user."""
    pwd_hash = _hash("secret")
    uid = db.execute_returning(
        "INSERT INTO as_users (username, password_hash, display_name, role) "
        "VALUES ('rootboss', %s, 'Root Boss', 'admin') RETURNING id",
        (pwd_hash,),
    )
    res = app_client.post("/api/login", json={"username": "rootboss", "password": "secret"})
    assert res.status_code == 200, res.get_json()
    app_client.user_id = uid  # type: ignore
    return app_client


# ============================================================================
# Agents CRUD
# ============================================================================

class TestAgentsCRUD:

    def test_full_lifecycle(self, auth_client):
        # LIST — initially empty
        r = auth_client.get("/api/agents")
        assert r.status_code == 200
        assert r.get_json() == []

        # CREATE
        r = auth_client.post("/api/agents", json={
            "name": "Ada",
            "role_title": "劇本評審",
            "description": "挑剔的 reviewer",
            "system_prompt": "你很挑",
            "primary_model_id": "claude-sonnet-4.6",
            "avatar_config": {"hair": "Medium", "face": "Calm"},
        })
        assert r.status_code == 200
        aid = r.get_json()["id"]
        assert isinstance(aid, int) and aid > 0

        # GET — verify fields round-tripped
        r = auth_client.get(f"/api/agents/{aid}")
        assert r.status_code == 200
        a = r.get_json()
        assert a["name"] == "Ada"
        assert a["role_title"] == "劇本評審"
        assert a["description"] == "挑剔的 reviewer"
        assert a["system_prompt"] == "你很挑"
        assert a["primary_model_id"] == "claude-sonnet-4.6"
        assert a["avatar_config"]["hair"] == "Medium"
        assert a["queue_depth"] == 0

        # LIST — now has one
        r = auth_client.get("/api/agents")
        assert len(r.get_json()) == 1

        # UPDATE — change name + avatar_config
        r = auth_client.put(f"/api/agents/{aid}", json={
            "name": "Ada v2",
            "avatar_config": {"hair": "Bangs", "face": "Smile"},
            "max_queue_depth": 10,
        })
        assert r.status_code == 200
        r = auth_client.get(f"/api/agents/{aid}")
        a = r.get_json()
        assert a["name"] == "Ada v2"
        assert a["avatar_config"]["hair"] == "Bangs"
        assert a["avatar_config"]["face"] == "Smile"
        assert a["max_queue_depth"] == 10

        # DELETE
        r = auth_client.delete(f"/api/agents/{aid}")
        assert r.status_code == 200

        # GET — now 404
        r = auth_client.get(f"/api/agents/{aid}")
        assert r.status_code == 404

        # LIST — empty again
        r = auth_client.get("/api/agents")
        assert r.get_json() == []

    def test_requires_auth(self, app_client):
        r = app_client.get("/api/agents")
        assert r.status_code == 401


# ============================================================================
# Workflows CRUD
# ============================================================================

class TestWorkflowsCRUD:

    def test_full_lifecycle(self, auth_client):
        # CREATE
        r = auth_client.post("/api/workflows", json={
            "name": "Screenplay",
            "description": "generate + review",
            "loop_enabled": False,
            "max_loops": 1,
        })
        assert r.status_code == 200
        wid = r.get_json()["id"]

        # GET — nodes empty
        r = auth_client.get(f"/api/workflows/{wid}")
        assert r.status_code == 200
        wf = r.get_json()
        assert wf["name"] == "Screenplay"
        assert wf["description"] == "generate + review"
        assert wf["nodes"] == []

        # LIST — 1 item
        r = auth_client.get("/api/workflows")
        assert len(r.get_json()) == 1

        # UPDATE
        r = auth_client.put(f"/api/workflows/{wid}", json={
            "name": "Screenplay v2",
            "loop_enabled": True,
            "max_loops": 3,
        })
        assert r.status_code == 200
        r = auth_client.get(f"/api/workflows/{wid}")
        wf = r.get_json()
        assert wf["name"] == "Screenplay v2"
        assert wf["loop_enabled"] is True
        assert wf["max_loops"] == 3

        # DELETE
        r = auth_client.delete(f"/api/workflows/{wid}")
        assert r.status_code == 200
        r = auth_client.get(f"/api/workflows/{wid}")
        assert r.status_code == 404

    def test_run_nonexistent_workflow_returns_404(self, auth_client):
        """Regression: POST /api/workflows/<id>/run for a workflow that
        doesn't exist used to crash with a 500 ForeignKeyViolation because
        engine.dispatch_workflow INSERTed into runs without validating the
        workflow first. It should return a clean 404 JSON instead."""
        r = auth_client.post(
            "/api/workflows/999999/run", json={"input": "x"},
        )
        assert r.status_code == 404
        body = r.get_json()
        assert body and "not found" in body.get("error", "").lower()

    def test_run_other_users_workflow_returns_404(self, auth_client):
        """A workflow owned by another user must not be runnable — same
        404 path as 'doesn't exist'."""
        from backend import db
        # Seed a second user and a workflow owned by them
        other_uid = db.execute_returning(
            "INSERT INTO as_users (username, password_hash) VALUES ('mallory', 'x') RETURNING id"
        )
        try:
            other_wid = db.execute_returning(
                "INSERT INTO workflows (user_id, name) VALUES (%s, 'secret') RETURNING id",
                (other_uid,),
            )
            r = auth_client.post(
                f"/api/workflows/{other_wid}/run", json={"input": "x"},
            )
            assert r.status_code == 404
        finally:
            db.execute("DELETE FROM workflows WHERE user_id = %s", (other_uid,))
            db.execute("DELETE FROM as_users WHERE id = %s", (other_uid,))


# ============================================================================
# Notifications CRUD (emit → list → mark read → dismiss)
# ============================================================================

class TestNotificationsCRUD:

    def test_full_lifecycle(self, auth_client):
        uid = auth_client.user_id  # type: ignore

        # Initially no notifications
        r = auth_client.get("/api/notifications")
        assert r.get_json() == []
        r = auth_client.get("/api/notifications/unread_count")
        assert r.get_json()["count"] == 0

        # Emit two notifications via the service layer
        nid1 = notif_service.emit(
            uid, "budget_warning",
            title="預算即將用完",
            body="剩餘 10%",
            severity="warn",
        )
        nid2 = notif_service.emit(
            uid, "queue_conflict",
            title="佇列衝突",
            body="urgent task 被 pause",
            severity="error",
        )

        # LIST
        r = auth_client.get("/api/notifications")
        rows = r.get_json()
        assert len(rows) == 2
        titles = {n["title"] for n in rows}
        assert titles == {"預算即將用完", "佇列衝突"}

        # unread_count = 2
        r = auth_client.get("/api/notifications/unread_count")
        assert r.get_json()["count"] == 2

        # MARK READ first one
        r = auth_client.post(f"/api/notifications/{nid1}/read")
        assert r.status_code == 200
        r = auth_client.get("/api/notifications/unread_count")
        assert r.get_json()["count"] == 1

        # RESOLVE second one
        r = auth_client.post(f"/api/notifications/{nid2}/resolve", json={"resolution": "ack"})
        assert r.status_code == 200

        # DISMISS first one
        r = auth_client.post(f"/api/notifications/{nid1}/dismiss")
        assert r.status_code == 200

        # Only resolved one remains in non-dismissed filter
        r = auth_client.get("/api/notifications?status=resolved")
        rows = r.get_json()
        assert len(rows) == 1
        assert rows[0]["id"] == nid2


# ============================================================================
# Schedules CRUD
# ============================================================================

class TestSchedulesCRUD:

    def test_full_lifecycle(self, auth_client):
        # Need a workflow to schedule against
        r = auth_client.post("/api/workflows", json={"name": "WF"})
        wid = r.get_json()["id"]

        # LIST — empty
        r = auth_client.get("/api/schedules")
        assert r.get_json() == []

        # CREATE
        r = auth_client.post("/api/schedules", json={
            "workflow_id": wid,
            "name": "每小時跑一次",
            "trigger_type": "interval",
            "interval_seconds": 3600,
            "default_input": "hello",
            "priority": "normal",
        })
        assert r.status_code == 200
        sid = r.get_json()["id"]

        # LIST — 1 item
        r = auth_client.get("/api/schedules")
        rows = r.get_json()
        assert len(rows) == 1
        assert rows[0]["id"] == sid
        assert rows[0]["enabled"] is True

        # TOGGLE off
        r = auth_client.post(f"/api/schedules/{sid}/toggle", json={"enabled": False})
        assert r.status_code == 200
        r = auth_client.get("/api/schedules")
        assert r.get_json()[0]["enabled"] is False

        # DELETE
        r = auth_client.delete(f"/api/schedules/{sid}")
        assert r.status_code == 200
        r = auth_client.get("/api/schedules")
        assert r.get_json() == []


# ============================================================================
# Runs — list + get + stop (no real dispatch; insert a row directly)
# ============================================================================

class TestRunsAPI:

    def test_list_get_stop(self, auth_client):
        uid = auth_client.user_id  # type: ignore

        # Create a workflow
        r = auth_client.post("/api/workflows", json={"name": "WF"})
        wid = r.get_json()["id"]

        # Insert a run directly (avoid engine.dispatch_workflow which
        # requires nodes + workers)
        run_id = db.execute_returning(
            """
            INSERT INTO runs (workflow_id, user_id, initial_input, status)
            VALUES (%s, %s, %s, 'running') RETURNING id
            """,
            (wid, uid, "test input"),
        )

        # LIST — paginated response shape: {runs: [...], has_more: bool}
        r = auth_client.get("/api/runs")
        body = r.get_json()
        assert "runs" in body and "has_more" in body
        rows = body["runs"]
        assert len(rows) == 1
        assert rows[0]["id"] == run_id
        assert rows[0]["initial_input"] == "test input"
        assert body["has_more"] is False

        # GET — should include steps[] and tasks[]
        r = auth_client.get(f"/api/runs/{run_id}")
        assert r.status_code == 200
        run = r.get_json()
        assert run["id"] == run_id
        assert run["steps"] == []
        assert run["tasks"] == []

        # STOP (hot stop) — no tasks in queue, should still 200
        r = auth_client.post(f"/api/runs/{run_id}/stop")
        assert r.status_code == 200
        assert r.get_json()["ok"] is True

        # GET non-existent run → 404
        r = auth_client.get("/api/runs/99999")
        assert r.status_code == 404

    def test_runs_list_pagination(self, auth_client):
        """Regression: previously the runs list hard-coded LIMIT 50,
        silently hiding older rows. Now uses ?before_id= cursor pagination
        and returns has_more."""
        uid = auth_client.user_id  # type: ignore
        wid = db.execute_returning(
            "INSERT INTO workflows (user_id, name) VALUES (%s, 'wf') RETURNING id",
            (uid,),
        )
        # Seed 60 runs (> default limit of 50)
        ids = []
        for i in range(60):
            rid = db.execute_returning(
                "INSERT INTO runs (workflow_id, user_id, initial_input, status) "
                "VALUES (%s, %s, %s, 'done') RETURNING id",
                (wid, uid, f"input-{i}"),
            )
            ids.append(rid)

        # Page 1 — newest 50, has_more=True
        r = auth_client.get("/api/runs?limit=50")
        body = r.get_json()
        assert len(body["runs"]) == 50
        assert body["has_more"] is True
        assert body["runs"][0]["id"] == ids[-1]  # newest first
        assert body["runs"][-1]["id"] == ids[10]  # 10th-oldest

        # Page 2 — 10 remaining, has_more=False
        r = auth_client.get(
            f"/api/runs?limit=50&before_id={body['runs'][-1]['id']}"
        )
        body2 = r.get_json()
        assert len(body2["runs"]) == 10
        assert body2["has_more"] is False
        assert body2["runs"][0]["id"] == ids[9]
        assert body2["runs"][-1]["id"] == ids[0]

    def test_run_isolated_to_user(self, auth_client):
        """Another user's run should not be visible."""
        # Create a second user via direct DB
        other_uid = db.execute_returning(
            "INSERT INTO as_users (username, password_hash) VALUES ('bob', 'x') RETURNING id"
        )
        other_wid = db.execute_returning(
            "INSERT INTO workflows (user_id, name) VALUES (%s, 'bob wf') RETURNING id",
            (other_uid,),
        )
        other_run = db.execute_returning(
            "INSERT INTO runs (workflow_id, user_id, initial_input, status) "
            "VALUES (%s, %s, '', 'running') RETURNING id",
            (other_wid, other_uid),
        )

        # Current user (tester) should not see it in list or GET
        r = auth_client.get("/api/runs")
        body = r.get_json()
        assert body["runs"] == []
        assert body["has_more"] is False
        r = auth_client.get(f"/api/runs/{other_run}")
        assert r.status_code == 404


# ============================================================================
# Admin user management (Phase 1.2)
# ============================================================================

class TestAdminUsersCRUD:

    def test_non_admin_blocked_from_admin_routes(self, auth_client):
        """A regular user must get 403 on every admin endpoint."""
        r = auth_client.get("/api/admin/users")
        assert r.status_code == 403
        r = auth_client.post("/api/admin/users", json={"username": "x", "password": "pw"})
        assert r.status_code == 403
        r = auth_client.put("/api/admin/users/1", json={"role": "admin"})
        assert r.status_code == 403
        r = auth_client.delete("/api/admin/users/1")
        assert r.status_code == 403
        r = auth_client.post("/api/admin/users/1/reset_password", json={"new_password": "abcdef"})
        assert r.status_code == 403

    def test_unauthenticated_gets_401(self, app_client):
        r = app_client.get("/api/admin/users")
        assert r.status_code == 401

    def test_admin_full_lifecycle(self, admin_client):
        # LIST — should include self (the admin)
        r = admin_client.get("/api/admin/users")
        assert r.status_code == 200
        users = r.get_json()
        assert len(users) == 1
        assert users[0]["username"] == "rootboss"
        assert users[0]["role"] == "admin"

        # CREATE a regular user
        r = admin_client.post("/api/admin/users", json={
            "username": "newbie",
            "password": "newpass123",
            "display_name": "Newbie",
        })
        assert r.status_code == 200, r.get_json()
        new_uid = r.get_json()["id"]
        assert r.get_json()["role"] == "user"  # default

        # LIST now sees two users
        r = admin_client.get("/api/admin/users")
        assert len(r.get_json()) == 2

        # Duplicate username → 409
        r = admin_client.post("/api/admin/users", json={"username": "newbie", "password": "x"})
        assert r.status_code == 409

        # Promote to admin
        r = admin_client.put(f"/api/admin/users/{new_uid}", json={"role": "admin"})
        assert r.status_code == 200
        assert r.get_json()["role"] == "admin"

        # Invalid role → 400
        r = admin_client.put(f"/api/admin/users/{new_uid}", json={"role": "superuser"})
        assert r.status_code == 400

        # Reset password
        r = admin_client.post(
            f"/api/admin/users/{new_uid}/reset_password",
            json={"new_password": "newer_password"},
        )
        assert r.status_code == 200
        # Too-short password refused
        r = admin_client.post(
            f"/api/admin/users/{new_uid}/reset_password",
            json={"new_password": "abc"},
        )
        assert r.status_code == 400

        # Delete
        r = admin_client.delete(f"/api/admin/users/{new_uid}")
        assert r.status_code == 200
        r = admin_client.get("/api/admin/users")
        assert len(r.get_json()) == 1

    def test_cannot_demote_last_admin(self, admin_client):
        me_id = admin_client.user_id  # type: ignore
        r = admin_client.put(f"/api/admin/users/{me_id}", json={"role": "user"})
        assert r.status_code == 400
        assert "last admin" in r.get_json()["error"]

    def test_cannot_delete_self(self, admin_client):
        me_id = admin_client.user_id  # type: ignore
        r = admin_client.delete(f"/api/admin/users/{me_id}")
        assert r.status_code == 400
        assert "current user" in r.get_json()["error"]

    def test_cannot_delete_last_admin(self, admin_client):
        """Create a second admin, then try to delete the first — blocked
        because that'd leave only the caller (who is also protected). Then
        create a regular user and verify that can be deleted freely."""
        # Two admins total: rootboss (self) + another
        r = admin_client.post("/api/admin/users", json={
            "username": "admin2", "password": "secret123", "role": "admin",
        })
        other_admin = r.get_json()["id"]

        # Demote other_admin — allowed because 2 admins
        r = admin_client.put(f"/api/admin/users/{other_admin}", json={"role": "user"})
        assert r.status_code == 200

        # Now only 1 admin (rootboss). Trying to delete rootboss is blocked
        # by "cannot delete the current user" AND "last admin".
        me_id = admin_client.user_id  # type: ignore
        r = admin_client.delete(f"/api/admin/users/{me_id}")
        assert r.status_code == 400

        # Deleting the regular user works fine
        r = admin_client.delete(f"/api/admin/users/{other_admin}")
        assert r.status_code == 200


# ============================================================================
# System feature flags (Phase 1.3)
# ============================================================================

class TestFeatureFlags:

    def test_list_seeded_defaults(self, admin_client):
        """On first boot the DEFAULTS from services.feature_flags should
        have been upserted. The admin can read them."""
        r = admin_client.get("/api/system/feature_flags")
        assert r.status_code == 200
        flags = r.get_json()
        keys = {f["feature"] for f in flags}
        # All five canonical flags should be present after seed_flags()
        assert "view_audit_log" in keys
        assert "manage_user_quota" in keys
        assert "create_mcp_server" in keys
        assert "create_rag_source" in keys
        assert "grant_mcp_rag" in keys
        # Spec defaults: audit log + user quota open; MCP/RAG create + grant admin-only
        by_key = {f["feature"]: f for f in flags}
        assert by_key["view_audit_log"]["admin_only"] is False
        assert by_key["manage_user_quota"]["admin_only"] is False
        assert by_key["create_mcp_server"]["admin_only"] is True
        assert by_key["create_rag_source"]["admin_only"] is True
        assert by_key["grant_mcp_rag"]["admin_only"] is True

    def test_non_admin_can_read_but_not_write(self, auth_client):
        r = auth_client.get("/api/system/feature_flags")
        assert r.status_code == 200
        assert len(r.get_json()) >= 5

        r = auth_client.put(
            "/api/system/feature_flags/view_audit_log",
            json={"admin_only": True},
        )
        assert r.status_code == 403

    def test_admin_can_flip_flag(self, admin_client):
        # Flip view_audit_log to admin_only=True
        r = admin_client.put(
            "/api/system/feature_flags/view_audit_log",
            json={"admin_only": True},
        )
        assert r.status_code == 200
        assert r.get_json()["admin_only"] is True

        # Re-read to confirm persistence
        r = admin_client.get("/api/system/feature_flags")
        by_key = {f["feature"]: f for f in r.get_json()}
        assert by_key["view_audit_log"]["admin_only"] is True

        # Flip back
        r = admin_client.put(
            "/api/system/feature_flags/view_audit_log",
            json={"admin_only": False},
        )
        assert r.get_json()["admin_only"] is False

    def test_unknown_feature_returns_404(self, admin_client):
        r = admin_client.put(
            "/api/system/feature_flags/not_a_real_feature",
            json={"admin_only": True},
        )
        assert r.status_code == 404

    def test_require_feature_decorator(self, admin_client, app_client):
        """The require_feature decorator enforces the current flag state
        at call time. We don't have a real route using it yet in Phase 1,
        so exercise the helper directly through the service module."""
        from backend.services import feature_flags as ff

        # view_audit_log is open by default → regular user is allowed
        assert ff.is_admin_only("view_audit_log") is False

        # Flip it, now it's admin-only
        admin_client.put(
            "/api/system/feature_flags/view_audit_log",
            json={"admin_only": True},
        )
        assert ff.is_admin_only("view_audit_log") is True

        # Unknown feature defaults to open
        assert ff.is_admin_only("ghost_feature") is False

        # Cleanup
        admin_client.put(
            "/api/system/feature_flags/view_audit_log",
            json={"admin_only": False},
        )


# ============================================================================
# Asset library REST routes (Phase 2.4)
# ============================================================================

class TestAssetRoutes:

    def test_admin_create_list_update_delete(self, admin_client):
        # CREATE mcp asset
        r = admin_client.post("/api/assets", json={
            "kind": "mcp",
            "name": "Test MCP",
            "description": "rest test",
            "config": {"url": "https://example.com"},
            "credential": "Bearer secret",
        })
        assert r.status_code == 200, r.get_json()
        aid = r.get_json()["id"]

        # GET
        r = admin_client.get(f"/api/assets/{aid}")
        assert r.status_code == 200
        row = r.get_json()
        assert row["has_credential"] is True
        assert "credential_encrypted" not in row

        # LIST
        r = admin_client.get("/api/assets?kind=mcp")
        assert any(a["id"] == aid for a in r.get_json())

        # UPDATE — rename + disable
        r = admin_client.put(f"/api/assets/{aid}", json={
            "name": "Test MCP 2",
            "enabled": False,
        })
        assert r.status_code == 200
        assert r.get_json()["enabled"] is False

        # AUDIT
        r = admin_client.get(f"/api/assets/{aid}/audit")
        actions = [row["action"] for row in r.get_json()]
        assert "create" in actions and "update" in actions and "disable" in actions

        # DELETE
        r = admin_client.delete(f"/api/assets/{aid}")
        assert r.status_code == 200
        r = admin_client.get(f"/api/assets/{aid}")
        assert r.status_code == 404

    def test_invalid_kind_400(self, admin_client):
        r = admin_client.post("/api/assets", json={"kind": "banana", "name": "x"})
        assert r.status_code == 400

    def test_non_admin_blocked_by_feature_flags(self, auth_client):
        """create_mcp_server defaults to admin_only=True — regular users
        get 403 until the admin flips the flag."""
        # Regular user tries to create an MCP → 403
        r = auth_client.post("/api/assets", json={
            "kind": "mcp", "name": "blocked", "config": {"url": "x"},
        })
        assert r.status_code == 403

        # Regular user can still CREATE skills (no flag guarding that)
        r = auth_client.post("/api/assets", json={
            "kind": "skill", "name": "allowed", "config": {},
        })
        assert r.status_code == 200

    def test_non_admin_only_sees_own_and_granted(self, auth_client):
        """A regular user doesn't see another user's ungranted assets.
        Uses a second admin-created asset injected directly via the
        service layer (not a second HTTP client, which would share the
        session cookie and fight with auth_client)."""
        from backend import db
        from backend.services import assets as assets_service

        admin_uid = db.execute_returning(
            "INSERT INTO as_users (username, password_hash, display_name, role) "
            "VALUES ('assetadmin', %s, 'Admin', 'admin') RETURNING id",
            (_hash("secret"),),
        )
        aid = assets_service.create_asset(
            actor_user_id=admin_uid, kind="rag", name="admin-only-kb",
            config={"backend": "pgvector"},
        )
        tester_uid = auth_client.user_id  # type: ignore

        # tester (non-admin) doesn't see it
        r = auth_client.get("/api/assets?kind=rag")
        assert all(a["id"] != aid for a in r.get_json())

        # Grant via the service layer (bypassing session mixing)
        assets_service.grant(aid, tester_uid, admin_uid)

        r = auth_client.get("/api/assets?kind=rag")
        assert any(a["id"] == aid for a in r.get_json())

    def test_agent_asset_assignment(self, admin_client):
        from backend import db
        # Admin creates an asset
        r = admin_client.post("/api/assets", json={
            "kind": "tool", "name": "http_get_wrapper",
            "config": {"module": "backend.tools.http_get", "fn": "handler"},
        })
        aid = r.get_json()["id"]

        # Admin creates an agent
        admin_uid = admin_client.user_id  # type: ignore
        agent_id = db.execute_returning(
            "INSERT INTO agents (user_id, owner_user_id, name, status) "
            "VALUES (%s, %s, 'Tester', 'active') RETURNING id",
            (admin_uid, admin_uid),
        )

        # Assign asset → agent
        r = admin_client.post(
            f"/api/agents/{agent_id}/assets",
            json={"asset_id": aid},
        )
        assert r.status_code == 200

        # List agent's assets
        r = admin_client.get(f"/api/agents/{agent_id}/assets")
        rows = r.get_json()
        assert any(row["id"] == aid for row in rows)

        # Unassign
        r = admin_client.delete(f"/api/agents/{agent_id}/assets/{aid}")
        assert r.status_code == 200
        r = admin_client.get(f"/api/agents/{agent_id}/assets")
        assert all(row["id"] != aid for row in r.get_json())

    def test_audit_log_captures_mutations(self, admin_client):
        """Mutating API calls should appear in /api/audit_log. Read-only
        GETs should not (they'd flood the table)."""
        from backend import db as _db
        # Clear any audit rows from the admin_client's login (there
        # shouldn't be any because /api/login is skipped)
        _db.execute("TRUNCATE audit_log RESTART IDENTITY")

        # Mutations: create an asset + delete it
        r = admin_client.post("/api/assets", json={
            "kind": "skill", "name": "audit-me", "config": {},
        })
        aid = r.get_json()["id"]
        admin_client.delete(f"/api/assets/{aid}")

        # Passive reads should NOT land in the audit log
        admin_client.get("/api/assets")

        # List audit entries
        r = admin_client.get("/api/audit_log")
        body = r.get_json()
        entries = body["entries"]
        paths = [e["path"] for e in entries]
        assert any(p == "/api/assets" for p in paths)  # POST /api/assets
        assert any(p == f"/api/assets/{aid}" for p in paths)  # DELETE
        # GET shouldn't show up
        assert not any(e["method"] == "GET" for e in entries)

    def test_audit_log_feature_flag_blocks_regular_user(self, auth_client):
        """When view_audit_log is flipped to admin_only, a regular user
        gets 403. When it's open (default), they only see their own rows."""
        # Default is open — tester sees their own activity (none yet)
        r = auth_client.get("/api/audit_log")
        assert r.status_code == 200

        # Flip the flag via the service (feature_flags module)
        from backend.services import feature_flags
        feature_flags.set_admin_only("view_audit_log", True)
        try:
            r = auth_client.get("/api/audit_log")
            assert r.status_code == 403
        finally:
            feature_flags.set_admin_only("view_audit_log", False)

    def test_rag_ingest_and_search_via_api(self, admin_client):
        """Full API-level roundtrip: create RAG source → ingest text → search.
        Mocks Bedrock embed so the test runs offline."""
        from unittest.mock import patch

        def fake_embed(text):
            from backend.services import rag
            # simple bag-of-keywords so 'foo' query hits 'foo' chunk
            vec = [0.0] * rag.EMBED_DIM
            lower = text.lower()
            for i, kw in enumerate(["foo", "bar", "baz"]):
                vec[i] = float(lower.count(kw))
            m = sum(v * v for v in vec) ** 0.5
            if m > 0:
                vec = [v / m for v in vec]
            vec[-1] = 0.001
            return vec

        r = admin_client.post("/api/assets", json={
            "kind": "rag", "name": "test-kb",
            "config": {"backend": "pgvector"},
        })
        aid = r.get_json()["id"]

        with patch("backend.services.rag.embed_one", side_effect=fake_embed):
            r = admin_client.post(f"/api/assets/{aid}/rag/ingest", json={
                "source_name": "doc1",
                "text": "The foo topic explains foo completely.\n\nBar is separate.",
            })
            assert r.status_code == 200
            assert r.get_json()["chunks_ingested"] >= 1

            r = admin_client.post(f"/api/assets/{aid}/rag/search", json={
                "query": "tell me about foo",
                "top_k": 3,
            })
            assert r.status_code == 200
            hits = r.get_json()["hits"]
            assert len(hits) >= 1
            assert "foo" in hits[0]["content"].lower()
