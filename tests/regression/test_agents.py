"""Agent CRUD — list, create, update, delete."""
from __future__ import annotations


def test_agents_list_empty_initially(test_user, holons_url):
    # Fresh users have no agents until they hire one.
    r = test_user["session"].get(f"{holons_url}/api/agents")
    assert r.status_code == 200
    assert r.json() == []


def test_agent_crud_cycle(test_user, holons_url):
    s = test_user["session"]
    create_payload = {
        "name": "RegressionBot",
        "role_title": "QA Tester",
        "description": "Touches things in tests.",
        "system_prompt": "You are a tester. Say 'ok'.",
        "avatar_config": {"body": "Shirt", "face": "Calm", "hair": "Short"},
        "primary_model_id": "jp.anthropic.claude-haiku-4-5-20251001-v1:0",
    }
    r = s.post(f"{holons_url}/api/agents", json=create_payload)
    assert r.status_code in (200, 201), r.text
    aid = r.json()["id"]

    # list now shows 1
    r = s.get(f"{holons_url}/api/agents")
    assert r.status_code == 200
    assert any(a["id"] == aid for a in r.json())

    # update (role_title change)
    r = s.put(f"{holons_url}/api/agents/{aid}", json={"role_title": "Updated Role"})
    assert r.status_code == 200

    # get detail verifies update landed
    r = s.get(f"{holons_url}/api/agents/{aid}")
    assert r.status_code == 200
    assert r.json()["role_title"] == "Updated Role"

    # delete
    r = s.delete(f"{holons_url}/api/agents/{aid}")
    assert r.status_code == 200

    # list empty again
    r = s.get(f"{holons_url}/api/agents")
    assert r.status_code == 200
    assert all(a["id"] != aid for a in r.json())
