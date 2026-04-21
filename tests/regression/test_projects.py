"""Project endpoints — list / get / artifacts / outputs / members."""
from __future__ import annotations


def test_projects_list_empty(test_user, holons_url):
    r = test_user["session"].get(f"{holons_url}/api/projects")
    assert r.status_code == 200
    assert r.json() == []


def test_project_get_not_found(test_user, holons_url):
    # Nonexistent project for this user → 404
    r = test_user["session"].get(f"{holons_url}/api/projects/999999")
    assert r.status_code == 404


def test_project_create_and_lookup(test_user, holons_url):
    s = test_user["session"]
    r = s.post(f"{holons_url}/api/projects", json={
        "name": "Regression Project",
        "goal": "Test that the project endpoints work.",
        "description": "Ephemeral test fixture.",
    })
    assert r.status_code in (200, 201), r.text
    pid = r.json()["id"]

    r = s.get(f"{holons_url}/api/projects/{pid}")
    assert r.status_code == 200
    assert r.json()["name"] == "Regression Project"

    # Endpoints that hang off a project
    for path in ("milestones", "reports", "events", "outputs", "artifacts"):
        r = s.get(f"{holons_url}/api/projects/{pid}/{path}")
        assert r.status_code == 200, f"{path}: {r.text}"


def test_project_with_coordinator_adds_coord_to_members(test_user, holons_url):
    """Bug #4 fix: creating a project with coordinator_agent_id must
    automatically enrol the coordinator in project_members so the quota
    middleware doesn't reject the coordinator's own dispatches."""
    s = test_user["session"]

    # Hire a cheap agent first
    r = s.post(f"{holons_url}/api/agents", json={
        "name": "CoordBot",
        "role_title": "Coord",
        "system_prompt": "Coord.",
        "avatar_config": {"body": "Shirt", "face": "Calm", "hair": "Short"},
        "primary_model_id": "jp.anthropic.claude-haiku-4-5-20251001-v1:0",
    })
    assert r.status_code in (200, 201), r.text
    coord_id = r.json()["id"]

    # Create a project with coord but no members in the payload
    r = s.post(f"{holons_url}/api/projects", json={
        "name": "Coord-autoadd",
        "goal": "Verify coord auto-enrols.",
        "coordinator_agent_id": coord_id,
    })
    assert r.status_code in (200, 201), r.text
    pid = r.json()["id"]

    # Project detail should include the coord as a member
    r = s.get(f"{holons_url}/api/projects/{pid}")
    assert r.status_code == 200
    member_ids = [m["agent_id"] for m in (r.json().get("members") or [])]
    assert coord_id in member_ids, f"coord {coord_id} missing from members {member_ids}"
