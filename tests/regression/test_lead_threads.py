"""Lead thread + messages endpoints.

Explicitly does NOT call POST /api/lead/chat — that would spend Bedrock
credits. We verify only that the listing and pending_count endpoints
work for a fresh user.
"""
from __future__ import annotations


def test_lead_threads_empty(test_user, holons_url):
    r = test_user["session"].get(f"{holons_url}/api/lead/threads")
    assert r.status_code == 200
    assert r.json() == []


def test_lead_pending_count(test_user, holons_url):
    r = test_user["session"].get(f"{holons_url}/api/lead/pending_count")
    assert r.status_code == 200
    assert r.json() == {"count": 0}
