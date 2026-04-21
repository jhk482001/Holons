"""Backup endpoint — admin-only, returns SQLite info + downloadable .db
file. Non-admin users get 403 on both /info and /download.
"""
from __future__ import annotations
import requests


def test_backup_info_requires_admin(test_user, holons_url):
    """Fresh registered users are role='user' — backup is admin-only."""
    r = test_user["session"].get(f"{holons_url}/api/backup/info")
    assert r.status_code == 403


def test_backup_download_requires_admin(test_user, holons_url):
    r = test_user["session"].get(f"{holons_url}/api/backup/download",
                                  allow_redirects=False)
    assert r.status_code == 403


def test_backup_info_and_download_as_admin(holons_url):
    """We log in as the seeded admin to check the happy path. No teardown
    since admin data is off-limits anyway."""
    s = requests.Session()
    r = s.post(f"{holons_url}/api/login",
               json={"username": "admin", "password": "admin"}, timeout=10)
    if r.status_code != 200:
        # Admin creds unknown on this deployment — skip cleanly.
        import pytest
        pytest.skip("admin login unavailable")
    r = s.get(f"{holons_url}/api/backup/info")
    assert r.status_code == 200
    body = r.json()
    assert "backend" in body
    if body["backend"] == "postgres":
        # Postgres deployments return 501 on download (use pg_dump instead).
        assert body["exportable"] is False
        r = s.get(f"{holons_url}/api/backup/download", allow_redirects=False)
        assert r.status_code == 501
    else:
        # SQLite: download should return a real binary .db payload.
        assert body["exportable"] is True
        r = s.get(f"{holons_url}/api/backup/download", stream=True, timeout=30)
        assert r.status_code == 200
        assert r.headers.get("Content-Type") in (
            "application/x-sqlite3", "application/octet-stream",
        )
        # SQLite files start with the magic header "SQLite format 3\x00"
        first_chunk = next(r.iter_content(chunk_size=16))
        assert first_chunk.startswith(b"SQLite format 3")
