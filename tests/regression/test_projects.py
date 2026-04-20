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
