"""Dashboard endpoints — summary, agent_load, load_heatmap, quota_overview."""
from __future__ import annotations


def test_dashboard_summary(test_user, holons_url):
    r = test_user["session"].get(f"{holons_url}/api/dashboard/summary")
    assert r.status_code == 200
    body = r.json()
    # Fresh user with no agents / runs / queue
    assert body["active_agents"] == 0
    assert body["total_queue_depth"] == 0
    assert body["today_cost_usd"] == 0.0
    assert body["today_runs"] == 0


def test_dashboard_agent_load(test_user, holons_url):
    r = test_user["session"].get(f"{holons_url}/api/dashboard/agent_load")
    assert r.status_code == 200
    # Fresh user: empty list
    assert r.json() == []


def test_dashboard_heatmap(test_user, holons_url):
    r = test_user["session"].get(f"{holons_url}/api/dashboard/load_heatmap?buckets=24")
    assert r.status_code == 200
    body = r.json()
    assert body["buckets"] == 24
    assert "agents" in body


def test_dashboard_quota_overview(test_user, holons_url):
    r = test_user["session"].get(f"{holons_url}/api/dashboard/quota_overview")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_usage_daily_all_group_by(test_user, holons_url):
    """Ensures every group_by key is accepted. Regression guard for the
    recent tab-switcher feature + the model_client addition."""
    s = test_user["session"]
    for gb in ["project", "agent", "group", "workflow", "model_client"]:
        r = s.get(f"{holons_url}/api/usage/daily?group_by={gb}&days=7")
        assert r.status_code == 200, f"{gb}: {r.text}"
        body = r.json()
        assert body["group_by"] == gb
        assert isinstance(body["rows"], list)

    # invalid group_by → 400
    r = s.get(f"{holons_url}/api/usage/daily?group_by=bogus&days=7")
    assert r.status_code == 400
