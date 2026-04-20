"""Cast layout round-trip — hidden_agents, facing, etc."""
from __future__ import annotations


def test_cast_layout_empty_initially(test_user, holons_url):
    r = test_user["session"].get(f"{holons_url}/api/me/cast_layout")
    assert r.status_code == 200
    assert r.json() == {}


def test_cast_layout_round_trip(test_user, holons_url):
    s = test_user["session"]
    payload = {
        "hidden_agents": [11, 12],
        "facing": {"11": "left", "12": "right"},
    }
    r = s.put(f"{holons_url}/api/me/cast_layout", json=payload)
    assert r.status_code == 200

    r = s.get(f"{holons_url}/api/me/cast_layout")
    assert r.status_code == 200
    got = r.json()
    assert got.get("hidden_agents") == [11, 12]
    assert got.get("facing") == {"11": "left", "12": "right"}
