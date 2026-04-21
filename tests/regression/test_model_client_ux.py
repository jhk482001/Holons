"""Sample + test endpoints for model clients. Real LLM calls are
admin-only and use live Bedrock — we only exercise the rejection
paths + the sample endpoint with the throwaway user."""
from __future__ import annotations
import requests


def test_kind_sample_returns_example(test_user, holons_url):
    """Sample endpoint is login-required but not admin-only — any user
    filling out the create form can see a sample."""
    r = test_user["session"].get(f"{holons_url}/api/model_clients/kinds/bedrock/sample")
    assert r.status_code == 200
    body = r.json()
    assert body["kind"] == "bedrock"
    assert "access_key" in body["credential"]
    assert "models" in body["config"]


def test_kind_sample_unknown_kind_404(test_user, holons_url):
    r = test_user["session"].get(f"{holons_url}/api/model_clients/kinds/fax/sample")
    assert r.status_code == 404


def test_test_endpoint_requires_admin(test_user, holons_url):
    r = test_user["session"].post(f"{holons_url}/api/model_clients/1/test")
    assert r.status_code == 403


def test_test_endpoint_happy_path_as_admin(holons_url):
    """Log in as admin + run the test against whatever client id=1 is
    (seeded Bedrock default). Skipped if admin login unavailable."""
    import pytest
    s = requests.Session()
    r = s.post(f"{holons_url}/api/login",
               json={"username": "admin", "password": "admin"}, timeout=10)
    if r.status_code != 200:
        pytest.skip("admin login unavailable")
    r = s.post(f"{holons_url}/api/model_clients/1/test", timeout=30)
    assert r.status_code == 200
    body = r.json()
    assert "ok" in body
    assert "message" in body
    assert isinstance(body["latency_ms"], int)
    if body["ok"]:
        assert body["input_tokens"] > 0
        assert body["output_tokens"] > 0
        assert body["model"]


def test_test_endpoint_not_found_as_admin(holons_url):
    import pytest
    s = requests.Session()
    r = s.post(f"{holons_url}/api/login",
               json={"username": "admin", "password": "admin"}, timeout=10)
    if r.status_code != 200:
        pytest.skip("admin login unavailable")
    r = s.post(f"{holons_url}/api/model_clients/999999/test")
    assert r.status_code == 404
