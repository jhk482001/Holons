"""Search — threads/runs/reports over a user's own data."""
from __future__ import annotations


def test_search_empty_query(test_user, holons_url):
    # Empty query — the endpoint expects q; handler returns empty if missing
    r = test_user["session"].get(f"{holons_url}/api/search?q=")
    # Accept either 200 with empty lists or 400 depending on policy
    assert r.status_code in (200, 400)


def test_search_no_matches_for_fresh_user(test_user, holons_url):
    r = test_user["session"].get(f"{holons_url}/api/search?q=anything")
    assert r.status_code == 200
    body = r.json()
    assert body["threads"] == []
    assert body["runs"] == []
    assert body["reports"] == []
