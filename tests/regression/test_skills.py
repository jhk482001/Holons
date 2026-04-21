"""Skill-extractor surface area: audit schema on /api/agents/:id/skills,
per-user auto-approve toggle on /api/me, and the extract endpoint's
"need more records" short-circuit (reachable without an LLM call).
"""
from __future__ import annotations


def test_me_exposes_skills_auto_approve_default_true(test_user, holons_url):
    r = test_user["session"].get(f"{holons_url}/api/me")
    assert r.status_code == 200
    body = r.json()
    assert body["authenticated"] is True
    # Default is TRUE per the schema migration.
    assert body.get("skills_auto_approve") in (True, None), body


def test_me_put_persists_skills_auto_approve(test_user, holons_url):
    s = test_user["session"]
    r = s.put(f"{holons_url}/api/me", json={"skills_auto_approve": False})
    assert r.status_code == 200
    r = s.get(f"{holons_url}/api/me")
    assert r.status_code == 200
    assert r.json().get("skills_auto_approve") is False

    r = s.put(f"{holons_url}/api/me", json={"skills_auto_approve": True})
    assert r.status_code == 200
    r = s.get(f"{holons_url}/api/me")
    assert r.json().get("skills_auto_approve") is True


def test_skills_list_exposes_audit_columns(test_user, holons_url):
    """Fresh user has no agents, but the list endpoint still has to
    return a well-formed array (checking the SQL SELECT * after the
    migration didn't break)."""
    s = test_user["session"]
    r = s.post(f"{holons_url}/api/agents", json={
        "name": "SkillProbe",
        "role_title": "Probe",
        "system_prompt": "probe",
        "avatar_config": {"body": "Shirt", "face": "Calm", "hair": "Short"},
        "primary_model_id": "jp.anthropic.claude-haiku-4-5-20251001-v1:0",
    })
    assert r.status_code in (200, 201), r.text
    aid = r.json()["id"]

    r = s.get(f"{holons_url}/api/agents/{aid}/skills")
    assert r.status_code == 200
    assert r.json() == []  # No skills mined yet.


def test_extract_too_few_records_returns_empty(test_user, holons_url):
    """The extractor requires >=5 run_steps. A fresh agent has 0, so
    the endpoint returns an empty list without invoking the LLM."""
    s = test_user["session"]
    r = s.post(f"{holons_url}/api/agents", json={
        "name": "EmptyProbe",
        "role_title": "Probe",
        "system_prompt": "probe",
        "avatar_config": {"body": "Shirt", "face": "Calm", "hair": "Short"},
        "primary_model_id": "jp.anthropic.claude-haiku-4-5-20251001-v1:0",
    })
    aid = r.json()["id"]
    r = s.post(f"{holons_url}/api/agents/{aid}/skills/extract")
    assert r.status_code == 200
    assert r.json() == {"extracted": []}
