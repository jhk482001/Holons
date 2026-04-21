"""Schedules — create / list / toggle / delete + project_id wiring (Bug #1)."""
from __future__ import annotations


def test_schedules_list_empty(test_user, holons_url):
    r = test_user["session"].get(f"{holons_url}/api/schedules")
    assert r.status_code == 200
    assert r.json() == []


def test_schedule_create_with_project_id_roundtrip(test_user, holons_url):
    """Bug #1 fix: create_schedule must persist project_id so dispatched
    runs attribute their spend to the project (scheduler._tick passes it
    to dispatch_workflow)."""
    s = test_user["session"]

    # Minimal workflow to attach the schedule to.
    r = s.post(f"{holons_url}/api/workflows", json={
        "name": "sched-proj-wf",
        "description": "fixture",
        "nodes": [],
    })
    assert r.status_code in (200, 201), r.text
    wf_id = r.json()["id"]

    # Project for the schedule to attribute to.
    r = s.post(f"{holons_url}/api/projects", json={
        "name": "Sched-target",
        "goal": "Hold spend from a scheduled workflow.",
    })
    assert r.status_code in (200, 201), r.text
    pid = r.json()["id"]

    # Create schedule with project_id
    r = s.post(f"{holons_url}/api/schedules", json={
        "workflow_id": wf_id,
        "project_id": pid,
        "name": "hourly-attrib",
        "trigger_type": "interval",
        "interval_seconds": 3600,
        "priority": "normal",
    })
    assert r.status_code in (200, 201), r.text
    sid = r.json()["id"]

    # Verify project_id round-trips through the list endpoint.
    r = s.get(f"{holons_url}/api/schedules")
    assert r.status_code == 200
    mine = [row for row in r.json() if row["id"] == sid]
    assert mine, "schedule not in list"
    assert mine[0].get("project_id") == pid

    # Disable to avoid any chance of firing during the test window.
    r = s.post(f"{holons_url}/api/schedules/{sid}/toggle", json={"enabled": False})
    assert r.status_code == 200

    # Cleanup
    r = s.delete(f"{holons_url}/api/schedules/{sid}")
    assert r.status_code == 200


def test_schedule_without_project_id_still_works(test_user, holons_url):
    """Ad-hoc schedules (no project_id) remain supported."""
    s = test_user["session"]
    r = s.post(f"{holons_url}/api/workflows", json={
        "name": "sched-adhoc-wf", "description": "fixture", "nodes": [],
    })
    wf_id = r.json()["id"]

    r = s.post(f"{holons_url}/api/schedules", json={
        "workflow_id": wf_id,
        "name": "adhoc",
        "trigger_type": "interval",
        "interval_seconds": 3600,
    })
    assert r.status_code in (200, 201), r.text
    sid = r.json()["id"]
    r = s.get(f"{holons_url}/api/schedules")
    mine = [row for row in r.json() if row["id"] == sid]
    assert mine and mine[0].get("project_id") in (None, 0)
    s.delete(f"{holons_url}/api/schedules/{sid}")
