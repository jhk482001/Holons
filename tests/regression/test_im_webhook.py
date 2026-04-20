"""Webhook endpoint regression tests.

We can't easily test the full setWebhook / delete path without a real
bot token, so these tests focus on the HTTP contract:

  - Unknown secret → 404
  - Known secret + empty body → 200 (no error, nothing to dispatch)
  - Known secret + real-looking Telegram update → 200, and a reply is
    attempted (we can't assert it landed without a real chat, but the
    path runs without error)

To populate a binding + secret without a real Telegram token, the
fixture writes directly to the DB via the backend's db helper.
"""
from __future__ import annotations

import os
import secrets as _secrets
import sys

import pytest
import requests

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, REPO)


@pytest.fixture
def webhook_binding(test_user):
    """Insert a fake webhook-mode binding directly into the DB for the
    throwaway test user, and clean it up after."""
    from backend import db
    from backend.services import asset_crypto

    secret = _secrets.token_urlsafe(16)
    enc_token = asset_crypto.encrypt("fake-bot-token-for-test")
    import json as _json
    bid = db.execute_returning(
        """INSERT INTO im_bindings
             (user_id, platform, secret_encrypted, enabled, transport, metadata)
           VALUES (%s, 'telegram', %s, TRUE, 'webhook', %s::jsonb)
           RETURNING id""",
        (test_user["id"], enc_token, _json.dumps({"webhook_secret": secret})),
    )
    yield {"id": bid, "secret": secret}
    # cascade_delete_user in conftest.py will wipe the binding


def test_webhook_unknown_secret_returns_404(holons_url):
    r = requests.post(
        f"{holons_url}/api/im/webhook/telegram/unknown-secret",
        json={"update_id": 1, "message": {"text": "hi"}},
        timeout=5,
    )
    assert r.status_code == 404


def test_webhook_wrong_platform_returns_404(holons_url, webhook_binding):
    r = requests.post(
        f"{holons_url}/api/im/webhook/slack/{webhook_binding['secret']}",
        json={},
        timeout=5,
    )
    assert r.status_code == 404


def test_webhook_empty_body_returns_200(holons_url, webhook_binding):
    """A matching secret with no payload is a legitimate Telegram ping.
    Must 200 regardless so Telegram doesn't retry-spam."""
    r = requests.post(
        f"{holons_url}/api/im/webhook/telegram/{webhook_binding['secret']}",
        json={},
        timeout=5,
    )
    assert r.status_code == 200


def test_webhook_non_text_update_returns_200(holons_url, webhook_binding):
    """A photo-only message can't be dispatched but the webhook must
    still 200 — otherwise Telegram retries forever."""
    r = requests.post(
        f"{holons_url}/api/im/webhook/telegram/{webhook_binding['secret']}",
        json={"update_id": 1, "message": {
            "chat": {"id": 1}, "photo": [{"file_id": "x"}],
        }},
        timeout=5,
    )
    assert r.status_code == 200


def test_transport_switch_validates_https(test_user, holons_url, webhook_binding):
    """Switching to webhook mode must reject http:// URLs."""
    r = test_user["session"].post(
        f"{holons_url}/api/im/bindings/{webhook_binding['id']}/transport",
        json={"transport": "webhook", "public_url": "http://insecure.example"},
    )
    assert r.status_code == 400
    assert "https" in r.json()["error"].lower()


def test_transport_switch_invalid_mode_rejected(test_user, holons_url, webhook_binding):
    r = test_user["session"].post(
        f"{holons_url}/api/im/bindings/{webhook_binding['id']}/transport",
        json={"transport": "carrier-pigeon"},
    )
    assert r.status_code == 400


def test_transport_switch_not_found_for_other_user(test_user, holons_url):
    r = test_user["session"].post(
        f"{holons_url}/api/im/bindings/999999/transport",
        json={"transport": "polling"},
    )
    assert r.status_code == 404
