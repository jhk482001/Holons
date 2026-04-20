"""Live-DB regression test fixtures.

Unlike the existing `tests/test_*.py` suite (which TRUNCATEs tables
between runs and would wipe real data), this suite runs **against the
live backend on localhost:8087** and touches only one throwaway user
per run — so jay, molly, and admin data stay untouched.

Each test run:
  1. POST /api/register to create `qa_test_<epoch>_<pid>`
  2. Yields a logged-in requests.Session
  3. Teardown: cascade-delete every row owned by that user via DB

Env:
  HOLONS_BACKEND_URL     — override base URL (default http://localhost:8087)
  HOLONS_REGRESSION_DB_URL — direct DB URL for teardown
                              (default from backend env)
"""
from __future__ import annotations

import os
import random
import sys
import time

import pytest
import requests


BASE_URL = os.environ.get("HOLONS_BACKEND_URL", "http://localhost:8087")


def _backend_up() -> bool:
    try:
        r = requests.get(f"{BASE_URL}/api/me", timeout=2)
        return r.status_code in (200, 401)
    except Exception:
        return False


@pytest.fixture(scope="session", autouse=True)
def _require_backend():
    if not _backend_up():
        pytest.exit(
            f"Backend is not reachable at {BASE_URL}. Start it first:\n"
            "  DB_BACKEND=postgres DATABASE_URL=... python3 -m backend.app",
            returncode=2,
        )


# ----------------------------------------------------------------------
# Direct DB handle for teardown (we bypass the API to cascade-delete a
# user because there's no DELETE /api/me endpoint and we want the
# cleanup to never 404 on a half-created user).
# ----------------------------------------------------------------------
def _db_handle():
    # Re-use the backend's own DB helper — same DATABASE_URL env.
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
    from backend import db as _db  # noqa: E402
    return _db


def _cascade_delete_user(uid: int) -> None:
    """Best-effort: scrub every table that FKs to user_id. Silent on
    missing tables (older schemas) so tests stay portable."""
    db = _db_handle()
    statements = [
        "DELETE FROM im_bindings WHERE user_id = %s",
        "DELETE FROM lead_messages WHERE thread_id IN "
        "  (SELECT thread_id FROM lead_conversations WHERE user_id = %s)",
        "DELETE FROM lead_conversations WHERE user_id = %s",
        "DELETE FROM schedules WHERE user_id = %s",
        "DELETE FROM run_steps WHERE run_id IN (SELECT id FROM runs WHERE user_id = %s)",
        "DELETE FROM runs WHERE user_id = %s",
        "DELETE FROM workflow_nodes WHERE workflow_id IN "
        "  (SELECT id FROM workflows WHERE user_id = %s)",
        "DELETE FROM workflows WHERE user_id = %s",
        "DELETE FROM project_artifacts WHERE project_id IN "
        "  (SELECT id FROM projects WHERE user_id = %s)",
        "DELETE FROM project_events WHERE project_id IN "
        "  (SELECT id FROM projects WHERE user_id = %s)",
        "DELETE FROM project_milestones WHERE project_id IN "
        "  (SELECT id FROM projects WHERE user_id = %s)",
        "DELETE FROM project_reports WHERE project_id IN "
        "  (SELECT id FROM projects WHERE user_id = %s)",
        "DELETE FROM project_members WHERE project_id IN "
        "  (SELECT id FROM projects WHERE user_id = %s)",
        "DELETE FROM projects WHERE user_id = %s",
        "DELETE FROM agent_mcp_servers WHERE agent_id IN "
        "  (SELECT id FROM agents WHERE user_id = %s)",
        "DELETE FROM agent_assets WHERE agent_id IN "
        "  (SELECT id FROM agents WHERE user_id = %s)",
        "DELETE FROM agent_tasks WHERE agent_id IN "
        "  (SELECT id FROM agents WHERE user_id = %s)",
        "DELETE FROM asset_items WHERE owner_user_id = %s",
        "DELETE FROM agents WHERE user_id = %s",
        "DELETE FROM as_users WHERE id = %s",
    ]
    for sql in statements:
        try:
            db.execute(sql, (uid,))
        except Exception:  # noqa: BLE001
            # If a table doesn't exist or a row's FK doesn't resolve,
            # keep going. The last statement (delete from as_users) is
            # the one that actually must succeed.
            pass


# ======================================================================
# Fixtures
# ======================================================================

@pytest.fixture
def test_user():
    """Fresh throwaway user per test. Yields a dict with id, username,
    password, and an authenticated requests.Session keyed to it."""
    stamp = f"qa_{int(time.time())}_{random.randint(1000, 9999)}"
    user = {
        "username": stamp,
        "password": f"{stamp}-pw",
        "display_name": "QA Tester",
    }
    session = requests.Session()
    # Register also logs in, setting the session cookie
    r = session.post(f"{BASE_URL}/api/register", json=user, timeout=10)
    r.raise_for_status()
    user["id"] = r.json()["id"]
    user["session"] = session

    try:
        yield user
    finally:
        try:
            _cascade_delete_user(user["id"])
        except Exception as e:
            print(f"[conftest] cleanup warning for user {user['id']}: {e}")


@pytest.fixture
def holons_url():
    """Base URL for the Holons backend under test. Named `holons_url`
    (not `base_url`) to avoid colliding with the pytest_base_url plugin
    that auto-registers a session-scoped fixture with that name."""
    return BASE_URL
