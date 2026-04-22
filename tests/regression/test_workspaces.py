"""Regression tests for Tier-1 workspace + file tools + code exec.

Exercises the public API only. Heavy scenarios (agent-driven workflow
that actually writes files via file_write) are covered in a separate
scenario_test script rather than here, because they need a live LLM
call.
"""
from __future__ import annotations


def test_workspace_crud(test_user, holons_url):
    s = test_user["session"]

    # List: empty
    r = s.get(f"{holons_url}/api/workspaces")
    assert r.status_code == 200
    assert r.json() == []

    # Create
    r = s.post(f"{holons_url}/api/workspaces", json={"name": "probe", "description": "regression"})
    assert r.status_code == 200, r.text
    ws = r.json()
    assert ws["name"] == "probe"
    assert ws["size_bytes"] == 0
    wid = ws["id"]

    # Get
    r = s.get(f"{holons_url}/api/workspaces/{wid}")
    assert r.status_code == 200
    assert r.json()["id"] == wid

    # Empty tree
    r = s.get(f"{holons_url}/api/workspaces/{wid}/files")
    assert r.status_code == 200
    assert r.json() == {"files": []}

    # Download zip (empty is valid)
    r = s.get(f"{holons_url}/api/workspaces/{wid}/download.zip")
    assert r.status_code == 200
    assert r.headers["Content-Type"] == "application/zip"

    # Delete
    r = s.delete(f"{holons_url}/api/workspaces/{wid}")
    assert r.status_code == 200
    assert r.json() == {"ok": True}

    # Get after delete → 404
    r = s.get(f"{holons_url}/api/workspaces/{wid}")
    assert r.status_code == 404


def test_workspace_file_roundtrip(test_user, holons_url):
    """Write via the service layer (simulating an agent tool call),
    then verify the UI endpoints see the file."""
    from backend.services import workspaces as ws_svc

    s = test_user["session"]
    r = s.post(f"{holons_url}/api/workspaces", json={"name": "rt"})
    ws = r.json()

    ws_svc.write_file(ws, "hello.txt", "hi\n")
    ws_svc.write_file(ws, "src/app.py", "print('x')\n")

    r = s.get(f"{holons_url}/api/workspaces/{ws['id']}/files")
    paths = [f["path"] for f in r.json()["files"] if not f["is_dir"]]
    assert "hello.txt" in paths
    assert "src/app.py" in paths

    r = s.get(f"{holons_url}/api/workspaces/{ws['id']}/files/hello.txt")
    assert r.json()["content"] == "hi\n"


def test_workspace_path_escape_rejected(test_user, holons_url):
    """Any path that realpaths outside the workspace root must be
    rejected by the service — never allow ../../etc/passwd writes."""
    from backend.services import workspaces as ws_svc

    s = test_user["session"]
    r = s.post(f"{holons_url}/api/workspaces", json={"name": "escape"})
    ws = r.json()
    try:
        ws_svc.write_file(ws, "../escape.txt", "no")
        assert False, "path escape should have raised"
    except ws_svc.PathEscape:
        pass


def test_me_exposes_code_exec_toggle(test_user, holons_url):
    s = test_user["session"]
    r = s.get(f"{holons_url}/api/me")
    assert r.status_code == 200
    body = r.json()
    # Default is FALSE for security.
    assert body.get("enable_code_execution") in (False, None)

    r = s.put(f"{holons_url}/api/me", json={"enable_code_execution": True})
    assert r.status_code == 200
    r = s.get(f"{holons_url}/api/me")
    assert r.json().get("enable_code_execution") is True


def test_run_code_refused_when_user_toggle_off(test_user, holons_url):
    """If the user hasn't opted in, run_code returns an error even with
    a bound workspace. This is the safety gate from the tool handler."""
    from backend.tools import run_code

    s = test_user["session"]
    r = s.post(f"{holons_url}/api/workspaces", json={"name": "cx-off"})
    ws = r.json()

    # Register an agent for this test user so agent_user_id resolves
    r = s.post(f"{holons_url}/api/agents", json={
        "name": "CodeProbe", "role_title": "probe",
        "system_prompt": "p",
        "avatar_config": {"body": "Shirt", "face": "Calm", "hair": "Short"},
        "primary_model_id": "jp.anthropic.claude-haiku-4-5-20251001-v1:0",
    })
    aid = r.json()["id"]

    out = run_code.handler(
        {"lang": "python", "code": "print(1)"},
        {"agent_id": aid, "agent_user_id": test_user["id"],
         "run_id": 0, "task_id": 0,
         "payload": {"workspace_id": ws["id"]},
         "workspace_id": ws["id"]},
    )
    assert out["ok"] is False
    assert "disabled" in (out.get("error") or "").lower()


def test_run_code_runs_when_enabled(test_user, holons_url):
    """Flip the toggle ON and the runner executes."""
    from backend.tools import run_code

    s = test_user["session"]
    s.put(f"{holons_url}/api/me", json={"enable_code_execution": True})

    r = s.post(f"{holons_url}/api/workspaces", json={"name": "cx-on"})
    ws = r.json()

    r = s.post(f"{holons_url}/api/agents", json={
        "name": "CodeProbe2", "role_title": "probe",
        "system_prompt": "p",
        "avatar_config": {"body": "Shirt", "face": "Calm", "hair": "Short"},
        "primary_model_id": "jp.anthropic.claude-haiku-4-5-20251001-v1:0",
    })
    aid = r.json()["id"]

    out = run_code.handler(
        {"lang": "python", "code": "print('ok', 2+2)"},
        {"agent_id": aid, "agent_user_id": test_user["id"],
         "run_id": 0, "task_id": 0,
         "payload": {"workspace_id": ws["id"]},
         "workspace_id": ws["id"]},
    )
    assert out["ok"] is True, out
    assert "ok 4" in out["stdout"]


def test_tool_registry_exposes_file_and_run_code(test_user, holons_url):
    """file_write / file_read / file_list / file_glob / file_delete /
    run_code all show up in the tool registry so the workflow editor
    can offer them."""
    from backend.tools import all_tool_names
    names = all_tool_names()
    for expected in ("file_write", "file_read", "file_list", "file_glob",
                      "file_delete", "run_code"):
        assert expected in names, f"{expected} missing from tool registry"
