"""Workflow listing. No run dispatch in regression (that would cost Bedrock)."""
from __future__ import annotations


def test_workflows_list_empty(test_user, holons_url):
    r = test_user["session"].get(f"{holons_url}/api/workflows")
    assert r.status_code == 200
    assert r.json() == []


def test_schedules_list_empty(test_user, holons_url):
    r = test_user["session"].get(f"{holons_url}/api/schedules")
    assert r.status_code == 200
    assert r.json() == []


def test_runs_list_empty(test_user, holons_url):
    r = test_user["session"].get(f"{holons_url}/api/runs")
    assert r.status_code == 200
    body = r.json()
    assert body["runs"] == []
    assert body["has_more"] is False
