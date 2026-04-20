"""IM binding CRUD — Telegram focus, but shape is channel-agnostic."""
from __future__ import annotations


def test_bindings_empty_initially(test_user, holons_url):
    r = test_user["session"].get(f"{holons_url}/api/im/bindings")
    assert r.status_code == 200
    assert r.json() == []


def test_unsupported_platform_rejected(test_user, holons_url):
    r = test_user["session"].post(f"{holons_url}/api/im/bindings", json={
        "platform": "fax-machine", "token": "whatever",
    })
    assert r.status_code == 400
    assert "unsupported" in r.json()["error"].lower()


def test_supported_platforms_accept_the_post(test_user, holons_url):
    """The three supported platforms must at least reach the token-verify
    step. Bad tokens fail with 400 'token rejected' — that's expected."""
    for platform in ("telegram", "slack", "line"):
        r = test_user["session"].post(f"{holons_url}/api/im/bindings", json={
            "platform": platform, "token": "obviously-bad-token",
        })
        assert r.status_code == 400, f"{platform}: {r.text}"
        msg = r.json()["error"].lower()
        # Two shapes of error are fine: 'unsupported' must NOT appear;
        # either 'token rejected' or a platform-specific 'HTTP 401/404' is OK.
        assert "unsupported" not in msg
        assert "token" in msg or "http" in msg


def test_empty_token_rejected(test_user, holons_url):
    r = test_user["session"].post(f"{holons_url}/api/im/bindings", json={
        "platform": "telegram", "token": "",
    })
    assert r.status_code == 400


def test_bad_telegram_token_rejected(test_user, holons_url):
    # Telegram's getMe returns 401/404 for a garbage token; the API layer
    # must translate that into a 400 with a clear message before persisting.
    r = test_user["session"].post(f"{holons_url}/api/im/bindings", json={
        "platform": "telegram", "token": "not-a-real-bot-token",
    })
    assert r.status_code == 400
    err = r.json()["error"]
    assert "token rejected" in err or "not found" in err.lower()

    # Confirm nothing was persisted on rejection
    r = test_user["session"].get(f"{holons_url}/api/im/bindings")
    assert r.json() == []


def test_delete_nonexistent_binding(test_user, holons_url):
    r = test_user["session"].delete(f"{holons_url}/api/im/bindings/999999")
    assert r.status_code == 404


def test_toggle_nonexistent_binding(test_user, holons_url):
    r = test_user["session"].post(f"{holons_url}/api/im/bindings/999999/toggle")
    assert r.status_code == 404
