"""Authentication flow — me, login, logout."""
from __future__ import annotations


def test_me_authenticated(test_user, holons_url):
    s = test_user["session"]
    r = s.get(f"{holons_url}/api/me")
    assert r.status_code == 200
    body = r.json()
    assert body["authenticated"] is True
    assert body["username"] == test_user["username"]


def test_login_logout_cycle(test_user, holons_url):
    s = test_user["session"]
    # Logout via POST
    r = s.post(f"{holons_url}/api/logout")
    assert r.status_code == 200
    # Subsequent /api/me sees unauthenticated
    r = s.get(f"{holons_url}/api/me")
    assert r.status_code == 200
    assert r.json()["authenticated"] is False
    # Login again
    r = s.post(f"{holons_url}/api/login", json={
        "username": test_user["username"], "password": test_user["password"],
    })
    assert r.status_code == 200
    assert r.json()["id"] == test_user["id"]


def test_bad_password_rejected(test_user, holons_url):
    import requests
    s = requests.Session()
    r = s.post(f"{holons_url}/api/login", json={
        "username": test_user["username"], "password": "nope-not-the-password",
    })
    assert r.status_code == 401
