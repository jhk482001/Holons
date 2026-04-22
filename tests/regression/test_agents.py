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


def test_agent_create_and_update_fallback_model(test_user, holons_url):
    """Both create and update paths must accept fallback_model_id.
    Agent LLM invocations retry on this model when the primary returns
    a transient error."""
    s = test_user["session"]
    r = s.post(f"{holons_url}/api/agents", json={
        "name": "FallbackBot",
        "role_title": "Probe",
        "system_prompt": "p",
        "avatar_config": {"body": "Shirt", "face": "Calm", "hair": "Short"},
        "primary_model_id": "jp.anthropic.claude-sonnet-4-6",
        "fallback_model_id": "jp.anthropic.claude-haiku-4-5-20251001-v1:0",
    })
    assert r.status_code in (200, 201), r.text
    aid = r.json()["id"]

    r = s.get(f"{holons_url}/api/agents/{aid}")
    assert r.json()["fallback_model_id"] == "jp.anthropic.claude-haiku-4-5-20251001-v1:0"

    r = s.put(f"{holons_url}/api/agents/{aid}", json={"fallback_model_id": None})
    assert r.status_code == 200
    r = s.get(f"{holons_url}/api/agents/{aid}")
    assert r.json()["fallback_model_id"] is None


def test_agent_create_persists_tool_config(test_user, holons_url):
    """POST /api/agents must persist tool_config — pre-tier1 the POST
    endpoint quietly dropped it, so brand-new agents always had an
    empty tool set and the LLM couldn't call file_write / run_code."""
    s = test_user["session"]
    r = s.post(f"{holons_url}/api/agents", json={
        "name": "ToolBot",
        "role_title": "Probe",
        "system_prompt": "p",
        "avatar_config": {"body": "Shirt", "face": "Calm", "hair": "Short"},
        "primary_model_id": "jp.anthropic.claude-haiku-4-5-20251001-v1:0",
        "tool_config": ["file_write", "run_code"],
    })
    assert r.status_code in (200, 201), r.text
    aid = r.json()["id"]

    r = s.get(f"{holons_url}/api/agents/{aid}")
    assert r.status_code == 200
    tc = r.json().get("tool_config") or []
    assert "file_write" in tc
    assert "run_code" in tc
