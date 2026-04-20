"""Flask app — v2 with queue-based execution.

This file exposes REST endpoints plus starts all agent workers on boot.
The v1 endpoints are preserved in spirit (same URLs) but they now dispatch
tasks into the queue and return immediately.
"""
from __future__ import annotations

import atexit
import json
import os
import secrets
import time
from functools import wraps
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory, session
from flask_cors import CORS
from werkzeug.security import check_password_hash, generate_password_hash

from . import db, engine, queue, worker
from .bedrock_client import list_models
from .config import PORT, SECRET_KEY, UPLOAD_FOLDER
from . import tools as tools_registry
from .services import (
    assets as assets_service, avatar, escalation, feature_flags, lead_agent,
    lead_proxy, notifications as notif_service, quotas, rag as rag_service,
    scheduler, sharing, skill_extractor, user_quotas,
)


STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

app = Flask(__name__, static_folder=str(STATIC_DIR), static_url_path="/static")
app.secret_key = SECRET_KEY
app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0

# CORS allow-list. Personal/desktop mode is same-origin via the Tauri fetch
# patch, so the default list only covers localhost dev servers. For a remote
# enterprise deployment, set FLASK_CORS_ORIGINS to a comma-separated list of
# trusted web-UI origins (e.g. "https://holons.company.com"). A wildcard
# without explicit origins + supports_credentials=True is a CSRF risk.
_cors_env = os.environ.get("FLASK_CORS_ORIGINS", "").strip()
_default_cors = [
    "http://localhost:1420", "http://127.0.0.1:1420",       # Vite dev proxy
    "http://localhost:5173", "http://127.0.0.1:5173",       # Vite default
    "tauri://localhost",                                     # desktop webview
]
_cors_origins = (
    [o.strip() for o in _cors_env.split(",") if o.strip()]
    if _cors_env else _default_cors
)
CORS(app, origins=_cors_origins, supports_credentials=True)

# ============================================================================
# Structured logging — JSON format with request_id
# ============================================================================

import uuid as _uuid
import logging as _logging

class _JSONFormatter(_logging.Formatter):
    def format(self, record):
        try:
            import json as _j
            entry = {
                "ts": self.formatTime(record, self.datefmt),
                "level": record.levelname,
                "logger": record.name,
                "msg": record.getMessage(),
            }
            if hasattr(record, "request_id"):
                entry["request_id"] = record.request_id
            if record.exc_info and record.exc_info[1]:
                entry["error"] = str(record.exc_info[1])
            return _j.dumps(entry, ensure_ascii=False)
        except Exception:
            return f"{record.levelname} {record.name}: {record.getMessage()}"

# Only apply JSON formatting when running as the main app, not during tests
import sys as _sys
if "pytest" not in _sys.modules:
    _handler = _logging.StreamHandler()
    _handler.setFormatter(_JSONFormatter())
    _logging.root.handlers = [_handler]
    _logging.root.setLevel(_logging.INFO)

log = _logging.getLogger("agent_company")

@app.before_request
def _inject_request_id():
    from flask import g
    g.request_id = request.headers.get("X-Request-ID") or _uuid.uuid4().hex[:12]

@app.after_request
def _add_request_id_header(response):
    from flask import g
    rid = getattr(g, "request_id", None)
    if rid:
        response.headers["X-Request-ID"] = rid
    return response


# ============================================================================
# Startup: init DB, apply schema, start workers
# ============================================================================

@app.errorhandler(Exception)
def _handle_unhandled(e):
    """Catch-all: log the full traceback as structured JSON, then return 500."""
    import traceback
    log.error(
        "Unhandled exception: %s\n%s",
        str(e),
        traceback.format_exc(),
    )
    return jsonify({"error": "internal server error"}), 500


def _startup() -> None:
    db.init()  # schema.create_all() seeds feature_flags for us
    worker.registry().start_all_active()
    scheduler.start()
    # IM channel pollers — one thread per enabled im_bindings row.
    # Skippable via env flag for local dev or tests.
    if os.environ.get("HOLONS_DISABLE_IM") != "1":
        from .services import im_channels
        im_channels.start_all()


# ============================================================================
# Phase 5.3 — Audit log middleware
# ============================================================================

# HTTP methods that land in the audit table. Read-only GETs would flood
# the table (notifications poll every 15s, etc.) so they're excluded by
# default. Mutating methods are always audited.
_AUDIT_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

# Paths to exclude even when the method is a POST — e.g. login itself,
# which we don't want to log (contains credentials in the body) or health
# checks. Login success is still visible via last_seen_at anyway.
_AUDIT_SKIP_PREFIXES = (
    "/api/login",
    "/api/logout/desktop",
    "/api/register",
    "/api/me",  # polled frequently for auth status
    "/api/avatar/",
    "/api/health",
    "/api/notifications/unread_count",
    "/api/im/webhook/",  # high-volume push from IM platforms
)


def _should_audit(path: str, method: str) -> bool:
    if method not in _AUDIT_METHODS:
        return False
    for prefix in _AUDIT_SKIP_PREFIXES:
        if path.startswith(prefix):
            return False
    return True


@app.after_request
def _audit_request(response):
    """Record every mutating authenticated API call into audit_log.
    Runs in after_request so we have the final status code to log.

    Failures writing to the audit table are swallowed — an audit
    regression must never break the request path.
    """
    try:
        path = request.path or ""
        if not path.startswith("/api/"):
            return response
        method = request.method or ""
        if not _should_audit(path, method):
            return response
        uid = session.get("user_id")
        if not uid:
            return response
        # Best-effort resource id extraction from URL: last integer path
        # segment if present. Not perfect but good enough for filters.
        resource_id = None
        for seg in reversed(path.split("/")):
            if seg.isdigit():
                try:
                    resource_id = int(seg)
                    break
                except ValueError:
                    pass
        db.execute(
            """
            INSERT INTO audit_log
              (user_id, method, path, status_code, resource_id, metadata)
            VALUES (%s, %s, %s, %s, %s, %s::jsonb)
            """,
            (
                uid,
                method,
                path,
                int(getattr(response, "status_code", 0) or 0),
                resource_id,
                json.dumps({}),
            ),
        )
    except Exception:
        pass
    return response


def _shutdown() -> None:
    log.info("Shutting down gracefully…")
    scheduler.stop()
    # Signal all workers to stop, then wait up to 30s for in-flight tasks
    reg = worker.registry()
    reg.stop_all()
    # Reset any tasks stuck in 'running' state (worker died mid-flight)
    try:
        db.execute(
            "UPDATE agent_tasks SET status = 'queued' "
            "WHERE status = 'running' AND created_at < NOW() - INTERVAL '5 minutes'"
        )
    except Exception:
        pass
    db.close()
    log.info("Shutdown complete")


atexit.register(_shutdown)

# Graceful signal handling — catch SIGTERM/SIGINT and trigger clean shutdown
import signal

def _signal_handler(signum, frame):
    log.info("Received signal %s, initiating graceful shutdown", signum)
    _shutdown()
    import sys
    sys.exit(0)

signal.signal(signal.SIGTERM, _signal_handler)
signal.signal(signal.SIGINT, _signal_handler)


# ============================================================================
# Helpers
# ============================================================================

def _resolve_desktop_token() -> int | None:
    """Check X-Desktop-Token header against desktop_sessions table.
    Returns user_id if valid, None otherwise."""
    token = request.headers.get("X-Desktop-Token")
    if not token:
        return None
    row = db.fetch_one(
        "SELECT user_id FROM desktop_sessions "
        "WHERE token = %s AND expires_at > NOW()",
        (token,),
    )
    return int(row["user_id"]) if row else None


def _resolve_api_token() -> int | None:
    """Authorization: Bearer <token> → user_id. Non-fatal on miss."""
    auth = request.headers.get("Authorization") or ""
    if not auth.startswith("Bearer "):
        return None
    raw = auth[len("Bearer "):].strip()
    if not raw:
        return None
    import hashlib
    h = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    row = db.fetch_one(
        "SELECT user_id, id FROM api_tokens WHERE token_hash = %s", (h,)
    )
    if not row:
        return None
    # Best-effort last_used_at refresh
    try:
        db.execute(
            "UPDATE api_tokens SET last_used_at = NOW() WHERE id = %s",
            (row["id"],),
        )
    except Exception:
        pass
    return int(row["user_id"])


def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        uid = (session.get("user_id")
               or _resolve_desktop_token()
               or _resolve_api_token())
        if not uid:
            return jsonify({"error": "not authenticated"}), 401
        _touch_last_seen(uid)
        # Stash on g so current_user_id() works for both auth methods
        from flask import g
        g._desktop_user_id = uid
        return f(*args, **kwargs)
    return wrapper


def admin_required(f):
    """Gate a route behind role='admin'. Accepts both cookie session and
    X-Desktop-Token auth."""
    @wraps(f)
    def wrapper(*args, **kwargs):
        uid = session.get("user_id") or _resolve_desktop_token()
        if not uid:
            return jsonify({"error": "not authenticated"}), 401
        _touch_last_seen(uid)
        from flask import g
        g._desktop_user_id = uid
        row = db.fetch_one("SELECT role FROM as_users WHERE id = %s", (uid,))
        if not row or row.get("role") != "admin":
            return jsonify({"error": "admin access required"}), 403
        return f(*args, **kwargs)
    return wrapper


def current_user_id() -> int:
    from flask import g
    return session.get("user_id") or getattr(g, "_desktop_user_id", None)


def current_user_role() -> str | None:
    uid = current_user_id()
    if not uid:
        return None
    row = db.fetch_one("SELECT role FROM as_users WHERE id = %s", (uid,))
    return row.get("role") if row else None


# last_seen_at is refreshed lazily — one UPDATE per user per 60s at most,
# so a chatty client polling notifications every 15s doesn't thrash the row.
_LAST_SEEN_CACHE: dict[int, float] = {}
_LAST_SEEN_DEBOUNCE_SECS = 60.0


def _touch_last_seen(uid: int) -> None:
    now = time.time()
    prev = _LAST_SEEN_CACHE.get(uid, 0.0)
    if now - prev < _LAST_SEEN_DEBOUNCE_SECS:
        return
    _LAST_SEEN_CACHE[uid] = now
    try:
        db.execute("UPDATE as_users SET last_seen_at = NOW() WHERE id = %s", (uid,))
    except Exception:
        pass


# ============================================================================
# Health / index
# ============================================================================

# Frontend SPA — served directly by the sidecar so "Open web settings"
# from the desktop tray (and any external browser hitting the personal
# sidecar's port) works. Bundled at build time by PyInstaller via
# --add-data "frontend/dist:frontend_dist".
_FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"
if not _FRONTEND_DIST.exists():
    _FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend_dist"


@app.route("/")
def index():
    if _FRONTEND_DIST.exists() and (_FRONTEND_DIST / "index.html").exists():
        return send_from_directory(str(_FRONTEND_DIST), "index.html")
    return jsonify({
        "service": "holons",
        "status": "running",
        "port": PORT,
    })


@app.route("/<path:path>")
def spa_fallback(path: str):
    """Serve SPA static assets + fall back to index.html for client-side
    routes like /settings, /projects, /dashboard so they don't 500.
    API routes are handled by their specific @app.route handlers and never
    reach this fallback (Flask matches longest prefix).
    """
    if not _FRONTEND_DIST.exists():
        return jsonify({"error": "not found"}), 404
    candidate = _FRONTEND_DIST / path
    if candidate.is_file():
        return send_from_directory(str(_FRONTEND_DIST), path)
    index_path = _FRONTEND_DIST / "index.html"
    if index_path.exists():
        return send_from_directory(str(_FRONTEND_DIST), "index.html")
    return jsonify({"error": "not found"}), 404


@app.route("/api/health")
def health():
    agents = db.fetch_one("SELECT COUNT(*) AS c FROM agents")
    runs = db.fetch_one("SELECT COUNT(*) AS c FROM runs")
    tasks = db.fetch_one("SELECT COUNT(*) AS c FROM agent_tasks")
    return jsonify({
        "db": "ok",
        "agents": agents["c"],
        "runs": runs["c"],
        "tasks": tasks["c"],
        "workers": len(worker.registry()._workers),
    })


# ============================================================================
# Auth
# ============================================================================

@app.route("/api/register", methods=["POST"])
def register():
    data = request.get_json() or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    display_name = data.get("display_name") or username
    if not username or not password:
        return jsonify({"error": "username+password required"}), 400
    if db.fetch_one("SELECT id FROM as_users WHERE username = %s", (username,)):
        return jsonify({"error": "username taken"}), 409
    uid = db.execute_returning(
        """
        INSERT INTO as_users (username, password_hash, display_name)
        VALUES (%s, %s, %s) RETURNING id
        """,
        (username, generate_password_hash(password, method="pbkdf2:sha256"), display_name),
    )
    # Auto-grant every default-for-new-users model client
    from .services import model_clients as mc
    mc.on_user_created(uid)
    session["user_id"] = uid
    return jsonify({"id": uid, "username": username})


# ---- Login throttle: per-IP brute-force protection ----
_login_failures: dict[str, list[float]] = {}  # ip → list of failure timestamps
_LOGIN_WINDOW = 300  # 5 minutes
_LOGIN_MAX_FAILURES = 5
_LOGIN_LOCKOUT = 60  # seconds

def _check_login_throttle() -> str | None:
    """Returns error message if the IP is locked out, else None."""
    ip = request.remote_addr or "unknown"
    import time as _t
    now = _t.time()
    failures = _login_failures.get(ip, [])
    # Prune old entries
    failures = [ts for ts in failures if now - ts < _LOGIN_WINDOW]
    _login_failures[ip] = failures
    if len(failures) >= _LOGIN_MAX_FAILURES:
        last = failures[-1]
        if now - last < _LOGIN_LOCKOUT:
            return f"Too many failed attempts. Please wait {_LOGIN_LOCKOUT}s."
    return None

def _record_login_failure():
    ip = request.remote_addr or "unknown"
    import time as _t
    _login_failures.setdefault(ip, []).append(_t.time())

def _clear_login_failures():
    ip = request.remote_addr or "unknown"
    _login_failures.pop(ip, None)


@app.route("/api/login", methods=["POST"])
def login():
    throttle_err = _check_login_throttle()
    if throttle_err:
        return jsonify({"error": throttle_err}), 429
    data = request.get_json() or {}
    u = db.fetch_one("SELECT * FROM as_users WHERE username = %s", (data.get("username"),))
    if not u or not check_password_hash(u["password_hash"], data.get("password") or ""):
        _record_login_failure()
        return jsonify({"error": "invalid credentials"}), 401
    _clear_login_failures()
    session["user_id"] = u["id"]
    _touch_last_seen(u["id"])
    return jsonify({
        "id": u["id"],
        "username": u["username"],
        "display_name": u["display_name"],
        "role": u.get("role", "user"),
    })


@app.route("/api/login/desktop", methods=["POST"])
def login_desktop():
    """Issue a long-lived opaque token for the Tauri desktop app.
    Token is stored in desktop_sessions with a 365-day TTL.
    On each login, old tokens for the same user are revoked (rotation)."""
    throttle_err = _check_login_throttle()
    if throttle_err:
        return jsonify({"error": throttle_err}), 429
    data = request.get_json() or {}
    u = db.fetch_one("SELECT * FROM as_users WHERE username = %s", (data.get("username"),))
    if not u or not check_password_hash(u["password_hash"], data.get("password") or ""):
        _record_login_failure()
        return jsonify({"error": "invalid credentials"}), 401
    _clear_login_failures()
    # Token rotation: delete all existing desktop tokens for this user.
    # This ensures only the latest login session is valid, preventing
    # stale tokens from accumulating.
    db.execute("DELETE FROM desktop_sessions WHERE user_id = %s", (u["id"],))
    token = secrets.token_urlsafe(64)
    db.execute(
        """
        INSERT INTO desktop_sessions (user_id, token, expires_at)
        VALUES (%s, %s, NOW() + INTERVAL '365 days')
        """,
        (u["id"], token),
    )
    _touch_last_seen(u["id"])
    return jsonify({
        "token": token,
        "id": u["id"],
        "username": u["username"],
        "display_name": u["display_name"],
        "role": u.get("role", "user"),
    })


@app.route("/api/logout/desktop", methods=["POST"])
def logout_desktop():
    """Revoke the desktop token."""
    token = request.headers.get("X-Desktop-Token")
    if token:
        db.execute("DELETE FROM desktop_sessions WHERE token = %s", (token,))
    return jsonify({"ok": True})


@app.route("/api/me/cast_layout")
@login_required
def get_cast_layout():
    row = db.fetch_one(
        "SELECT cast_layout FROM as_users WHERE id = %s",
        (current_user_id(),),
    )
    return jsonify(row["cast_layout"] if row and row.get("cast_layout") else {})


@app.route("/api/me/cast_layout", methods=["PUT"])
@login_required
def put_cast_layout():
    data = request.get_json() or {}
    db.execute(
        "UPDATE as_users SET cast_layout = %s::jsonb WHERE id = %s",
        (json.dumps(data), current_user_id()),
    )
    return jsonify({"ok": True})


# ============================================================================
# IM channel bindings — per-user bot tokens for Telegram / Slack / …
#
# Wire protocol: Bot tokens are stored encrypted via asset_crypto.Fernet.
# Creating or updating a binding triggers im_channels.reload_user() so the
# polling thread picks up the new token without a backend restart.
# ============================================================================


@app.route("/api/im/bindings")
@login_required
def list_im_bindings():
    rows = db.fetch_all(
        "SELECT id, platform, external_id, display_name, enabled, metadata, created_at "
        "FROM im_bindings WHERE user_id = %s ORDER BY id",
        (current_user_id(),),
    )
    return jsonify(rows)


@app.route("/api/im/bindings", methods=["POST"])
@login_required
def upsert_im_binding():
    """Create or update the (user, platform) binding. Validates the
    token by calling getMe first — a bad token fails fast with a 400
    before we ever persist it."""
    from .services import asset_crypto
    from .services import im_channels
    from .services.im_channels import telegram as _tg
    from .services.im_channels import slack as _sl
    from .services.im_channels import line as _ln

    d = request.get_json() or {}
    platform = (d.get("platform") or "").lower()
    token = (d.get("token") or "").strip()
    # Slack and LINE are webhook-only (no sane polling API for bot events).
    # Telegram defaults to polling; user can switch via /transport.
    if platform not in ("telegram", "slack", "line"):
        return jsonify({"error": f"unsupported platform: {platform}"}), 400
    if not token:
        return jsonify({"error": "token required"}), 400

    # Verify — each platform has its own "whoami" check so bad tokens
    # fail fast before we persist.
    try:
        if platform == "telegram":
            info = _tg.verify_token(token)
            display_name = f"@{info.get('username')} ({info.get('first_name')})"
        elif platform == "slack":
            info = _sl.verify_token(token)
            display_name = f"{info.get('team') or 'Slack'} · bot:{info.get('user') or '?'}"
        elif platform == "line":
            info = _ln.verify_token(token)
            display_name = f"LINE · @{info.get('basicId') or info.get('userId') or 'bot'}"
    except Exception as e:
        return jsonify({"error": f"token rejected by platform: {e}"}), 400

    enc = asset_crypto.encrypt(token)
    uid = current_user_id()
    # Slack / LINE are webhook-only — set default transport accordingly.
    default_transport = "polling" if platform == "telegram" else "webhook"
    existing = db.fetch_one(
        "SELECT id FROM im_bindings WHERE user_id = %s AND platform = %s",
        (uid, platform),
    )
    if existing:
        db.execute(
            "UPDATE im_bindings SET secret_encrypted = %s, display_name = %s, "
            "enabled = TRUE, updated_at = NOW(), external_id = NULL, "
            "transport = %s, metadata = '{}'::jsonb WHERE id = %s",
            (enc, display_name, default_transport, existing["id"]),
        )
        bid = existing["id"]
    else:
        bid = db.execute_returning(
            "INSERT INTO im_bindings (user_id, platform, secret_encrypted, "
            "display_name, enabled, transport) "
            "VALUES (%s, %s, %s, %s, TRUE, %s) RETURNING id",
            (uid, platform, enc, display_name, default_transport),
        )
    im_channels.reload_user(uid)
    return jsonify({
        "id": bid, "display_name": display_name, "platform": platform,
        "transport": default_transport,
    })


@app.route("/api/im/bindings/<int:bid>", methods=["DELETE"])
@login_required
def delete_im_binding(bid: int):
    from .services import im_channels
    uid = current_user_id()
    row = db.fetch_one(
        "SELECT id FROM im_bindings WHERE id = %s AND user_id = %s", (bid, uid),
    )
    if not row:
        return jsonify({"error": "not found"}), 404
    db.execute("DELETE FROM im_bindings WHERE id = %s", (bid,))
    im_channels.reload_user(uid)
    return jsonify({"ok": True})


@app.route("/api/im/bindings/<int:bid>/toggle", methods=["POST"])
@login_required
def toggle_im_binding(bid: int):
    from .services import im_channels
    uid = current_user_id()
    row = db.fetch_one(
        "SELECT id, enabled FROM im_bindings WHERE id = %s AND user_id = %s",
        (bid, uid),
    )
    if not row:
        return jsonify({"error": "not found"}), 404
    new_enabled = not row["enabled"]
    db.execute(
        "UPDATE im_bindings SET enabled = %s, updated_at = NOW() WHERE id = %s",
        (new_enabled, bid),
    )
    im_channels.reload_user(uid)
    return jsonify({"id": bid, "enabled": new_enabled})


@app.route("/api/im/bindings/<int:bid>/transport", methods=["POST"])
@login_required
def switch_im_transport(bid: int):
    """Switch a binding between polling and webhook mode.
    Body: {"transport": "polling" | "webhook", "public_url": "https://..."}
    Webhook mode stores a random secret in metadata and calls the
    platform's setWebhook API; polling mode calls deleteWebhook + spawns
    a polling thread."""
    import secrets as _secrets
    from .services import asset_crypto, im_channels
    from .services.im_channels import telegram as _tg

    uid = current_user_id()
    row = db.fetch_one(
        "SELECT * FROM im_bindings WHERE id = %s AND user_id = %s", (bid, uid),
    )
    if not row:
        return jsonify({"error": "not found"}), 404

    d = request.get_json() or {}
    mode = d.get("transport")
    if mode not in ("polling", "webhook"):
        return jsonify({"error": "transport must be 'polling' or 'webhook'"}), 400
    # Slack and LINE can't realistically poll for bot events.
    if mode == "polling" and row["platform"] in ("slack", "line"):
        return jsonify({
            "error": f"{row['platform']} is webhook-only — polling not supported",
        }), 400

    metadata = dict(row.get("metadata") or {})
    token = asset_crypto.decrypt(row["secret_encrypted"])

    if mode == "webhook":
        public_url = (d.get("public_url") or "").strip()
        if not public_url.startswith("https://"):
            return jsonify({"error": "public_url must be https://"}), 400
        # Generate (or reuse) a per-binding secret so the webhook URL is
        # unguessable — path contains /<platform>/<secret>.
        webhook_secret = metadata.get("webhook_secret") or _secrets.token_urlsafe(24)
        metadata["webhook_secret"] = webhook_secret
        metadata["webhook_public_url"] = public_url
        webhook_url = f"{public_url.rstrip('/')}/api/im/webhook/{row['platform']}/{webhook_secret}"
        try:
            if row["platform"] == "telegram":
                adapter = _tg.TelegramAdapter({**row, "secret": token})
                adapter.set_webhook(webhook_url)
        except Exception as e:
            return jsonify({"error": f"platform rejected webhook: {e}"}), 400
    else:  # polling
        try:
            if row["platform"] == "telegram":
                adapter = _tg.TelegramAdapter({**row, "secret": token})
                adapter.delete_webhook()
        except Exception:
            pass  # if deleteWebhook fails we still flip local state

    db.execute(
        "UPDATE im_bindings SET transport = %s, metadata = %s::jsonb, updated_at = NOW() "
        "WHERE id = %s",
        (mode, json.dumps(metadata), bid),
    )
    im_channels.reload_user(uid)
    return jsonify({
        "id": bid, "transport": mode,
        "webhook_url": metadata.get("webhook_public_url") if mode == "webhook" else None,
    })


@app.route("/api/im/webhook/<platform>/<secret>", methods=["POST"])
def im_webhook(platform: str, secret: str):
    """Inbound webhook. Each platform's adapter converts the incoming
    payload into one or more InboundMessage objects and hands them to
    the router. Endpoint is unauthenticated (no session); authorisation
    is the per-binding secret in the URL path.

    Slack requires a `url_verification` handshake on first setup — we
    echo the challenge back without touching any binding.
    """
    from .services import asset_crypto
    from .services.im_channels import router as _router
    from .services.im_channels import telegram as _tg
    from .services.im_channels import slack as _sl
    from .services.im_channels import line as _ln

    payload = request.get_json(silent=True) or {}

    # Slack url_verification is a one-shot challenge at webhook setup time.
    # It arrives BEFORE the URL is officially registered, so we answer it
    # even without looking up a binding.
    if platform == "slack" and payload.get("type") == "url_verification":
        return jsonify({"challenge": payload.get("challenge", "")})

    row = db.fetch_one(
        "SELECT * FROM im_bindings "
        "WHERE platform = %s AND metadata->>'webhook_secret' = %s AND enabled = TRUE "
        "  AND transport = 'webhook'",
        (platform, secret),
    )
    if not row:
        # Don't leak whether the binding exists vs. the secret is wrong.
        return ("", 404)

    token = asset_crypto.decrypt(row["secret_encrypted"])

    # Parse one or many messages depending on the platform envelope.
    messages = []
    adapter = None
    try:
        if platform == "telegram":
            adapter = _tg.TelegramAdapter({**row, "secret": token})
            parsed = adapter.parse_update(payload)
            if parsed:
                messages.append(parsed)
        elif platform == "slack":
            adapter = _sl.SlackAdapter({**row, "secret": token})
            parsed = adapter.parse_update(payload)
            if parsed:
                messages.append(parsed)
        elif platform == "line":
            adapter = _ln.LineAdapter({**row, "secret": token})
            messages = adapter.parse_update(payload)
    except Exception:
        app.logger.exception("webhook parse failed for binding %s", row["id"])

    from .services.im_channels import manager as _im_mgr
    for m in messages:
        try:
            result = _router.dispatch(m, row["user_id"])
            if adapter is not None:
                _im_mgr._deliver(adapter, m.external_id, result)
        except Exception:
            app.logger.exception("webhook dispatch failed for binding %s", row["id"])

    # Always 200 — if we 4xx/5xx the platform retries and the user sees
    # duplicate replies.
    return ("", 200)


@app.route("/api/logout", methods=["POST"])
def logout():
    session.clear()
    return jsonify({"ok": True})


@app.route("/api/me")
def me():
    # /api/me deliberately has no @login_required — unauthenticated callers
    # get {authenticated: false} rather than 401. But we DO need to consult
    # the desktop-token + API-token resolvers explicitly since those only
    # populate g._desktop_user_id from inside login_required.
    uid = (session.get("user_id")
           or _resolve_desktop_token()
           or _resolve_api_token())
    if not uid:
        return jsonify({"authenticated": False})
    u = db.fetch_one(
        "SELECT id, username, display_name, default_lead_agent_id, role, language, "
        "       lead_max_steps, lead_max_tokens "
        "FROM as_users WHERE id = %s",
        (uid,),
    )
    if u:
        _touch_last_seen(uid)
    return jsonify({"authenticated": True, **(u or {})})


@app.route("/api/me", methods=["PUT"])
@login_required
def update_me():
    d = request.get_json() or {}
    sets, params = [], []
    if "display_name" in d:
        dn = (d["display_name"] or "").strip()
        if not dn:
            return jsonify({"error": "display_name required"}), 400
        sets.append("display_name = %s")
        params.append(dn)
    if "language" in d:
        lang = d["language"] or "en"
        if lang not in ("en", "zh-TW"):
            return jsonify({"error": "language must be 'en' or 'zh-TW'"}), 400
        sets.append("language = %s")
        params.append(lang)
    if "lead_max_steps" in d:
        from .services import feature_flags
        hard = int(feature_flags.get_value("lead_max_steps_hard_limit") or 1000)
        val = max(1, min(hard, int(d["lead_max_steps"])))
        sets.append("lead_max_steps = %s")
        params.append(val)
    if "lead_max_tokens" in d:
        from .services import feature_flags
        hard = int(feature_flags.get_value("lead_max_tokens_hard_limit") or 500000)
        val = max(1000, min(hard, int(d["lead_max_tokens"])))
        sets.append("lead_max_tokens = %s")
        params.append(val)
    if not sets:
        return jsonify({"ok": True})
    params.append(current_user_id())
    db.execute(
        f"UPDATE as_users SET {', '.join(sets)} WHERE id = %s",
        tuple(params),
    )
    return jsonify({"ok": True})


@app.route("/api/me/password", methods=["PUT"])
@login_required
def update_password():
    d = request.get_json() or {}
    old_pw = d.get("old_password") or ""
    new_pw = d.get("new_password") or ""
    if len(new_pw) < 4:
        return jsonify({"error": "password too short"}), 400
    u = db.fetch_one(
        "SELECT password_hash FROM as_users WHERE id = %s",
        (current_user_id(),),
    )
    if not u or not check_password_hash(u["password_hash"], old_pw):
        return jsonify({"error": "incorrect current password"}), 401
    db.execute(
        "UPDATE as_users SET password_hash = %s WHERE id = %s",
        (generate_password_hash(new_pw, method="pbkdf2:sha256"), current_user_id()),
    )
    return jsonify({"ok": True})


# ============================================================================
# Agents
# ============================================================================

@app.route("/api/agents", methods=["GET"])
@login_required
def list_agents():
    """Return every agent the current user can use (owned + borrowed).

    Borrowed agents include:
      - agents shared via an explicit `agent_shares` record (not revoked / expired)
      - agents whose owner set `visibility = 'org_wide'`
      - agents whose owner set `visibility = 'user_list'` with this user in the list

    Each row is tagged with a `borrowed` boolean so the UI can show the
    owner vs the borrower perspective distinctly.
    """
    uid = current_user_id()
    rows = db.fetch_all(
        """
        SELECT a.*,
               (a.user_id <> %(uid)s) AS borrowed,
               owner.username AS owner_username,
               owner.display_name AS owner_display_name
        FROM agents a
        LEFT JOIN as_users owner ON owner.id = a.user_id
        WHERE a.user_id = %(uid)s
           OR (
               a.is_lead = FALSE
               AND (
                   a.visibility = 'org_wide'
                   OR (a.visibility = 'user_list'
                       AND a.visible_user_ids @> %(uid_arr)s::jsonb)
                   OR EXISTS (
                       SELECT 1 FROM agent_shares s
                       WHERE s.agent_id = a.id
                         AND s.borrower_user_id = %(uid)s
                         AND s.revoked_at IS NULL
                         AND (s.expires_at IS NULL OR s.expires_at > NOW())
                   )
               )
           )
        ORDER BY (a.user_id <> %(uid)s) ASC, a.is_lead DESC, a.id ASC
        """,
        {"uid": uid, "uid_arr": json.dumps([uid])},
    )
    return jsonify(rows)


@app.route("/api/agents", methods=["POST"])
@login_required
def create_agent():
    from .services import model_clients as mc
    from .services.sanitize import clean_name, clean_text
    d = request.get_json() or {}
    # Sanitize text inputs
    for key in ("name", "role_title"):
        if key in d and d[key]:
            d[key] = clean_name(d[key])
    for key in ("description", "system_prompt", "few_shot"):
        if key in d and d[key]:
            d[key] = clean_text(d[key], max_len=20_000)

    # Resolve the model client this agent should use. Caller may pass
    # model_client_id explicitly; otherwise we pick the first default
    # client the user is allowed to use.
    client_id = d.get("model_client_id")
    is_admin = _is_admin_user(current_user_id())
    if client_id:
        if not mc.user_can_use(int(client_id), current_user_id(), is_admin=is_admin):
            return jsonify({"error": "you don't have permission to use that model client"}), 403
    else:
        allowed = mc.list_for_user(current_user_id())
        default_rows = [r for r in allowed if r.get("default_for_new_users")]
        if default_rows:
            client_id = default_rows[0]["id"]
        elif allowed:
            client_id = allowed[0]["id"]

    # Pick a default model_id from the chosen client's config.models if
    # the caller didn't supply one.
    primary_model_id = d.get("primary_model_id")
    if not primary_model_id and client_id:
        raw = mc.get_raw(int(client_id))
        models = ((raw or {}).get("config") or {}).get("models") or []
        if models:
            primary_model_id = models[0].get("id")
    if not primary_model_id:
        primary_model_id = "jp.anthropic.claude-sonnet-4-6"

    aid = db.execute_returning(
        """
        INSERT INTO agents (user_id, owner_user_id, name, role_title, description,
                           system_prompt, few_shot, primary_model_id, avatar_config,
                           model_client_id)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s)
        RETURNING id
        """,
        (
            current_user_id(), current_user_id(),
            d.get("name"), d.get("role_title"), d.get("description"),
            d.get("system_prompt"), d.get("few_shot"),
            primary_model_id,
            json.dumps(d.get("avatar_config") or {}),
            client_id,
        ),
    )
    # Start a worker for this new agent
    worker.registry().start_agent(aid)
    return jsonify({"id": aid})


def _is_admin_user(uid: int) -> bool:
    row = db.fetch_one("SELECT role FROM as_users WHERE id = %s", (uid,))
    return bool(row and row.get("role") == "admin")


@app.route("/api/agents/<int:aid>", methods=["GET"])
@login_required
def get_agent(aid: int):
    uid = current_user_id()
    if not sharing.user_can_access_agent(uid, aid):
        return jsonify({"error": "not found"}), 404
    a = db.fetch_one("SELECT * FROM agents WHERE id = %s", (aid,))
    if not a:
        return jsonify({"error": "not found"}), 404
    a["borrowed"] = a.get("user_id") != uid
    a["queue_depth"] = queue.queue_depth(aid)
    a["running_task"] = queue.running_task(aid)
    return jsonify(a)


@app.route("/api/agents/<int:aid>", methods=["PUT"])
@login_required
def update_agent(aid: int):
    from .services import model_clients as mc
    d = request.get_json() or {}

    # Validate model_client_id if the caller is trying to change it
    if "model_client_id" in d and d["model_client_id"] is not None:
        if not mc.user_can_use(
            int(d["model_client_id"]), current_user_id(),
            is_admin=_is_admin_user(current_user_id()),
        ):
            return jsonify({"error": "you don't have permission to use that model client"}), 403

    fields, params = [], []
    for key in ("name", "role_title", "description", "system_prompt",
                "few_shot", "primary_model_id", "status", "max_queue_depth",
                "model_client_id",
                "daily_token_quota", "daily_cost_quota",
                "monthly_token_quota", "monthly_cost_quota"):
        if key in d:
            fields.append(f"{key} = %s")
            params.append(d[key])
    # JSON fields
    for key in ("working_hours", "visible_user_ids", "avatar_config", "tool_config"):
        if key in d:
            fields.append(f"{key} = %s::jsonb")
            params.append(json.dumps(d[key]))
    if not fields:
        return jsonify({"ok": True})
    params.extend([aid, current_user_id()])
    db.execute(
        f"UPDATE agents SET {', '.join(fields)}, updated_at = NOW() WHERE id = %s AND user_id = %s",
        tuple(params),
    )
    return jsonify({"ok": True})


@app.route("/api/agents/<int:aid>/usage")
@login_required
def get_agent_usage(aid: int):
    """Return today + last 30d usage for the budget bar in AgentDetail."""
    if not db.fetch_one(
        "SELECT id FROM agents WHERE id = %s AND user_id = %s",
        (aid, current_user_id()),
    ):
        return jsonify({"error": "not found"}), 404
    day = db.fetch_one(
        """SELECT COALESCE(SUM(cost_usd), 0)::float AS cost,
                  COALESCE(SUM(input_tokens + output_tokens), 0) AS tokens
           FROM run_steps
           WHERE agent_id = %s AND started_at >= NOW() - INTERVAL '1 day'""",
        (aid,),
    ) or {}
    month = db.fetch_one(
        """SELECT COALESCE(SUM(cost_usd), 0)::float AS cost,
                  COALESCE(SUM(input_tokens + output_tokens), 0) AS tokens
           FROM run_steps
           WHERE agent_id = %s AND started_at >= NOW() - INTERVAL '30 days'""",
        (aid,),
    ) or {}
    return jsonify({
        "today": {"cost": float(day.get("cost") or 0),
                  "tokens": int(day.get("tokens") or 0)},
        "month": {"cost": float(month.get("cost") or 0),
                  "tokens": int(month.get("tokens") or 0)},
    })


@app.route("/api/agents/<int:aid>", methods=["DELETE"])
@login_required
def delete_agent(aid: int):
    db.execute("DELETE FROM agents WHERE id = %s AND user_id = %s", (aid, current_user_id()))
    worker.registry().stop_agent(aid)
    return jsonify({"ok": True})


@app.route("/api/agents/<int:aid>/queue")
@login_required
def get_agent_queue(aid: int):
    tasks = queue.queue_for_agent(aid, limit=100)
    return jsonify(tasks)


@app.route("/api/agents/<int:aid>/chat", methods=["POST"])
@login_required
def chat_with_agent_route(aid: int):
    d = request.get_json() or {}
    message = d.get("message", "")
    thread_id = d.get("thread_id")
    if not message:
        return jsonify({"error": "message required"}), 400
    try:
        result = lead_agent.chat_with_agent(
            current_user_id(), aid, message, thread_id=thread_id,
        )
    except ValueError as e:
        return jsonify({"error": str(e)}), 404
    return jsonify(result)


@app.route("/api/agents/<int:aid>/threads")
@login_required
def list_agent_threads_route(aid: int):
    return jsonify(lead_agent.list_agent_threads(current_user_id(), aid))


@app.route("/api/agents/<int:aid>/mcp_servers", methods=["GET"])
@login_required
def list_agent_mcp_servers(aid: int):
    """List MCP servers attached to this agent. Owner-only."""
    a = db.fetch_one(
        "SELECT id FROM agents WHERE id = %s AND user_id = %s",
        (aid, current_user_id()),
    )
    if not a:
        return jsonify({"error": "not found"}), 404
    rows = db.fetch_all(
        """
        SELECT id, name, url, enabled, created_at,
               (auth_header IS NOT NULL AND auth_header <> '') AS has_auth
        FROM agent_mcp_servers
        WHERE agent_id = %s
        ORDER BY id
        """,
        (aid,),
    )
    return jsonify(rows)


@app.route("/api/agents/<int:aid>/mcp_servers", methods=["POST"])
@login_required
def create_agent_mcp_server(aid: int):
    a = db.fetch_one(
        "SELECT id FROM agents WHERE id = %s AND user_id = %s",
        (aid, current_user_id()),
    )
    if not a:
        return jsonify({"error": "not found"}), 404
    d = request.get_json() or {}
    name = (d.get("name") or "").strip()
    url = (d.get("url") or "").strip()
    if not name or not url:
        return jsonify({"error": "name + url required"}), 400
    if not (url.startswith("http://") or url.startswith("https://")):
        return jsonify({"error": "url must be http(s)"}), 400
    sid = db.execute_returning(
        """
        INSERT INTO agent_mcp_servers (agent_id, name, url, auth_header, enabled)
        VALUES (%s, %s, %s, %s, TRUE) RETURNING id
        """,
        (aid, name, url, d.get("auth_header") or None),
    )
    return jsonify({"id": sid})


@app.route("/api/agents/<int:aid>/mcp_servers/<int:sid>", methods=["PUT"])
@login_required
def update_agent_mcp_server(aid: int, sid: int):
    a = db.fetch_one(
        "SELECT id FROM agents WHERE id = %s AND user_id = %s",
        (aid, current_user_id()),
    )
    if not a:
        return jsonify({"error": "not found"}), 404
    d = request.get_json() or {}
    fields, params = [], []
    for key in ("name", "url", "auth_header", "enabled"):
        if key in d:
            fields.append(f"{key} = %s")
            params.append(d[key])
    if not fields:
        return jsonify({"ok": True})
    params.extend([sid, aid])
    db.execute(
        f"UPDATE agent_mcp_servers SET {', '.join(fields)}, updated_at = NOW() "
        f"WHERE id = %s AND agent_id = %s",
        tuple(params),
    )
    return jsonify({"ok": True})


@app.route("/api/agents/<int:aid>/mcp_servers/<int:sid>", methods=["DELETE"])
@login_required
def delete_agent_mcp_server(aid: int, sid: int):
    a = db.fetch_one(
        "SELECT id FROM agents WHERE id = %s AND user_id = %s",
        (aid, current_user_id()),
    )
    if not a:
        return jsonify({"error": "not found"}), 404
    db.execute(
        "DELETE FROM agent_mcp_servers WHERE id = %s AND agent_id = %s",
        (sid, aid),
    )
    return jsonify({"ok": True})


@app.route("/api/agents/<int:aid>/mcp_servers/<int:sid>/probe", methods=["POST"])
@login_required
def probe_agent_mcp_server(aid: int, sid: int):
    """Test-fetch tools/list from this MCP server. Used by the UI to
    verify a server is reachable before saving."""
    a = db.fetch_one(
        "SELECT id FROM agents WHERE id = %s AND user_id = %s",
        (aid, current_user_id()),
    )
    if not a:
        return jsonify({"error": "not found"}), 404
    row = db.fetch_one(
        "SELECT url, auth_header FROM agent_mcp_servers WHERE id = %s AND agent_id = %s",
        (sid, aid),
    )
    if not row:
        return jsonify({"error": "not found"}), 404
    from .services import mcp_client
    try:
        tools_list = mcp_client.list_tools(row["url"], row.get("auth_header"))
    except Exception as e:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(e)})
    return jsonify({
        "ok": True,
        "count": len(tools_list),
        "tools": [
            {"name": t.get("name"), "description": t.get("description") or ""}
            for t in tools_list
        ],
    })


@app.route("/api/agents/<int:aid>/runs")
@login_required
def list_agent_runs(aid: int):
    """Recent runs where this agent participated (via run_steps).

    Used by the Dialog Center calendar tab to show "what has this person
    been working on lately"."""
    a = db.fetch_one(
        "SELECT id FROM agents WHERE id = %s AND user_id = %s",
        (aid, current_user_id()),
    )
    if not a:
        return jsonify({"error": "not found"}), 404
    rows = db.fetch_all(
        """
        SELECT DISTINCT r.id, r.workflow_id, r.status, r.started_at, r.finished_at,
               r.total_cost_usd::float AS total_cost_usd,
               r.total_input_tokens, r.total_output_tokens,
               w.name AS workflow_name,
               (
                   SELECT COUNT(*) FROM run_steps s2
                   WHERE s2.run_id = r.id AND s2.agent_id = %s
               ) AS my_steps
        FROM runs r
        JOIN run_steps s ON s.run_id = r.id
        JOIN workflows w ON w.id = r.workflow_id
        WHERE s.agent_id = %s AND r.user_id = %s
        ORDER BY r.id DESC
        LIMIT 30
        """,
        (aid, aid, current_user_id()),
    )
    return jsonify(rows)


# ============================================================================
# Workflows
# ============================================================================

@app.route("/api/workflows", methods=["GET"])
@login_required
def list_workflows():
    """List workflows. Filters:
      ?scope=mine     (default) — workflows owned by the current user
      ?scope=templates          — every workflow with is_template = TRUE
                                   (shared pool, across users)
    """
    scope = request.args.get("scope") or "mine"
    if scope == "templates":
        rows = db.fetch_all(
            """
            SELECT w.*, u.username AS owner_username, u.display_name AS owner_display_name
            FROM workflows w
            LEFT JOIN as_users u ON u.id = w.user_id
            WHERE w.is_template = TRUE
            ORDER BY w.id DESC
            """,
        )
    else:
        rows = db.fetch_all(
            "SELECT * FROM workflows WHERE user_id = %s ORDER BY id DESC",
            (current_user_id(),),
        )
    return jsonify(rows)


@app.route("/api/workflows/<int:wid>", methods=["GET"])
@login_required
def get_workflow(wid: int):
    wf = db.fetch_one("SELECT * FROM workflows WHERE id = %s AND user_id = %s",
                      (wid, current_user_id()))
    if not wf:
        return jsonify({"error": "not found"}), 404
    nodes = db.fetch_all(
        "SELECT * FROM workflow_nodes WHERE workflow_id = %s ORDER BY position",
        (wid,),
    )
    # Hydrate group nodes with their group meta + members so the editor can
    # render fan-out / fan-in branches.
    group_ids = list({n["group_id"] for n in nodes if n.get("group_id")})
    if group_ids:
        groups_rows = db.fetch_all(
            "SELECT id, name, mode, aggregator_agent_id FROM groups_tbl WHERE id = ANY(%s::bigint[])",
            (group_ids,),
        )
        groups_by_id = {g["id"]: g for g in groups_rows}
        members_rows = db.fetch_all(
            """
            SELECT gm.id, gm.group_id, gm.agent_id, gm.position, gm.custom_prompt,
                   a.name AS agent_name, a.role_title, a.avatar_config
            FROM group_members gm
            JOIN agents a ON a.id = gm.agent_id
            WHERE gm.group_id = ANY(%s::bigint[])
            ORDER BY gm.group_id, gm.position
            """,
            (group_ids,),
        )
        members_by_group: dict[int, list[dict]] = {}
        for m in members_rows:
            members_by_group.setdefault(m["group_id"], []).append(m)
        for n in nodes:
            gid = n.get("group_id")
            if gid and gid in groups_by_id:
                g = groups_by_id[gid]
                n["group"] = {
                    "id": g["id"],
                    "name": g.get("name"),
                    "mode": g.get("mode"),
                    "aggregator_agent_id": g.get("aggregator_agent_id"),
                    "members": members_by_group.get(gid, []),
                }
    wf["nodes"] = nodes
    return jsonify(wf)


@app.route("/api/workflows", methods=["POST"])
@login_required
def create_workflow():
    d = request.get_json() or {}
    wid = db.execute_returning(
        """
        INSERT INTO workflows (user_id, name, description, loop_enabled, max_loops, source)
        VALUES (%s, %s, %s, %s, %s, 'manual') RETURNING id
        """,
        (current_user_id(), d.get("name"), d.get("description"),
         bool(d.get("loop_enabled")), int(d.get("max_loops", 1))),
    )
    return jsonify({"id": wid})


@app.route("/api/workflows/<int:wid>", methods=["PUT"])
@login_required
def update_workflow(wid: int):
    d = request.get_json() or {}
    fields, params = [], []
    for k in ("name", "description", "loop_enabled", "max_loops", "is_draft", "is_template"):
        if k in d:
            fields.append(f"{k} = %s")
            params.append(d[k])
    if not fields:
        return jsonify({"ok": True})
    params.extend([wid, current_user_id()])
    db.execute(
        f"UPDATE workflows SET {', '.join(fields)}, updated_at = NOW() WHERE id = %s AND user_id = %s",
        tuple(params),
    )
    return jsonify({"ok": True})


@app.route("/api/workflows/<int:wid>", methods=["DELETE"])
@login_required
def delete_workflow(wid: int):
    db.execute("DELETE FROM workflows WHERE id = %s AND user_id = %s", (wid, current_user_id()))
    return jsonify({"ok": True})


def _assert_workflow_owner(wid: int) -> bool:
    row = db.fetch_one(
        "SELECT id FROM workflows WHERE id = %s AND user_id = %s",
        (wid, current_user_id()),
    )
    return row is not None


@app.route("/api/workflows/<int:wid>/nodes", methods=["POST"])
@login_required
def create_workflow_node(wid: int):
    if not _assert_workflow_owner(wid):
        return jsonify({"error": "not found"}), 404
    d = request.get_json() or {}
    node_type = d.get("node_type", "agent")
    if node_type not in ("agent", "group"):
        return jsonify({"error": "invalid node_type"}), 400
    # Append at end by default
    max_pos = db.fetch_one(
        "SELECT COALESCE(MAX(position), -1) AS p FROM workflow_nodes WHERE workflow_id = %s",
        (wid,),
    )["p"]
    position = int(d.get("position", max_pos + 1))
    nid = db.execute_returning(
        """
        INSERT INTO workflow_nodes
            (workflow_id, position, node_type, agent_id, group_id,
             label, prompt_template, system_prompt_override, pos_x, pos_y)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (
            wid, position, node_type,
            d.get("agent_id"), d.get("group_id"),
            d.get("label"), d.get("prompt_template"),
            d.get("system_prompt_override"),
            int(d.get("pos_x", 100 + position * 280)),
            int(d.get("pos_y", 200)),
        ),
    )
    return jsonify({"id": nid})


@app.route("/api/workflows/<int:wid>/nodes/<int:nid>", methods=["PUT"])
@login_required
def update_workflow_node(wid: int, nid: int):
    if not _assert_workflow_owner(wid):
        return jsonify({"error": "not found"}), 404
    d = request.get_json() or {}
    fields, params = [], []
    for k in ("position", "node_type", "agent_id", "group_id",
              "label", "prompt_template", "system_prompt_override",
              "pos_x", "pos_y"):
        if k in d:
            fields.append(f"{k} = %s")
            params.append(d[k])
    if not fields:
        return jsonify({"ok": True})
    params.extend([nid, wid])
    db.execute(
        f"UPDATE workflow_nodes SET {', '.join(fields)} WHERE id = %s AND workflow_id = %s",
        tuple(params),
    )
    return jsonify({"ok": True})


@app.route("/api/workflows/<int:wid>/nodes/reorder", methods=["POST"])
@login_required
def reorder_workflow_nodes(wid: int):
    if not _assert_workflow_owner(wid):
        return jsonify({"error": "not found"}), 404
    d = request.get_json() or {}
    # Accepts { node_ids: [id1, id2, id3, ...] } — this becomes the new
    # position order (0, 1, 2, ...). Each id must belong to this workflow.
    node_ids = d.get("node_ids") or []
    if not isinstance(node_ids, list):
        return jsonify({"error": "node_ids must be a list"}), 400
    existing = db.fetch_all(
        "SELECT id FROM workflow_nodes WHERE workflow_id = %s",
        (wid,),
    )
    valid = {r["id"] for r in existing}
    for i, nid in enumerate(node_ids):
        if int(nid) not in valid:
            continue
        db.execute(
            "UPDATE workflow_nodes SET position = %s WHERE id = %s AND workflow_id = %s",
            (i, int(nid), wid),
        )
    return jsonify({"ok": True})


@app.route("/api/workflows/<int:wid>/nodes/<int:nid>", methods=["DELETE"])
@login_required
def delete_workflow_node(wid: int, nid: int):
    if not _assert_workflow_owner(wid):
        return jsonify({"error": "not found"}), 404
    db.execute(
        "DELETE FROM workflow_nodes WHERE id = %s AND workflow_id = %s",
        (nid, wid),
    )
    # Re-pack positions so they stay contiguous
    db.execute(
        """
        WITH ranked AS (
            SELECT id, ROW_NUMBER() OVER (ORDER BY position, id) - 1 AS new_pos
            FROM workflow_nodes WHERE workflow_id = %s
        )
        UPDATE workflow_nodes w SET position = r.new_pos
        FROM ranked r WHERE w.id = r.id
        """,
        (wid,),
    )
    return jsonify({"ok": True})


@app.route("/api/workflows/<int:wid>/export")
@login_required
def export_workflow(wid: int):
    wf = db.fetch_one(
        "SELECT * FROM workflows WHERE id = %s AND user_id = %s",
        (wid, current_user_id()),
    )
    if not wf:
        return jsonify({"error": "not found"}), 404
    nodes = db.fetch_all(
        "SELECT * FROM workflow_nodes WHERE workflow_id = %s ORDER BY position",
        (wid,),
    )
    # Resolve agent references by name so they can be re-bound on import
    bundle_nodes = []
    for n in nodes:
        agent_name = None
        if n["agent_id"]:
            a = db.fetch_one("SELECT name FROM agents WHERE id = %s", (n["agent_id"],))
            agent_name = a["name"] if a else None
        bundle_nodes.append({
            "position": n["position"],
            "node_type": n["node_type"],
            "agent_name": agent_name,
            "label": n["label"],
            "prompt_template": n["prompt_template"],
            "pos_x": n["pos_x"],
            "pos_y": n["pos_y"],
        })
    return jsonify({
        "format": "agent_company.workflow.v1",
        "name": wf["name"],
        "description": wf["description"],
        "loop_enabled": wf["loop_enabled"],
        "max_loops": wf["max_loops"],
        "loop_prompt": wf["loop_prompt"],
        "nodes": bundle_nodes,
    })


@app.route("/api/workflows/import", methods=["POST"])
@login_required
def import_workflow():
    bundle = request.get_json() or {}
    if bundle.get("format") != "agent_company.workflow.v1":
        return jsonify({"error": "invalid bundle format"}), 400
    name = (bundle.get("name") or "").strip() or "Imported workflow"
    wid = db.execute_returning(
        """
        INSERT INTO workflows
            (user_id, name, description, loop_enabled, max_loops, loop_prompt, source)
        VALUES (%s, %s, %s, %s, %s, %s, 'imported') RETURNING id
        """,
        (
            current_user_id(),
            name,
            bundle.get("description"),
            bool(bundle.get("loop_enabled")),
            int(bundle.get("max_loops", 1)),
            bundle.get("loop_prompt"),
        ),
    )
    # Build agent_name → id map for the current user
    user_agents = db.fetch_all(
        "SELECT id, name FROM agents WHERE user_id = %s",
        (current_user_id(),),
    )
    by_name = {a["name"]: a["id"] for a in user_agents}

    for n in bundle.get("nodes") or []:
        agent_id = by_name.get(n.get("agent_name")) if n.get("agent_name") else None
        db.execute(
            """
            INSERT INTO workflow_nodes
                (workflow_id, position, node_type, agent_id, label,
                 prompt_template, pos_x, pos_y)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                wid,
                int(n.get("position", 0)),
                n.get("node_type", "agent"),
                agent_id,
                n.get("label"),
                n.get("prompt_template"),
                int(n.get("pos_x", 100)),
                int(n.get("pos_y", 200)),
            ),
        )
    return jsonify({"id": wid})


@app.route("/api/workflows/<int:wid>/run", methods=["POST"])
@login_required
def run_workflow(wid: int):
    d = request.get_json() or {}
    initial = d.get("input", "")
    priority = d.get("priority", "normal")
    # trigger_source is whitelisted — 'api' is used by programmatic clients
    # and e2e tests to suppress the Lead completion notification.
    trigger_source = d.get("trigger_source", "manual")
    if trigger_source not in ("manual", "chat", "api"):
        trigger_source = "manual"
    # Validate the workflow exists and belongs to the current user before
    # anything else — otherwise the engine's INSERT INTO runs would raise a
    # psycopg ForeignKeyViolation and bubble up as a 500.
    wf_row = db.fetch_one(
        "SELECT name FROM workflows WHERE id = %s AND user_id = %s",
        (wid, current_user_id()),
    )
    if not wf_row:
        return jsonify({"error": f"workflow {wid} not found"}), 404
    wf_name = wf_row.get("name") or f"Workflow #{wid}"
    # Optional thread_id: when provided (e.g., from a chat WorkflowBubble),
    # attach a "user→請開始執行" + "lead→好的..." pair plus a run_event
    # placeholder bubble in that thread before dispatching the run. The
    # engine's run-complete notifier later UPDATES that placeholder.
    thread_id = d.get("thread_id")
    run_event_msg_id: int | None = None
    if thread_id:
        owns_thread = db.fetch_one(
            "SELECT thread_id FROM lead_conversations WHERE thread_id = %s AND user_id = %s",
            (thread_id, current_user_id()),
        )
        if owns_thread:
            db.execute(
                "INSERT INTO lead_messages (thread_id, role, content) VALUES (%s, 'user', %s)",
                (thread_id, "Please start the run."),
            )
            db.execute(
                "INSERT INTO lead_messages (thread_id, role, content) VALUES (%s, 'lead', %s)",
                (thread_id, f"On it — starting **{wf_name}**."),
            )

    try:
        run_id = engine.dispatch_workflow(
            wid, current_user_id(), initial,
            trigger_source=trigger_source,
            priority=priority,
            project_id=d.get("project_id"),
        )
    except user_quotas.QuotaExceeded as qe:
        return jsonify({
            "error": str(qe),
            "spent": qe.spent,
            "limits": qe.limits,
        }), 429

    # After we have a run_id, insert the run_event placeholder message that
    # the frontend will render as a live RunStatusCard.
    if thread_id and owns_thread:
        run_event_msg_id = db.execute_returning(
            """
            INSERT INTO lead_messages (thread_id, role, content, metadata)
            VALUES (%s, 'lead', '', %s::jsonb)
            RETURNING id
            """,
            (thread_id, json.dumps({
                "event": "run_event",
                "run_id": run_id,
                "workflow_id": wid,
                "workflow_name": wf_name,
            })),
        )
        db.execute(
            "UPDATE lead_conversations SET updated_at = NOW() WHERE thread_id = %s",
            (thread_id,),
        )
        # Tell the engine which message to update on completion
        db.execute(
            "UPDATE runs SET trigger_context = trigger_context || %s::jsonb WHERE id = %s",
            (json.dumps({
                "lead_thread_id": thread_id,
                "lead_run_event_msg_id": run_event_msg_id,
            }), run_id),
        )

    return jsonify({"run_id": run_id, "status": "dispatched"})


@app.route("/api/workflows/<int:wid>/clone", methods=["POST"])
@login_required
def clone_workflow(wid: int):
    """Duplicate a workflow (template or otherwise) into a new non-template
    copy for the current user, with parent_workflow_id set for provenance.

    Works for any workflow the current user can access:
      - their own workflows
      - any workflow currently flagged as is_template (treated as public within
        this deployment). Future work: visibility fields on workflows.
    """
    wf = db.fetch_one("SELECT * FROM workflows WHERE id = %s", (wid,))
    if not wf:
        return jsonify({"error": "not found"}), 404
    uid = current_user_id()
    if wf["user_id"] != uid and not wf.get("is_template"):
        return jsonify({"error": "not found"}), 404

    d = request.get_json() or {}
    new_name = (d.get("name") or "").strip() or f"{wf['name']} (copy)"

    new_id = db.execute_returning(
        """
        INSERT INTO workflows
            (user_id, name, description, loop_enabled, max_loops, loop_prompt,
             source, is_draft, is_template, parent_workflow_id)
        VALUES (%s, %s, %s, %s, %s, %s, 'cloned', FALSE, FALSE, %s)
        RETURNING id
        """,
        (
            uid,
            new_name,
            wf["description"],
            wf["loop_enabled"],
            wf["max_loops"],
            wf["loop_prompt"],
            wid,
        ),
    )

    src_nodes = db.fetch_all(
        "SELECT * FROM workflow_nodes WHERE workflow_id = %s ORDER BY position",
        (wid,),
    )
    for n in src_nodes:
        db.execute(
            """
            INSERT INTO workflow_nodes
                (workflow_id, position, node_type, agent_id, group_id,
                 label, prompt_template, pos_x, pos_y)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                new_id, n["position"], n["node_type"],
                n.get("agent_id"), n.get("group_id"),
                n.get("label"), n.get("prompt_template"),
                n.get("pos_x"), n.get("pos_y"),
            ),
        )
    return jsonify({"id": new_id})


@app.route("/api/workflows/<int:wid>/runs")
@login_required
def list_workflow_runs(wid: int):
    """Return recent runs for a single workflow (used by WorkflowBubble to
    show whether the user has already executed this proposal)."""
    if not _assert_workflow_owner(wid):
        return jsonify({"error": "not found"}), 404
    rows = db.fetch_all(
        """
        SELECT id, status, started_at, finished_at, total_cost_usd::float AS total_cost_usd,
               total_input_tokens, total_output_tokens, iterations, trigger_source
        FROM runs
        WHERE workflow_id = %s AND user_id = %s
        ORDER BY id DESC
        LIMIT 20
        """,
        (wid, current_user_id()),
    )
    return jsonify(rows)


# ============================================================================
# Groups (parallel / sequential agent ensembles)
# ============================================================================

def _fetch_group_with_members(gid: int, user_id: int) -> dict | None:
    g = db.fetch_one(
        "SELECT * FROM groups_tbl WHERE id = %s AND user_id = %s",
        (gid, user_id),
    )
    if not g:
        return None
    g["members"] = db.fetch_all(
        """
        SELECT gm.id, gm.agent_id, gm.position, gm.custom_prompt,
               a.name AS agent_name, a.role_title, a.avatar_config
        FROM group_members gm
        JOIN agents a ON a.id = gm.agent_id
        WHERE gm.group_id = %s
        ORDER BY gm.position, gm.id
        """,
        (gid,),
    )
    return g


@app.route("/api/groups", methods=["GET"])
@login_required
def list_groups():
    rows = db.fetch_all(
        """
        SELECT g.*, COUNT(gm.id) AS member_count
        FROM groups_tbl g
        LEFT JOIN group_members gm ON gm.group_id = g.id
        WHERE g.user_id = %s AND COALESCE(g.is_ephemeral, FALSE) = FALSE
        GROUP BY g.id
        ORDER BY g.id DESC
        """,
        (current_user_id(),),
    )
    return jsonify(rows)


@app.route("/api/groups/<int:gid>", methods=["GET"])
@login_required
def get_group(gid: int):
    g = _fetch_group_with_members(gid, current_user_id())
    if not g:
        return jsonify({"error": "not found"}), 404
    return jsonify(g)


@app.route("/api/groups", methods=["POST"])
@login_required
def create_group():
    d = request.get_json() or {}
    name = (d.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name required"}), 400
    mode = d.get("mode", "parallel")
    if mode not in ("parallel", "sequential"):
        return jsonify({"error": "invalid mode"}), 400
    gid = db.execute_returning(
        """
        INSERT INTO groups_tbl (user_id, name, description, mode, aggregator_agent_id)
        VALUES (%s, %s, %s, %s, %s) RETURNING id
        """,
        (
            current_user_id(), name, d.get("description"),
            mode, d.get("aggregator_agent_id"),
        ),
    )
    # Optional initial members
    for i, aid in enumerate(d.get("member_agent_ids") or []):
        db.execute(
            """
            INSERT INTO group_members (group_id, agent_id, position)
            VALUES (%s, %s, %s)
            """,
            (gid, int(aid), i),
        )
    return jsonify({"id": gid})


@app.route("/api/groups/<int:gid>", methods=["PUT"])
@login_required
def update_group(gid: int):
    if not db.fetch_one(
        "SELECT id FROM groups_tbl WHERE id = %s AND user_id = %s",
        (gid, current_user_id()),
    ):
        return jsonify({"error": "not found"}), 404
    d = request.get_json() or {}
    fields, params = [], []
    for k in ("name", "description", "mode", "aggregator_agent_id"):
        if k in d:
            fields.append(f"{k} = %s")
            params.append(d[k])
    if fields:
        params.append(gid)
        db.execute(
            f"UPDATE groups_tbl SET {', '.join(fields)} WHERE id = %s",
            tuple(params),
        )
    # Replace members if provided
    if "member_agent_ids" in d:
        db.execute("DELETE FROM group_members WHERE group_id = %s", (gid,))
        for i, aid in enumerate(d.get("member_agent_ids") or []):
            db.execute(
                "INSERT INTO group_members (group_id, agent_id, position) VALUES (%s, %s, %s)",
                (gid, int(aid), i),
            )
    return jsonify({"ok": True})


@app.route("/api/groups/<int:gid>", methods=["DELETE"])
@login_required
def delete_group(gid: int):
    db.execute(
        "DELETE FROM groups_tbl WHERE id = %s AND user_id = %s",
        (gid, current_user_id()),
    )
    return jsonify({"ok": True})


# ============================================================================
# Projects — long-lived goals wrapping runs + agents + a coordinator.
# ============================================================================

from .services import quotas as _quotas  # noqa: E402


def _fetch_project_with_members(pid: int, user_id: int) -> dict | None:
    p = db.fetch_one(
        "SELECT * FROM projects WHERE id = %s AND user_id = %s",
        (pid, user_id),
    )
    if not p:
        return None
    p["members"] = db.fetch_all(
        """
        SELECT pm.id, pm.agent_id, pm.daily_alloc_pct, pm.monthly_alloc_pct,
               a.name AS agent_name, a.role_title, a.avatar_config
        FROM project_members pm
        JOIN agents a ON a.id = pm.agent_id
        WHERE pm.project_id = %s
        ORDER BY pm.id
        """,
        (pid,),
    )
    return p


@app.route("/api/projects", methods=["GET"])
@login_required
def list_projects():
    status_filter = request.args.get("status")
    where = "user_id = %s"
    params: list = [current_user_id()]
    if status_filter:
        where += " AND status = %s"
        params.append(status_filter)
    rows = db.fetch_all(
        f"""
        SELECT p.*,
               (SELECT COUNT(*) FROM project_members pm WHERE pm.project_id = p.id) AS member_count,
               (SELECT COALESCE(SUM(cost_usd), 0)::float FROM run_steps
                WHERE project_id = p.id
                  AND started_at >= NOW() - INTERVAL '1 day') AS today_cost,
               (SELECT COUNT(*) FROM runs WHERE project_id = p.id) AS runs_count
        FROM projects p
        WHERE {where}
        ORDER BY p.id DESC
        """,
        tuple(params),
    )
    return jsonify(rows)


@app.route("/api/projects/<int:pid>", methods=["GET"])
@login_required
def get_project(pid: int):
    p = _fetch_project_with_members(pid, current_user_id())
    if not p:
        return jsonify({"error": "not found"}), 404
    # Include recent runs
    p["recent_runs"] = db.fetch_all(
        """
        SELECT r.id, r.status, r.started_at, r.finished_at,
               r.total_cost_usd, w.name AS workflow_name
        FROM runs r
        LEFT JOIN workflows w ON w.id = r.workflow_id
        WHERE r.project_id = %s
        ORDER BY r.id DESC LIMIT 20
        """,
        (pid,),
    )
    return jsonify(p)


@app.route("/api/projects", methods=["POST"])
@login_required
def create_project():
    d = request.get_json() or {}
    name = (d.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name required"}), 400
    pid = db.execute_returning(
        """
        INSERT INTO projects (user_id, name, description, goal, status, coordinator_agent_id)
        VALUES (%s, %s, %s, %s, %s, %s) RETURNING id
        """,
        (
            current_user_id(),
            name,
            d.get("description"),
            d.get("goal"),
            d.get("status", "active"),
            d.get("coordinator_agent_id"),
        ),
    )
    for m in d.get("members") or []:
        if not m.get("agent_id"):
            continue
        db.execute(
            """
            INSERT INTO project_members
                (project_id, agent_id, daily_alloc_pct, monthly_alloc_pct)
            VALUES (%s, %s, %s, %s)
            """,
            (pid, int(m["agent_id"]),
             float(m.get("daily_alloc_pct", 100.0)),
             float(m.get("monthly_alloc_pct", 100.0))),
        )
    _log_project_event(pid, "created",
                       {"name": name, "member_count": len(d.get("members") or [])})
    return jsonify({"id": pid})


@app.route("/api/projects/<int:pid>", methods=["PUT"])
@login_required
def update_project(pid: int):
    if not db.fetch_one(
        "SELECT id FROM projects WHERE id = %s AND user_id = %s",
        (pid, current_user_id()),
    ):
        return jsonify({"error": "not found"}), 404
    d = request.get_json() or {}
    fields, params = [], []
    for k in ("name", "description", "goal", "status", "coordinator_agent_id"):
        if k in d:
            fields.append(f"{k} = %s")
            params.append(d[k])
    if fields:
        fields.append("updated_at = NOW()")
        params.append(pid)
        db.execute(
            f"UPDATE projects SET {', '.join(fields)} WHERE id = %s",
            tuple(params),
        )
        # Log status / coordinator changes explicitly (most interesting).
        if "status" in d:
            _log_project_event(pid, "status_changed", {"to": d["status"]})
        if "coordinator_agent_id" in d:
            _log_project_event(pid, "coordinator_changed",
                               {"to_agent_id": d["coordinator_agent_id"]})
    if "members" in d:
        db.execute("DELETE FROM project_members WHERE project_id = %s", (pid,))
        for m in d["members"] or []:
            if not m.get("agent_id"):
                continue
            db.execute(
                """INSERT INTO project_members
                   (project_id, agent_id, daily_alloc_pct, monthly_alloc_pct)
                   VALUES (%s, %s, %s, %s)""",
                (pid, int(m["agent_id"]),
                 float(m.get("daily_alloc_pct", 100.0)),
                 float(m.get("monthly_alloc_pct", 100.0))),
            )
        _log_project_event(pid, "members_updated", {"member_count": len(d["members"] or [])})
    return jsonify({"ok": True})


@app.route("/api/projects/<int:pid>", methods=["DELETE"])
@login_required
def delete_project(pid: int):
    db.execute(
        "DELETE FROM projects WHERE id = %s AND user_id = %s",
        (pid, current_user_id()),
    )
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Project coordinator chat. Uses lead_agent's prompt/workflow plumbing but
# scopes the team roster + adds project context + quota hints.
# ---------------------------------------------------------------------------

from .services import coordinator as _coord  # noqa: E402


@app.route("/api/projects/<int:pid>/chat/thread", methods=["GET"])
@login_required
def project_chat_thread(pid: int):
    if not db.fetch_one(
        "SELECT id FROM projects WHERE id = %s AND user_id = %s",
        (pid, current_user_id()),
    ):
        return jsonify({"error": "not found"}), 404
    tid = _coord.get_or_create_thread(current_user_id(), pid)
    return jsonify({"thread_id": tid})


@app.route("/api/projects/<int:pid>/chat", methods=["POST"])
@login_required
def project_chat(pid: int):
    from .services.sanitize import clean_text
    d = request.get_json() or {}
    msg = clean_text(d.get("message", ""), max_len=10_000)
    if not msg:
        return jsonify({"error": "message required"}), 400
    if not _chat_rate_ok(current_user_id(), f"project-{pid}"):
        return jsonify({"error": "too many messages — slow down for a minute"}), 429
    if not db.fetch_one(
        "SELECT id FROM projects WHERE id = %s AND user_id = %s",
        (pid, current_user_id()),
    ):
        return jsonify({"error": "not found"}), 404
    thread_id = d.get("thread_id") or _coord.get_or_create_thread(current_user_id(), pid)
    result = _coord.chat(current_user_id(), pid, msg, thread_id=thread_id)
    return jsonify(result)


# --- Milestones ------------------------------------------------------------

@app.route("/api/projects/<int:pid>/milestones", methods=["GET"])
@login_required
def list_milestones(pid: int):
    if not db.fetch_one(
        "SELECT id FROM projects WHERE id = %s AND user_id = %s",
        (pid, current_user_id()),
    ):
        return jsonify({"error": "not found"}), 404
    rows = db.fetch_all(
        "SELECT * FROM project_milestones WHERE project_id = %s "
        "ORDER BY position, id",
        (pid,),
    )
    return jsonify(rows)


@app.route("/api/projects/<int:pid>/milestones", methods=["POST"])
@login_required
def create_milestone(pid: int):
    if not db.fetch_one(
        "SELECT id FROM projects WHERE id = %s AND user_id = %s",
        (pid, current_user_id()),
    ):
        return jsonify({"error": "not found"}), 404
    d = request.get_json() or {}
    title = (d.get("title") or "").strip()
    if not title:
        return jsonify({"error": "title required"}), 400
    # Append — position = max+1
    pos_row = db.fetch_one(
        "SELECT COALESCE(MAX(position), -1) + 1 AS next_pos "
        "FROM project_milestones WHERE project_id = %s",
        (pid,),
    ) or {}
    mid = db.execute_returning(
        """INSERT INTO project_milestones
           (project_id, position, title, description, status, due_date)
           VALUES (%s, %s, %s, %s, %s, %s) RETURNING id""",
        (pid, int(pos_row.get("next_pos") or 0), title,
         d.get("description"), d.get("status", "pending"),
         d.get("due_date")),
    )
    _log_project_event(pid, "milestone_added", {"milestone_id": mid, "title": title})
    return jsonify({"id": mid})


@app.route("/api/projects/<int:pid>/milestones/<int:mid>", methods=["PUT"])
@login_required
def update_milestone(pid: int, mid: int):
    if not db.fetch_one(
        "SELECT id FROM projects WHERE id = %s AND user_id = %s",
        (pid, current_user_id()),
    ):
        return jsonify({"error": "not found"}), 404
    d = request.get_json() or {}
    fields, params = [], []
    for k in ("title", "description", "status", "due_date", "position"):
        if k in d:
            fields.append(f"{k} = %s")
            params.append(d[k])
    if fields:
        params.extend([mid, pid])
        db.execute(
            f"UPDATE project_milestones SET {', '.join(fields)} "
            f"WHERE id = %s AND project_id = %s",
            tuple(params),
        )
        if "status" in d:
            _log_project_event(pid, "milestone_status_changed",
                               {"milestone_id": mid, "to": d["status"]})
    return jsonify({"ok": True})


@app.route("/api/projects/<int:pid>/milestones/<int:mid>", methods=["DELETE"])
@login_required
def delete_milestone(pid: int, mid: int):
    if not db.fetch_one(
        "SELECT id FROM projects WHERE id = %s AND user_id = %s",
        (pid, current_user_id()),
    ):
        return jsonify({"error": "not found"}), 404
    db.execute(
        "DELETE FROM project_milestones WHERE id = %s AND project_id = %s",
        (mid, pid),
    )
    return jsonify({"ok": True})


def _log_project_event(project_id: int, event_type: str, payload: dict | None = None,
                       actor: str | None = None) -> None:
    db.execute(
        """INSERT INTO project_events (project_id, actor, event_type, payload)
           VALUES (%s, %s, %s, %s::jsonb)""",
        (project_id, actor, event_type, json.dumps(payload or {})),
    )


@app.route("/api/projects/<int:pid>/events", methods=["GET"])
@login_required
def list_project_events(pid: int):
    if not db.fetch_one(
        "SELECT id FROM projects WHERE id = %s AND user_id = %s",
        (pid, current_user_id()),
    ):
        return jsonify({"error": "not found"}), 404
    rows = db.fetch_all(
        "SELECT id, actor, event_type, payload, created_at FROM project_events "
        "WHERE project_id = %s ORDER BY id DESC LIMIT 100",
        (pid,),
    )
    return jsonify(rows)


@app.route("/api/projects/<int:pid>/artifacts", methods=["GET"])
@login_required
def list_project_artifacts(pid: int):
    """HTML / slides / file / markdown artifacts produced inside this project.
    Populated by lead_agent.chat() whenever the coordinator emits an
    artifact fence during a project chat."""
    if not db.fetch_one(
        "SELECT id FROM projects WHERE id = %s AND user_id = %s",
        (pid, current_user_id()),
    ):
        return jsonify({"error": "not found"}), 404
    rows = db.fetch_all(
        """
        SELECT pa.id, pa.agent_id, pa.source, pa.source_ref,
               pa.kind, pa.title, pa.payload, pa.created_at,
               a.name AS agent_name, a.role_title AS agent_role
        FROM project_artifacts pa
        LEFT JOIN agents a ON a.id = pa.agent_id
        WHERE pa.project_id = %s
        ORDER BY pa.id DESC
        LIMIT 100
        """,
        (pid,),
    )
    return jsonify(rows)


@app.route("/api/projects/<int:pid>/outputs", methods=["GET"])
@login_required
def list_project_outputs(pid: int):
    """Every run of this project with a non-empty final_output, newest first."""
    if not db.fetch_one(
        "SELECT id FROM projects WHERE id = %s AND user_id = %s",
        (pid, current_user_id()),
    ):
        return jsonify({"error": "not found"}), 404
    rows = db.fetch_all(
        """
        SELECT r.id AS run_id, r.status, r.started_at, r.finished_at,
               r.total_cost_usd,
               w.name AS workflow_name,
               r.final_output
        FROM runs r
        LEFT JOIN workflows w ON w.id = r.workflow_id
        WHERE r.project_id = %s
          AND r.final_output IS NOT NULL
          AND LENGTH(r.final_output) > 0
        ORDER BY r.id DESC LIMIT 50
        """,
        (pid,),
    )
    return jsonify(rows)


@app.route("/api/runs/<int:rid>/output/download")
@login_required
def download_run_output(rid: int):
    """Download a single run's final_output as a markdown file."""
    row = db.fetch_one(
        "SELECT r.final_output, w.name FROM runs r "
        "LEFT JOIN workflows w ON w.id = r.workflow_id "
        "WHERE r.id = %s AND r.user_id = %s",
        (rid, current_user_id()),
    )
    if not row or not row.get("final_output"):
        return jsonify({"error": "no output"}), 404
    from flask import Response
    name = (row.get("name") or f"run_{rid}").replace("/", "_")[:80]
    return Response(
        row["final_output"],
        mimetype="text/markdown",
        headers={"Content-Disposition": f'attachment; filename="{name}.md"'},
    )


@app.route("/api/projects/<int:pid>/reports/<int:rid>/download")
@login_required
def download_project_report(pid: int, rid: int):
    if not db.fetch_one(
        "SELECT id FROM projects WHERE id = %s AND user_id = %s",
        (pid, current_user_id()),
    ):
        return jsonify({"error": "not found"}), 404
    row = db.fetch_one(
        "SELECT summary_md, report_date FROM project_reports "
        "WHERE id = %s AND project_id = %s",
        (rid, pid),
    )
    if not row:
        return jsonify({"error": "no report"}), 404
    from flask import Response
    return Response(
        row["summary_md"] or "",
        mimetype="text/markdown",
        headers={"Content-Disposition": f'attachment; filename="report-{row["report_date"]}.md"'},
    )


@app.route("/api/projects/<int:pid>/outputs/<int:rid>/download")
@login_required
def download_project_output(pid: int, rid: int):
    if not db.fetch_one(
        "SELECT id FROM projects WHERE id = %s AND user_id = %s",
        (pid, current_user_id()),
    ):
        return jsonify({"error": "not found"}), 404
    row = db.fetch_one(
        "SELECT r.final_output, w.name AS workflow_name "
        "FROM runs r LEFT JOIN workflows w ON w.id = r.workflow_id "
        "WHERE r.id = %s AND r.project_id = %s",
        (rid, pid),
    )
    if not row or not row.get("final_output"):
        return jsonify({"error": "no output"}), 404
    name = (row.get("workflow_name") or f"run_{rid}").replace("/", "_")[:80]
    from flask import Response
    return Response(
        row["final_output"],
        mimetype="text/markdown",
        headers={"Content-Disposition": f'attachment; filename="{name}.md"'},
    )


@app.route("/api/projects/<int:pid>/reports", methods=["GET"])
@login_required
def list_project_reports_route(pid: int):
    if not db.fetch_one(
        "SELECT id FROM projects WHERE id = %s AND user_id = %s",
        (pid, current_user_id()),
    ):
        return jsonify({"error": "not found"}), 404
    from .services import project_reports as _pr
    return jsonify(_pr.list_reports(pid))


@app.route("/api/projects/<int:pid>/reports/generate", methods=["POST"])
@login_required
def generate_project_report_route(pid: int):
    if not db.fetch_one(
        "SELECT id FROM projects WHERE id = %s AND user_id = %s",
        (pid, current_user_id()),
    ):
        return jsonify({"error": "not found"}), 404
    from .services import project_reports as _pr
    d = request.get_json(silent=True) or {}
    result = _pr.generate(pid, force=bool(d.get("force", False)))
    if result.get("error"):
        return jsonify(result), 400
    return jsonify(result)


@app.route("/api/projects/<int:pid>/chat/messages", methods=["GET"])
@login_required
def project_chat_messages(pid: int):
    if not db.fetch_one(
        "SELECT id FROM projects WHERE id = %s AND user_id = %s",
        (pid, current_user_id()),
    ):
        return jsonify({"error": "not found"}), 404
    thread_id = _coord.get_or_create_thread(current_user_id(), pid)
    msgs = _coord.list_messages(thread_id)
    return jsonify({"thread_id": thread_id, "messages": msgs})


# ---------------------------------------------------------------------------
# Minimal per-user sliding-window rate limiter for chat endpoints.
# In-memory, single process — fine for personal + small team. A real Redis
# limiter would replace this in a multi-instance deploy.
# ---------------------------------------------------------------------------

from collections import defaultdict, deque as _deque  # noqa: E402
import time as _time  # noqa: E402

_CHAT_LIMIT_WINDOW_S = 60
_CHAT_LIMIT_REQS = 20
_rate_buckets: dict = defaultdict(_deque)


def _chat_rate_ok(user_id: int, route: str) -> bool:
    key = f"{user_id}:{route}"
    bucket = _rate_buckets[key]
    now = _time.time()
    while bucket and bucket[0] < now - _CHAT_LIMIT_WINDOW_S:
        bucket.popleft()
    if len(bucket) >= _CHAT_LIMIT_REQS:
        return False
    bucket.append(now)
    return True


@app.route("/api/search")
@login_required
def global_search():
    """Unified search across lead threads, runs (final_output), and project
    reports. Returns {threads, runs, reports} ranked by recency. Uses ILIKE
    for portability — fine for a personal-scale dataset.
    """
    q = (request.args.get("q") or "").strip()
    if not q:
        return jsonify({"threads": [], "runs": [], "reports": []})
    pattern = f"%{q}%"
    uid = current_user_id()

    threads = db.fetch_all(
        """
        SELECT DISTINCT c.thread_id, c.title, c.updated_at
        FROM lead_conversations c
        JOIN lead_messages m ON m.thread_id = c.thread_id
        WHERE c.user_id = %s
          AND c.thread_id NOT LIKE 'proj-%%'
          AND m.content ILIKE %s
        ORDER BY c.updated_at DESC LIMIT 15
        """,
        (uid, pattern),
    )
    runs = db.fetch_all(
        """
        SELECT id, workflow_id, status, started_at, SUBSTRING(final_output FOR 200) AS snippet
        FROM runs
        WHERE user_id = %s AND final_output ILIKE %s
        ORDER BY id DESC LIMIT 15
        """,
        (uid, pattern),
    )
    reports = db.fetch_all(
        """
        SELECT pr.id, pr.project_id, pr.report_date,
               SUBSTRING(pr.summary_md FOR 200) AS snippet,
               p.name AS project_name
        FROM project_reports pr
        JOIN projects p ON p.id = pr.project_id
        WHERE p.user_id = %s AND pr.summary_md ILIKE %s
        ORDER BY pr.report_date DESC LIMIT 15
        """,
        (uid, pattern),
    )
    return jsonify({"query": q, "threads": threads, "runs": runs, "reports": reports})


@app.route("/api/dashboard/quota_overview")
@login_required
def dashboard_quota_overview():
    """List agents whose daily cost OR token usage is >= 80% of their cap.
    Used by the Dashboard's 'agents near quota' block.
    """
    rows = db.fetch_all(
        """
        SELECT a.id, a.name, a.role_title, a.avatar_config,
               a.daily_cost_quota, a.daily_token_quota,
               COALESCE((
                   SELECT SUM(cost_usd)::float FROM run_steps
                   WHERE agent_id = a.id AND started_at >= NOW() - INTERVAL '1 day'
               ), 0)::float AS today_cost,
               COALESCE((
                   SELECT SUM(input_tokens + output_tokens) FROM run_steps
                   WHERE agent_id = a.id AND started_at >= NOW() - INTERVAL '1 day'
               ), 0)::bigint AS today_tokens
        FROM agents a
        WHERE a.user_id = %s
          AND (a.daily_cost_quota IS NOT NULL OR a.daily_token_quota IS NOT NULL)
        """,
        (current_user_id(),),
    )
    near = []
    for r in rows:
        pct_cost = (float(r["today_cost"]) / float(r["daily_cost_quota"])) if r.get("daily_cost_quota") else 0
        pct_tok = (int(r["today_tokens"]) / int(r["daily_token_quota"])) if r.get("daily_token_quota") else 0
        pct = max(pct_cost, pct_tok)
        if pct >= 0.8:
            near.append({
                **r,
                "today_cost": float(r["today_cost"]),
                "today_tokens": int(r["today_tokens"]),
                "pct": pct,
            })
    near.sort(key=lambda x: -x["pct"])
    return jsonify(near)


@app.route("/api/me/autotopup/events", methods=["GET"])
@login_required
def my_autotopup_events():
    from .services import quotas as _q
    days = max(1, min(60, int(request.args.get("days", 14))))
    return jsonify(_q.list_autotopup_events(current_user_id(), days=days))


@app.route("/api/me/api-tokens", methods=["GET", "POST"])
@login_required
def my_api_tokens():
    if request.method == "GET":
        rows = db.fetch_all(
            "SELECT id, name, created_at, last_used_at "
            "FROM api_tokens WHERE user_id = %s ORDER BY id DESC",
            (current_user_id(),),
        )
        return jsonify(rows)
    # Create
    import hashlib
    d = request.get_json() or {}
    name = (d.get("name") or "Unnamed token").strip()[:100]
    raw = "hlns_" + secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    tid = db.execute_returning(
        "INSERT INTO api_tokens (user_id, name, token_hash) "
        "VALUES (%s, %s, %s) RETURNING id",
        (current_user_id(), name, token_hash),
    )
    # Return the raw token ONCE — caller must save it.
    return jsonify({"id": tid, "name": name, "token": raw,
                    "note": "Save this token — it won't be shown again."})


@app.route("/api/me/api-tokens/<int:tid>", methods=["DELETE"])
@login_required
def delete_api_token(tid: int):
    db.execute(
        "DELETE FROM api_tokens WHERE id = %s AND user_id = %s",
        (tid, current_user_id()),
    )
    return jsonify({"ok": True})


@app.route("/api/me/webhook", methods=["GET", "PUT"])
@login_required
def my_webhook():
    if request.method == "GET":
        row = db.fetch_one(
            "SELECT report_webhook_url FROM as_users WHERE id = %s",
            (current_user_id(),),
        ) or {}
        return jsonify({"report_webhook_url": row.get("report_webhook_url") or ""})
    d = request.get_json() or {}
    url = (d.get("report_webhook_url") or "").strip() or None
    if url and not url.startswith(("http://", "https://")):
        return jsonify({"error": "url must start with http:// or https://"}), 400
    db.execute(
        "UPDATE as_users SET report_webhook_url = %s WHERE id = %s",
        (url, current_user_id()),
    )
    return jsonify({"ok": True})


@app.route("/api/me/autotopup", methods=["GET", "PUT"])
@login_required
def my_autotopup():
    if request.method == "GET":
        row = db.fetch_one(
            "SELECT auto_topup_enabled, auto_topup_per_topup_cost, "
            "auto_topup_max_per_day FROM as_users WHERE id = %s",
            (current_user_id(),),
        ) or {}
        return jsonify({
            "enabled": bool(row.get("auto_topup_enabled")),
            "per_topup_cost": float(row.get("auto_topup_per_topup_cost") or 1.0),
            "max_per_day": int(row.get("auto_topup_max_per_day") or 3),
        })
    d = request.get_json() or {}
    # Honor global hard caps from the quotas service.
    from .services.quotas import MAX_AUTO_TOPUP_PER_DAY, MAX_AUTO_TOPUP_COST_USD
    per = min(float(d.get("per_topup_cost", 1.0)), MAX_AUTO_TOPUP_COST_USD)
    max_per_day = min(int(d.get("max_per_day", 3)), MAX_AUTO_TOPUP_PER_DAY)
    db.execute(
        """UPDATE as_users SET auto_topup_enabled = %s,
               auto_topup_per_topup_cost = %s,
               auto_topup_max_per_day = %s
           WHERE id = %s""",
        (bool(d.get("enabled")), per, max_per_day, current_user_id()),
    )
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Usage aggregation — feeds daily stack-bar charts across dashboard,
# project detail, agent detail, workflow detail. One endpoint, four shapes.
# ---------------------------------------------------------------------------

@app.route("/api/usage/daily")
@login_required
def usage_daily():
    """Return per-day token + cost totals, grouped by one of:
        project | agent | group | workflow | model_client
    Optional scope filters restrict rows to a single parent, e.g.
    `?group_by=agent&project_id=4` returns a per-agent timeseries for
    project 4. `?days=14` controls the window (default 14, max 90).

    When group_by=workflow, the label is suffixed with a 📅 marker if the
    workflow has any schedule row so the chart legend tells
    scheduled-triggered runs apart from manual ones at a glance.
    """
    group_by = (request.args.get("group_by") or "project").lower()
    days = max(1, min(90, int(request.args.get("days", 14))))
    project_id = request.args.get("project_id")
    agent_id = request.args.get("agent_id")
    workflow_id = request.args.get("workflow_id")

    # Workflow labels get annotated with a 📅 when any schedule points at
    # them — helpful for distinguishing background recurring work from
    # ad-hoc dispatches on the same project page.
    workflow_label = (
        "CASE WHEN EXISTS (SELECT 1 FROM schedules s WHERE s.workflow_id = w.id) "
        "THEN w.name || ' 📅' ELSE w.name END"
    )
    key_sql_map = {
        "project":      ("rs.project_id",      "COALESCE(p.name, '(adhoc)')"),
        "agent":        ("rs.agent_id",        "a.name"),
        "group":        ("rs.group_id",        "COALESCE(g.name, '(no group)')"),
        "workflow":     ("r.workflow_id",      workflow_label),
        "model_client": ("a.model_client_id",  "COALESCE(mc.name, '(no client)')"),
    }
    if group_by not in key_sql_map:
        return jsonify({"error": f"invalid group_by: {group_by}"}), 400
    key_expr, label_expr = key_sql_map[group_by]

    filters = ["r.user_id = %s", "rs.started_at >= NOW() - (%s * INTERVAL '1 day')"]
    params: list = [current_user_id(), days]
    if project_id is not None:
        if project_id == "null":
            filters.append("rs.project_id IS NULL")
        else:
            filters.append("rs.project_id = %s")
            params.append(int(project_id))
    if agent_id is not None:
        filters.append("rs.agent_id = %s")
        params.append(int(agent_id))
    if workflow_id is not None:
        filters.append("r.workflow_id = %s")
        params.append(int(workflow_id))
    where = " AND ".join(filters)

    sql = f"""
        SELECT DATE(rs.started_at) AS date,
               {key_expr}          AS key,
               {label_expr}        AS label,
               SUM(rs.input_tokens + rs.output_tokens)::bigint AS tokens,
               SUM(rs.cost_usd)::float                          AS cost
        FROM run_steps rs
        JOIN runs r              ON r.id = rs.run_id
        LEFT JOIN agents a       ON a.id = rs.agent_id
        LEFT JOIN groups_tbl g   ON g.id = rs.group_id
        LEFT JOIN projects p     ON p.id = rs.project_id
        LEFT JOIN workflows w    ON w.id = r.workflow_id
        LEFT JOIN model_clients mc ON mc.id = a.model_client_id
        WHERE {where}
        GROUP BY date, key, label
        ORDER BY date ASC, key ASC
    """
    rows = db.fetch_all(sql, tuple(params))
    return jsonify({
        "group_by": group_by,
        "days": days,
        "rows": [
            {
                "date": str(r["date"]),
                "key": r.get("key"),
                "label": r.get("label") or "(unlabeled)",
                "tokens": int(r.get("tokens") or 0),
                "cost": float(r.get("cost") or 0),
            }
            for r in rows
        ],
    })


# ---------------------------------------------------------------------------
# Group chat — user sits in a room with the group's members.
# ---------------------------------------------------------------------------

from .services import group_chat as _group_chat  # noqa: E402


@app.route("/api/groups/<int:gid>/chat/thread", methods=["GET"])
@login_required
def group_chat_thread(gid: int):
    """Get (or create) the active chat thread for this (user, group)."""
    if not _fetch_group_with_members(gid, current_user_id()):
        return jsonify({"error": "group not found"}), 404
    thread_id = _group_chat.get_or_create_thread(current_user_id(), gid)
    return jsonify({"thread_id": thread_id})


@app.route("/api/group-chat/<int:thread_id>/messages", methods=["GET"])
@login_required
def group_chat_messages(thread_id: int):
    t = db.fetch_one(
        "SELECT id, group_id FROM group_chat_threads WHERE id = %s AND user_id = %s",
        (thread_id, current_user_id()),
    )
    if not t:
        return jsonify({"error": "not found"}), 404
    msgs = _group_chat.list_messages(thread_id)
    return jsonify({"thread_id": thread_id, "group_id": t["group_id"], "messages": msgs})


@app.route("/api/group-chat/<int:thread_id>/send", methods=["POST"])
@login_required
def group_chat_send(thread_id: int):
    d = request.get_json() or {}
    msg = (d.get("message") or "").strip()
    if not msg:
        return jsonify({"error": "message required"}), 400
    if not _chat_rate_ok(current_user_id(), f"group-chat-{thread_id}"):
        return jsonify({"error": "too many messages — slow down for a minute"}), 429
    t = db.fetch_one(
        "SELECT id, group_id FROM group_chat_threads WHERE id = %s AND user_id = %s",
        (thread_id, current_user_id()),
    )
    if not t:
        return jsonify({"error": "not found"}), 404
    result = _group_chat.send_user_message(current_user_id(), t["group_id"], thread_id, msg)
    if result.get("error"):
        return jsonify(result), 400
    return jsonify(result)


@app.route("/api/group-chat/<int:thread_id>/continue", methods=["POST"])
@login_required
def group_chat_continue(thread_id: int):
    d = request.get_json() or {}
    rounds = int(d.get("rounds") or 1)
    t = db.fetch_one(
        "SELECT id, group_id FROM group_chat_threads WHERE id = %s AND user_id = %s",
        (thread_id, current_user_id()),
    )
    if not t:
        return jsonify({"error": "not found"}), 404
    result = _group_chat.continue_rounds(current_user_id(), t["group_id"], thread_id, rounds)
    if result.get("error"):
        return jsonify(result), 400
    return jsonify(result)


# ============================================================================
# Runs
# ============================================================================

@app.route("/api/runs")
@login_required
def list_runs():
    """Cursor-paginated runs list. Use `?before_id=N` to fetch older rows.

    The original route hard-coded LIMIT 50, silently hiding everything
    after the 50th most-recent run. We now return a stable page of
    `limit` rows (default 50, max 200) plus a `has_more` flag so the
    frontend can do infinite scroll.
    """
    try:
        limit = int(request.args.get("limit", 50))
    except (TypeError, ValueError):
        limit = 50
    limit = max(1, min(limit, 200))
    before_raw = request.args.get("before_id")
    before_id: int | None = None
    if before_raw:
        try:
            before_id = int(before_raw)
        except (TypeError, ValueError):
            before_id = None

    params: list = [current_user_id()]
    before_clause = ""
    if before_id is not None:
        before_clause = " AND r.id < %s"
        params.append(before_id)
    params.append(limit + 1)

    rows = db.fetch_all(
        f"""
        SELECT r.id, r.workflow_id, r.user_id, r.initial_input, r.final_output,
               r.status, r.started_at, r.finished_at,
               r.total_cost_usd::float AS total_cost_usd,
               r.total_input_tokens, r.total_output_tokens, r.iterations,
               w.name AS workflow_name
        FROM runs r
        LEFT JOIN workflows w ON w.id = r.workflow_id
        WHERE r.user_id = %s{before_clause}
        ORDER BY r.id DESC
        LIMIT %s
        """,
        tuple(params),
    )
    has_more = len(rows) > limit
    if has_more:
        rows = rows[:limit]
    return jsonify({"runs": rows, "has_more": has_more})


@app.route("/api/runs/<int:run_id>")
@login_required
def get_run(run_id: int):
    r = db.fetch_one(
        """
        SELECT r.*, r.total_cost_usd::float AS total_cost_usd,
               w.name AS workflow_name
        FROM runs r
        LEFT JOIN workflows w ON w.id = r.workflow_id
        WHERE r.id = %s AND r.user_id = %s
        """,
        (run_id, current_user_id()),
    )
    if not r:
        return jsonify({"error": "not found"}), 404
    r["steps"] = db.fetch_all(
        """
        SELECT id, run_id, iteration, node_position, group_id, agent_id,
               role_label, prompt, system_prompt, response, model_id,
               model_provider, input_tokens, output_tokens,
               cost_usd::float AS cost_usd,
               duration_ms, error, turn, tool_calls, started_at
        FROM run_steps WHERE run_id = %s ORDER BY id
        """,
        (run_id,),
    )
    r["tasks"] = db.fetch_all(
        "SELECT id, agent_id, priority, status, created_at, started_at, finished_at, error_message FROM agent_tasks WHERE run_id = %s ORDER BY id",
        (run_id,),
    )
    return jsonify(r)


@app.route("/api/runs/<int:run_id>/stop", methods=["POST"])
@login_required
def stop_run(run_id: int):
    """Hot stop — mark run as cancelling, cancel queued tasks."""
    r = db.fetch_one("SELECT id FROM runs WHERE id = %s AND user_id = %s",
                     (run_id, current_user_id()))
    if not r:
        return jsonify({"error": "not found"}), 404
    cancelled = queue.cancel_run(run_id)
    return jsonify({"ok": True, "cancelled_tasks": cancelled})


# ============================================================================
# Models
# ============================================================================

@app.route("/api/models")
@login_required
def get_models():
    return jsonify(list_models())


@app.route("/api/tools")
@login_required
def get_tools():
    """List every built-in tool the deployment offers, for the per-agent
    tool-config picker UI."""
    return jsonify(tools_registry.describe_all())


# ============================================================================
# Lead Agent (chat)
# ============================================================================

@app.route("/api/lead/chat", methods=["POST"])
@login_required
def lead_chat():
    from .services.sanitize import clean_text
    d = request.get_json() or {}
    message = clean_text(d.get("message", ""), max_len=10_000)
    thread_id = d.get("thread_id")
    if not message:
        return jsonify({"error": "message required"}), 400
    if not _chat_rate_ok(current_user_id(), "lead"):
        return jsonify({"error": "too many messages — slow down for a minute"}), 429
    result = lead_agent.chat(
        current_user_id(), message,
        thread_id=thread_id,
        project_id=d.get("project_id"),
    )
    return jsonify(result)


@app.route("/api/lead/threads")
@login_required
def lead_threads():
    return jsonify(lead_agent.list_threads(current_user_id()))


@app.route("/api/lead/pending_count")
@login_required
def lead_pending_count_route():
    return jsonify({"count": lead_agent.lead_pending_count(current_user_id())})


@app.route("/api/lead/threads/<thread_id>/messages")
@login_required
def lead_thread_messages(thread_id):
    try:
        limit = int(request.args.get("limit", 20))
    except (TypeError, ValueError):
        limit = 20
    before_raw = request.args.get("before_id")
    before_id = None
    if before_raw:
        try:
            before_id = int(before_raw)
        except (TypeError, ValueError):
            before_id = None
    return jsonify(
        lead_agent.get_thread_messages(
            current_user_id(), thread_id, limit=limit, before_id=before_id
        )
    )


@app.route("/api/lead/proxy_responses")
@login_required
def lead_proxy_responses_route():
    """List proxy answers generated for the current user. Feeds the
    紀錄 → 代答紀錄 tab in the Records page."""
    return jsonify(lead_proxy.list_proxy_responses(current_user_id()))


@app.route("/api/lead/proxy_responses/<int:msg_id>/retract", methods=["POST"])
@login_required
def lead_proxy_retract_route(msg_id: int):
    # Ownership: only the thread owner can retract a proxy reply.
    row = db.fetch_one(
        """
        SELECT c.user_id
        FROM lead_messages m
        JOIN lead_conversations c ON c.thread_id = m.thread_id
        WHERE m.id = %s
        """,
        (msg_id,),
    )
    if not row or row["user_id"] != current_user_id():
        return jsonify({"error": "not allowed"}), 403
    if not lead_proxy.mark_retracted(msg_id, current_user_id()):
        return jsonify({"error": "not a proxy response"}), 404
    return jsonify({"ok": True})


@app.route("/api/lead/proxy_settings", methods=["GET", "PUT"])
@login_required
def lead_proxy_settings_route():
    uid = current_user_id()
    if request.method == "GET":
        row = db.fetch_one(
            """
            SELECT lead_proxy_enabled, lead_proxy_timeout_minutes,
                   lead_proxy_away_minutes
            FROM as_users WHERE id = %s
            """,
            (uid,),
        )
        return jsonify(row or {})
    d = request.get_json() or {}
    sets, params = [], []
    if "enabled" in d:
        sets.append("lead_proxy_enabled = %s")
        params.append(bool(d["enabled"]))
    if "timeout_minutes" in d:
        tm = int(d["timeout_minutes"])
        if tm < 1 or tm > 120:
            return jsonify({"error": "timeout_minutes must be 1..120"}), 400
        sets.append("lead_proxy_timeout_minutes = %s")
        params.append(tm)
    if "away_minutes" in d:
        am = int(d["away_minutes"])
        if am < 1 or am > 60:
            return jsonify({"error": "away_minutes must be 1..60"}), 400
        sets.append("lead_proxy_away_minutes = %s")
        params.append(am)
    if not sets:
        return jsonify({"error": "no fields"}), 400
    params.append(uid)
    db.execute(
        f"UPDATE as_users SET {', '.join(sets)} WHERE id = %s",
        tuple(params),
    )
    return jsonify({"ok": True})


@app.route("/api/lead/threads/<thread_id>/archive", methods=["POST"])
@login_required
def lead_archive(thread_id):
    lead_agent.archive_thread(current_user_id(), thread_id)
    return jsonify({"ok": True})


@app.route("/api/lead/hire_proposals/<int:msg_id>/accept", methods=["POST"])
@login_required
def lead_accept_hire(msg_id: int):
    """Materialise a Lead-proposed hire. Admin can override any field
    (name / role_title / description / system_prompt) via request JSON
    before the agent is created.
    """
    data = request.get_json(silent=True) or {}
    overrides = {k: data[k] for k in ("name", "role_title", "description", "system_prompt")
                  if data.get(k)}
    try:
        out = lead_agent.accept_hire_proposal(current_user_id(), msg_id, overrides)
    except ValueError as e:
        return jsonify({"error": str(e)}), 404
    return jsonify(out)


@app.route("/api/lead/project_proposals/<int:msg_id>/accept", methods=["POST"])
@login_required
def lead_accept_project(msg_id: int):
    """Materialise a Lead-proposed project. Creates the project row +
    attaches members (100% allocation default)."""
    data = request.get_json(silent=True) or {}
    overrides = {
        k: data[k]
        for k in ("name", "goal", "description", "coordinator_agent_id",
                   "member_agent_ids")
        if k in data
    }
    try:
        out = lead_agent.accept_project_proposal(current_user_id(), msg_id, overrides)
    except ValueError as e:
        return jsonify({"error": str(e)}), 404
    return jsonify(out)


# ============================================================================
# Schedules
# ============================================================================

@app.route("/api/schedules", methods=["GET"])
@login_required
def list_schedules_route():
    return jsonify(scheduler.list_schedules(current_user_id()))


@app.route("/api/schedules", methods=["POST"])
@login_required
def create_schedule_route():
    sid = scheduler.create_schedule(current_user_id(), request.get_json() or {})
    return jsonify({"id": sid})


@app.route("/api/schedules/<int:sid>/toggle", methods=["POST"])
@login_required
def toggle_schedule_route(sid):
    enabled = bool((request.get_json() or {}).get("enabled", True))
    scheduler.toggle_schedule(current_user_id(), sid, enabled)
    return jsonify({"ok": True})


@app.route("/api/schedules/<int:sid>", methods=["DELETE"])
@login_required
def delete_schedule_route(sid):
    scheduler.delete_schedule(current_user_id(), sid)
    return jsonify({"ok": True})


# ============================================================================
# Notifications
# ============================================================================

@app.route("/api/notifications")
@login_required
def list_notifications_route():
    status = request.args.get("status")
    return jsonify(notif_service.list_notifications(current_user_id(), status=status))


@app.route("/api/notifications/unread_count")
@login_required
def unread_count_route():
    return jsonify({"count": notif_service.unread_count(current_user_id())})


@app.route("/api/notifications/<int:nid>/read", methods=["POST"])
@login_required
def mark_read_route(nid):
    notif_service.mark_read(current_user_id(), nid)
    return jsonify({"ok": True})


@app.route("/api/notifications/mark_all_read", methods=["POST"])
@login_required
def mark_all_read_route():
    n = notif_service.mark_all_read(current_user_id())
    return jsonify({"ok": True, "marked": n})


@app.route("/api/notifications/<int:nid>/resolve", methods=["POST"])
@login_required
def resolve_notification_route(nid):
    resolution = (request.get_json() or {}).get("resolution", "")
    notif_service.resolve(current_user_id(), nid, resolution)
    return jsonify({"ok": True})


@app.route("/api/notifications/<int:nid>/dismiss", methods=["POST"])
@login_required
def dismiss_notification_route(nid):
    notif_service.dismiss(current_user_id(), nid)
    return jsonify({"ok": True})


# ============================================================================
# Quotas
# ============================================================================

@app.route("/api/agents/<int:aid>/quotas", methods=["GET"])
@login_required
def list_agent_quotas(aid):
    return jsonify(quotas.list_quotas(aid))


@app.route("/api/agents/<int:aid>/quotas", methods=["POST"])
@login_required
def create_agent_quota(aid):
    qid = quotas.create_quota(aid, request.get_json() or {})
    return jsonify({"id": qid})


@app.route("/api/quotas/<int:qid>", methods=["DELETE"])
@login_required
def delete_quota_route(qid):
    quotas.delete_quota(qid)
    return jsonify({"ok": True})


# ============================================================================
# Skills
# ============================================================================

@app.route("/api/agents/<int:aid>/skills")
@login_required
def list_agent_skills(aid):
    rows = db.fetch_all(
        "SELECT * FROM agent_skills WHERE agent_id = %s ORDER BY approved_by_user DESC, times_used DESC",
        (aid,),
    )
    return jsonify(rows)


@app.route("/api/agents/<int:aid>/skills/extract", methods=["POST"])
@login_required
def extract_skills_route(aid):
    saved = skill_extractor.extract_for_agent(aid)
    return jsonify({"extracted": saved})


@app.route("/api/skills/<int:sid>/approve", methods=["POST"])
@login_required
def approve_skill(sid):
    skill_extractor.approve(sid, current_user_id())
    return jsonify({"ok": True})


@app.route("/api/skills/<int:sid>/reject", methods=["POST"])
@login_required
def reject_skill(sid):
    skill_extractor.reject(sid, current_user_id())
    return jsonify({"ok": True})


@app.route("/api/agents/<int:aid>/skills/export")
@login_required
def export_skills_route(aid):
    return jsonify(skill_extractor.export_skills(aid))


# ============================================================================
# Agent sharing
# ============================================================================

@app.route("/api/agents/<int:aid>/visibility", methods=["POST"])
@login_required
def set_visibility_route(aid):
    d = request.get_json() or {}
    sharing.set_visibility(current_user_id(), aid,
                            d.get("visibility", "private"),
                            d.get("visible_user_ids"))
    return jsonify({"ok": True})


@app.route("/api/shares/out")
@login_required
def list_shares_out_route():
    return jsonify(sharing.list_shares_out(current_user_id()))


@app.route("/api/shares/in")
@login_required
def list_shares_in_route():
    return jsonify(sharing.list_shares_in(current_user_id()))


@app.route("/api/users")
@login_required
def list_users_route():
    """Lightweight user list for share pickers — excludes self."""
    rows = db.fetch_all(
        "SELECT id, username, display_name FROM as_users WHERE id <> %s ORDER BY username",
        (current_user_id(),),
    )
    return jsonify(rows)


# ============================================================================
# Admin — user management (Phase 1.2)
# ============================================================================

@app.route("/api/admin/users")
@admin_required
def admin_list_users():
    rows = db.fetch_all(
        """
        SELECT id, username, display_name, role, last_seen_at, created_at
        FROM as_users
        ORDER BY id
        """,
    )
    return jsonify(rows)


@app.route("/api/admin/users", methods=["POST"])
@admin_required
def admin_create_user():
    d = request.get_json() or {}
    username = (d.get("username") or "").strip()
    password = d.get("password") or ""
    display_name = (d.get("display_name") or username).strip()
    role = d.get("role") or "user"
    if role not in ("admin", "user"):
        return jsonify({"error": "role must be 'admin' or 'user'"}), 400
    if not username or not password:
        return jsonify({"error": "username+password required"}), 400
    if db.fetch_one("SELECT id FROM as_users WHERE username = %s", (username,)):
        return jsonify({"error": "username taken"}), 409
    uid = db.execute_returning(
        """
        INSERT INTO as_users (username, password_hash, display_name, role)
        VALUES (%s, %s, %s, %s) RETURNING id
        """,
        (username, generate_password_hash(password, method="pbkdf2:sha256"), display_name, role),
    )
    # Auto-grant every default-for-new-users model client
    from .services import model_clients as mc
    mc.on_user_created(uid)
    return jsonify({"id": uid, "username": username, "role": role})


@app.route("/api/admin/users/<int:uid>", methods=["PUT"])
@admin_required
def admin_update_user(uid: int):
    d = request.get_json() or {}
    sets, params = [], []
    if "display_name" in d:
        sets.append("display_name = %s")
        params.append(d["display_name"])
    if "role" in d:
        if d["role"] not in ("admin", "user"):
            return jsonify({"error": "invalid role"}), 400
        # Guard against demoting the last admin — always keep at least one.
        if d["role"] == "user":
            row = db.fetch_one("SELECT role FROM as_users WHERE id = %s", (uid,))
            if row and row["role"] == "admin":
                admin_count = db.fetch_one(
                    "SELECT COUNT(*) AS c FROM as_users WHERE role = 'admin'"
                )["c"]
                if admin_count <= 1:
                    return jsonify({"error": "cannot demote the last admin"}), 400
        sets.append("role = %s")
        params.append(d["role"])
    if not sets:
        return jsonify({"error": "no fields to update"}), 400
    params.append(uid)
    result = db.fetch_one(
        f"UPDATE as_users SET {', '.join(sets)}, updated_at = NOW() "
        "WHERE id = %s RETURNING id, username, display_name, role",
        tuple(params),
    )
    if not result:
        return jsonify({"error": "user not found"}), 404
    return jsonify(result)


@app.route("/api/admin/users/<int:uid>", methods=["DELETE"])
@admin_required
def admin_delete_user(uid: int):
    # Forbid self-delete so the current admin can't nuke their own session.
    if uid == current_user_id():
        return jsonify({"error": "cannot delete the current user"}), 400
    # Don't let the last admin be removed.
    row = db.fetch_one("SELECT role FROM as_users WHERE id = %s", (uid,))
    if not row:
        return jsonify({"error": "user not found"}), 404
    if row["role"] == "admin":
        admin_count = db.fetch_one(
            "SELECT COUNT(*) AS c FROM as_users WHERE role = 'admin'"
        )["c"]
        if admin_count <= 1:
            return jsonify({"error": "cannot delete the last admin"}), 400
    db.execute("DELETE FROM as_users WHERE id = %s", (uid,))
    return jsonify({"ok": True})


@app.route("/api/admin/users/<int:uid>/reset_password", methods=["POST"])
@admin_required
def admin_reset_password(uid: int):
    d = request.get_json() or {}
    new_password = d.get("new_password") or ""
    if len(new_password) < 6:
        return jsonify({"error": "password must be at least 6 characters"}), 400
    result = db.fetch_one(
        "UPDATE as_users SET password_hash = %s, updated_at = NOW() "
        "WHERE id = %s RETURNING id",
        (generate_password_hash(new_password, method="pbkdf2:sha256"), uid),
    )
    if not result:
        return jsonify({"error": "user not found"}), 404
    return jsonify({"ok": True})


# ============================================================================
# System feature flags (Phase 1.3)
# ============================================================================

@app.route("/api/system/feature_flags")
@login_required
def list_feature_flags_route():
    """Any authenticated user can READ the flag list — the frontend uses
    it to hide buttons they don't have permission to click. Writing is
    admin-only."""
    return jsonify(feature_flags.list_flags())


@app.route("/api/system/feature_flags/<feature>", methods=["PUT"])
@admin_required
def update_feature_flag_route(feature: str):
    d = request.get_json() or {}
    if "admin_only" in d:
        if not feature_flags.set_admin_only(feature, bool(d["admin_only"])):
            return jsonify({"error": f"unknown feature '{feature}'"}), 404
    if "value" in d:
        db.execute(
            "UPDATE system_feature_flags SET value = %s, updated_at = NOW() WHERE feature = %s",
            (str(d["value"]), feature),
        )
    return jsonify(feature_flags.get_flag(feature))


@app.route("/api/me/quota")
@login_required
def my_quota_route():
    return jsonify(user_quotas.summary(current_user_id()))


@app.route("/api/admin/users/<int:uid>/quota", methods=["GET"])
@login_required
def admin_get_user_quota_route(uid: int):
    """Anyone can see their own quota; admins can see anybody's."""
    if uid != current_user_id() and current_user_role() != "admin":
        return jsonify({"error": "not allowed"}), 403
    return jsonify(user_quotas.summary(uid))


@app.route("/api/admin/users/<int:uid>/quota", methods=["PUT"])
@login_required
def admin_set_user_quota_route(uid: int):
    """Admins can always edit anybody's quota. Non-admins can only
    edit their own, and only when the manage_user_quota flag is open."""
    is_admin = current_user_role() == "admin"
    if not is_admin:
        if uid != current_user_id():
            return jsonify({"error": "not allowed"}), 403
        if feature_flags.is_admin_only("manage_user_quota"):
            return jsonify({"error": "managing user quota is admin-only"}), 403
    d = request.get_json() or {}
    # Only accept keys we know about
    allowed = {
        "daily_token_limit", "daily_cost_limit_usd",
        "monthly_token_limit", "monthly_cost_limit_usd",
    }
    payload = {k: v for k, v in d.items() if k in allowed}
    return jsonify(user_quotas.set_quota(uid, payload))


@app.route("/api/audit_log")
@login_required
def list_audit_log_route():
    """Phase 5.3 audit log list endpoint. Gated by the view_audit_log
    feature flag — when admin-only, regular users get 403.

    Non-admins only see their own activity; admins see everyone's. Query
    params: user_id (admin only), method, before_id, limit (max 500).
    """
    is_admin = current_user_role() == "admin"
    if feature_flags.is_admin_only("view_audit_log") and not is_admin:
        return jsonify({"error": "audit log is admin-only"}), 403

    try:
        limit = int(request.args.get("limit", 100))
    except (TypeError, ValueError):
        limit = 100
    limit = max(1, min(limit, 500))
    before_raw = request.args.get("before_id")
    before_id: int | None = None
    if before_raw:
        try:
            before_id = int(before_raw)
        except (TypeError, ValueError):
            before_id = None

    where: list[str] = []
    params: list = []
    if is_admin:
        uid_filter = request.args.get("user_id")
        if uid_filter:
            try:
                where.append("a.user_id = %s")
                params.append(int(uid_filter))
            except ValueError:
                pass
    else:
        where.append("a.user_id = %s")
        params.append(current_user_id())

    method_filter = request.args.get("method")
    if method_filter:
        where.append("a.method = %s")
        params.append(method_filter.upper())

    if before_id is not None:
        where.append("a.id < %s")
        params.append(before_id)

    params.append(limit + 1)

    where_sql = " WHERE " + " AND ".join(where) if where else ""
    rows = db.fetch_all(
        f"""
        SELECT a.id, a.user_id, a.method, a.path, a.status_code,
               a.resource_id, a.metadata, a.created_at,
               u.username
        FROM audit_log a
        LEFT JOIN as_users u ON u.id = a.user_id
        {where_sql}
        ORDER BY a.id DESC
        LIMIT %s
        """,
        tuple(params),
    )
    has_more = len(rows) > limit
    if has_more:
        rows = rows[:limit]
    return jsonify({"entries": rows, "has_more": has_more})


# ============================================================================
# Asset library (Phase 2) — Skill / Tool / MCP / RAG
# ============================================================================
#
# The admin view (default): list every asset in the system, rolled up with
# grant / assigned-agent / usage counts.
# The regular user view: list assets they own plus assets granted to them.
#
# Create / update / delete / grant / assign are guarded by feature flags
# (see services.feature_flags DEFAULTS) so an admin can decide whether
# regular users can do these things. Admins bypass the flags entirely.

def _is_admin() -> bool:
    return current_user_role() == "admin"


def _asset_feature_allowed(feature: str) -> bool:
    """Admins can always use any feature. Non-admins only when the flag
    says the feature is open to everyone."""
    if _is_admin():
        return True
    return not feature_flags.is_admin_only(feature)


@app.route("/api/assets")
@login_required
def list_assets_route():
    """GET /api/assets?kind=mcp — list assets visible to the caller.

    Admins see every asset; regular users see owned + granted rows.
    Optional `kind` query param filters to one of skill/tool/mcp/rag.
    """
    kind = request.args.get("kind")
    if kind and kind not in assets_service.VALID_KINDS:
        return jsonify({"error": f"invalid kind {kind!r}"}), 400
    viewer = None if _is_admin() else current_user_id()
    rows = assets_service.list_assets(kind=kind, viewer_user_id=viewer)
    return jsonify(rows)


@app.route("/api/assets/<int:asset_id>")
@login_required
def get_asset_route(asset_id: int):
    row = assets_service.get_asset(asset_id)
    if not row:
        return jsonify({"error": "asset not found"}), 404
    if not _is_admin() and not assets_service.visible_to_user(asset_id, current_user_id()):
        return jsonify({"error": "asset not found"}), 404
    return jsonify(row)


@app.route("/api/assets", methods=["POST"])
@login_required
def create_asset_route():
    d = request.get_json() or {}
    kind = d.get("kind")
    # Flag gate based on kind
    if kind == "mcp" and not _asset_feature_allowed("create_mcp_server"):
        return jsonify({"error": "creating MCP servers is admin-only"}), 403
    if kind == "rag" and not _asset_feature_allowed("create_rag_source"):
        return jsonify({"error": "creating RAG sources is admin-only"}), 403
    try:
        aid = assets_service.create_asset(
            actor_user_id=current_user_id(),
            kind=kind,
            name=d.get("name") or "",
            description=d.get("description"),
            config=d.get("config") or {},
            metadata=d.get("metadata") or {},
            credential_plaintext=d.get("credential"),
            enabled=bool(d.get("enabled", True)),
        )
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"id": aid})


def _owns_or_admin(asset_id: int) -> bool:
    """Only asset owner or admin can mutate the asset."""
    if _is_admin():
        return True
    row = assets_service.get_asset(asset_id)
    return bool(row and row["owner_user_id"] == current_user_id())


@app.route("/api/assets/<int:asset_id>", methods=["PUT"])
@login_required
def update_asset_route(asset_id: int):
    if not _owns_or_admin(asset_id):
        return jsonify({"error": "not allowed"}), 403
    d = request.get_json() or {}
    credential_clear = bool(d.get("clear_credential"))
    result = assets_service.update_asset(
        asset_id,
        current_user_id(),
        name=d.get("name"),
        description=d.get("description"),
        config=d.get("config"),
        metadata=d.get("metadata"),
        credential_plaintext=d.get("credential"),
        credential_clear=credential_clear,
        enabled=d.get("enabled"),
    )
    if result is None:
        return jsonify({"error": "asset not found"}), 404
    return jsonify(result)


@app.route("/api/assets/<int:asset_id>", methods=["DELETE"])
@login_required
def delete_asset_route(asset_id: int):
    if not _owns_or_admin(asset_id):
        return jsonify({"error": "not allowed"}), 403
    ok = assets_service.delete_asset(asset_id, current_user_id())
    if not ok:
        return jsonify({"error": "asset not found"}), 404
    return jsonify({"ok": True})


# --- grants ---

@app.route("/api/assets/<int:asset_id>/grants")
@login_required
def list_asset_grants_route(asset_id: int):
    if not _owns_or_admin(asset_id):
        return jsonify({"error": "not allowed"}), 403
    return jsonify(assets_service.list_grants(asset_id))


@app.route("/api/assets/<int:asset_id>/grants", methods=["POST"])
@login_required
def create_asset_grant_route(asset_id: int):
    if not _asset_feature_allowed("grant_mcp_rag"):
        return jsonify({"error": "granting assets is admin-only"}), 403
    if not _owns_or_admin(asset_id):
        return jsonify({"error": "not allowed"}), 403
    d = request.get_json() or {}
    grantee = d.get("grantee_user_id")
    if not grantee:
        return jsonify({"error": "grantee_user_id required"}), 400
    gid = assets_service.grant(asset_id, int(grantee), current_user_id())
    return jsonify({"id": gid})


@app.route("/api/assets/<int:asset_id>/grants/<int:grantee_id>", methods=["DELETE"])
@login_required
def revoke_asset_grant_route(asset_id: int, grantee_id: int):
    if not _owns_or_admin(asset_id):
        return jsonify({"error": "not allowed"}), 403
    ok = assets_service.revoke(asset_id, grantee_id, current_user_id())
    return jsonify({"ok": ok})


# --- agent assignment ---

@app.route("/api/assets/<int:asset_id>/agents")
@login_required
def list_asset_agents_route(asset_id: int):
    if not _owns_or_admin(asset_id) and not assets_service.visible_to_user(asset_id, current_user_id()):
        return jsonify({"error": "not allowed"}), 403
    return jsonify(assets_service.list_agent_assignments(asset_id))


@app.route("/api/agents/<int:agent_id>/assets")
@login_required
def list_agent_assets_route(agent_id: int):
    kind = request.args.get("kind")
    return jsonify(assets_service.list_assets_for_agent(agent_id, kind=kind))


@app.route("/api/agents/<int:agent_id>/assets", methods=["POST"])
@login_required
def assign_asset_to_agent_route(agent_id: int):
    d = request.get_json() or {}
    asset_id = d.get("asset_id")
    if not asset_id:
        return jsonify({"error": "asset_id required"}), 400
    # Caller must be able to see the asset and own the agent
    if not _is_admin() and not assets_service.visible_to_user(int(asset_id), current_user_id()):
        return jsonify({"error": "asset not visible"}), 403
    agent_row = db.fetch_one("SELECT user_id FROM agents WHERE id = %s", (agent_id,))
    if not agent_row:
        return jsonify({"error": "agent not found"}), 404
    if not _is_admin() and agent_row["user_id"] != current_user_id():
        return jsonify({"error": "not the agent's owner"}), 403
    aid = assets_service.assign_to_agent(int(asset_id), agent_id, current_user_id())
    return jsonify({"id": aid})


@app.route("/api/agents/<int:agent_id>/assets/<int:asset_id>", methods=["DELETE"])
@login_required
def unassign_asset_from_agent_route(agent_id: int, asset_id: int):
    agent_row = db.fetch_one("SELECT user_id FROM agents WHERE id = %s", (agent_id,))
    if not agent_row:
        return jsonify({"error": "agent not found"}), 404
    if not _is_admin() and agent_row["user_id"] != current_user_id():
        return jsonify({"error": "not the agent's owner"}), 403
    ok = assets_service.unassign_from_agent(asset_id, agent_id, current_user_id())
    return jsonify({"ok": ok})


# --- audit + usage ---

@app.route("/api/assets/<int:asset_id>/audit")
@login_required
def asset_audit_route(asset_id: int):
    if not _owns_or_admin(asset_id):
        return jsonify({"error": "not allowed"}), 403
    return jsonify(assets_service.list_audit(asset_id, limit=100))


@app.route("/api/assets/<int:asset_id>/usage")
@login_required
def asset_usage_route(asset_id: int):
    if not _owns_or_admin(asset_id) and not assets_service.visible_to_user(asset_id, current_user_id()):
        return jsonify({"error": "not allowed"}), 403
    hours = int(request.args.get("hours", 24))
    return jsonify({
        "summary": assets_service.usage_summary(asset_id),
        "timeseries": assets_service.usage_timeseries(asset_id, hours=hours),
    })


# --- RAG ingest + search ---

@app.route("/api/assets/<int:asset_id>/rag/ingest", methods=["POST"])
@login_required
def rag_ingest_route(asset_id: int):
    asset = assets_service.get_asset(asset_id)
    if not asset:
        return jsonify({"error": "asset not found"}), 404
    if asset["kind"] != "rag":
        return jsonify({"error": "asset is not a RAG source"}), 400
    if not _owns_or_admin(asset_id):
        return jsonify({"error": "not allowed"}), 403
    d = request.get_json() or {}
    text = d.get("text") or ""
    source_name = d.get("source_name") or "untitled"
    metadata = d.get("metadata") or {}
    try:
        n = rag_service.ingest_text(asset, source_name, text, metadata=metadata)
    except (ValueError, NotImplementedError) as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"chunks_ingested": n})


@app.route("/api/assets/<int:asset_id>/rag/search", methods=["POST"])
@login_required
def rag_search_route(asset_id: int):
    asset = assets_service.get_asset(asset_id)
    if not asset:
        return jsonify({"error": "asset not found"}), 404
    if asset["kind"] != "rag":
        return jsonify({"error": "asset is not a RAG source"}), 400
    if not _is_admin() and not assets_service.visible_to_user(asset_id, current_user_id()):
        return jsonify({"error": "not allowed"}), 403
    d = request.get_json() or {}
    query = d.get("query") or ""
    top_k = int(d.get("top_k", 5))
    try:
        hits = rag_service.search(asset, query, top_k=top_k)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"hits": hits})


@app.route("/api/agents/<int:aid>/shares", methods=["POST"])
@login_required
def create_share_route(aid):
    d = request.get_json() or {}
    borrower_username = (d.get("borrower_username") or "").strip()
    if not borrower_username:
        return jsonify({"error": "borrower_username required"}), 400
    borrower = db.fetch_one(
        "SELECT id FROM as_users WHERE username = %s",
        (borrower_username,),
    )
    if not borrower:
        return jsonify({"error": "user not found"}), 404
    try:
        sid = sharing.share_agent(
            current_user_id(), aid, borrower["id"],
            scope=d.get("scope") or "invoke",
        )
    except PermissionError:
        return jsonify({"error": "not the owner"}), 403
    return jsonify({"id": sid})


@app.route("/api/shares/<int:sid>", methods=["DELETE"])
@login_required
def revoke_share_route(sid):
    sharing.revoke_share(current_user_id(), sid)
    return jsonify({"ok": True})


@app.route("/api/agents/<int:aid>/export")
@login_required
def export_agent_route(aid):
    return jsonify(sharing.export_agent_profile(aid))


@app.route("/api/agents/import", methods=["POST"])
@login_required
def import_agent_route():
    bundle = request.get_json() or {}
    new_id = sharing.import_agent_profile(current_user_id(), bundle)
    worker.registry().start_agent(new_id)
    return jsonify({"id": new_id})


# ============================================================================
# Escalation
# ============================================================================

@app.route("/api/escalations")
@login_required
def list_escalations_route():
    return jsonify(db.fetch_all(
        """
        SELECT e.*, a.name AS agent_name
        FROM agent_escalations e
        LEFT JOIN agents a ON a.id = e.raising_agent_id
        WHERE e.task_owner_id = %s
        ORDER BY e.created_at DESC LIMIT 50
        """,
        (current_user_id(),),
    ))


@app.route("/api/escalations/<int:eid>/resolve", methods=["POST"])
@login_required
def resolve_escalation_route(eid):
    resolution = (request.get_json() or {}).get("resolution", "")
    escalation.resolve(eid, resolution)
    return jsonify({"ok": True})


# ============================================================================
# Dashboard
# ============================================================================

@app.route("/api/dashboard/summary")
@login_required
def dashboard_summary():
    uid = current_user_id()
    row = db.fetch_one(
        """
        SELECT
            (SELECT COUNT(*) FROM agents WHERE user_id = %s AND status = 'active') AS active_agents,
            (SELECT COUNT(*) FROM agent_tasks t JOIN agents a ON a.id = t.agent_id
             WHERE a.user_id = %s AND t.status IN ('queued','paused')) AS total_queue,
            (SELECT COALESCE(SUM(total_cost_usd), 0)::float FROM runs
             WHERE user_id = %s AND started_at >= NOW() - INTERVAL '1 day') AS today_cost,
            (SELECT COUNT(*) FROM runs
             WHERE user_id = %s AND started_at >= NOW() - INTERVAL '1 day') AS today_runs
        """,
        (uid, uid, uid, uid),
    )
    return jsonify({
        "active_agents": int(row["active_agents"] or 0),
        "total_queue_depth": int(row["total_queue"] or 0),
        "today_cost_usd": float(row["today_cost"] or 0),
        "today_runs": int(row["today_runs"] or 0),
    })


@app.route("/api/dashboard/gantt")
@login_required
def dashboard_gantt():
    """Return recent tasks for every agent within a time window.

    Accepts either:
      - `hours=<N>` (legacy, window ending at NOW()), default 6
      - `start_ts` / `end_ts` (ISO 8601 or unix ms) for explicit ranges,
        used by the Dashboard timeline pan/zoom buttons.

    Returns `{ start_ts, end_ts, window_hours, agents: [{..., tasks: [...]}] }`.
    """
    import datetime as _dt

    def _parse_ts(s: str | None) -> _dt.datetime | None:
        if not s:
            return None
        s = s.strip()
        if not s:
            return None
        # Unix ms
        if s.isdigit():
            try:
                return _dt.datetime.fromtimestamp(int(s) / 1000, tz=_dt.timezone.utc)
            except (ValueError, OverflowError):
                return None
        # ISO 8601
        try:
            # Python 3.9 fromisoformat doesn't parse trailing 'Z'
            s2 = s.replace("Z", "+00:00")
            return _dt.datetime.fromisoformat(s2)
        except ValueError:
            return None

    end_ts = _parse_ts(request.args.get("end_ts"))
    start_ts = _parse_ts(request.args.get("start_ts"))
    hours_param = request.args.get("hours")

    if end_ts is None:
        end_ts = _dt.datetime.now(tz=_dt.timezone.utc)
    if start_ts is None:
        try:
            hours = float(hours_param or "6")
        except (TypeError, ValueError):
            hours = 6.0
        hours = max(0.25, min(hours, 24 * 14))  # 15 min .. 14 days
        start_ts = end_ts - _dt.timedelta(hours=hours)
    # Clamp to at least 15-minute window
    if (end_ts - start_ts).total_seconds() < 15 * 60:
        end_ts = start_ts + _dt.timedelta(minutes=15)
    window_hours = round((end_ts - start_ts).total_seconds() / 3600, 3)

    uid = current_user_id()
    agents = db.fetch_all(
        """
        SELECT id, name, role_title, status
        FROM agents WHERE user_id = %s ORDER BY is_lead DESC, id
        """,
        (uid,),
    )
    result = []
    for a in agents:
        tasks = db.fetch_all(
            """
            SELECT t.id, t.status, t.created_at, t.started_at, t.finished_at,
                   t.priority, t.run_id,
                   COALESCE(s.role_label, t.payload->>'label') AS label
            FROM agent_tasks t
            LEFT JOIN run_steps s ON s.id = t.step_id
            WHERE t.agent_id = %s
              AND t.created_at >= %s
              AND t.created_at <= %s
            ORDER BY t.created_at
            """,
            (a["id"], start_ts, end_ts),
        )
        result.append({**a, "tasks": tasks})
    return jsonify({
        "window_hours": window_hours,
        "start_ts": start_ts.isoformat(),
        "end_ts": end_ts.isoformat(),
        "agents": result,
    })


@app.route("/api/dashboard/load_heatmap")
@login_required
def dashboard_load_heatmap():
    """Return the last 24 hourly buckets of task-busyness per agent, as a
    simple intensity grid for the dashboard's condensed loading widget.

    Shape::

        {
            "buckets": 24,
            "bucket_hours": 1,
            "agents": [
                {
                    "id": 1, "name": "小明", "role_title": "...", "is_lead": false,
                    "avatar_config": {...},
                    "values": [0, 1, 3, 0, 2, ..., 0]  // length 24, newest last
                },
                ...
            ]
        }

    A bucket's value is the count of non-terminal + completed agent_tasks
    that were *touched* during that hour (created / started / finished).
    It's deliberately a fuzzy measure — the widget just wants to show
    heat, not exact throughput.
    """
    uid = current_user_id()
    try:
        buckets = int(request.args.get("buckets", "24"))
    except (TypeError, ValueError):
        buckets = 24
    buckets = max(6, min(buckets, 96))

    agents = db.fetch_all(
        """
        SELECT id, name, role_title, status, is_lead, avatar_config
        FROM agents WHERE user_id = %s
        ORDER BY is_lead DESC, id
        """,
        (uid,),
    )
    out = []
    for a in agents:
        rows = db.fetch_all(
            """
            WITH slots AS (
                SELECT generate_series(0, %s - 1) AS idx
            )
            SELECT slots.idx,
                   (SELECT COUNT(*)
                      FROM agent_tasks t
                      WHERE t.agent_id = %s
                        AND t.created_at >= NOW() - ((%s - slots.idx) || ' hours')::interval
                        AND t.created_at <  NOW() - ((%s - slots.idx - 1) || ' hours')::interval
                   ) AS n
            FROM slots
            ORDER BY slots.idx
            """,
            (buckets, a["id"], buckets, buckets),
        )
        values = [int(r["n"]) for r in rows]
        out.append({**a, "values": values})
    return jsonify({
        "buckets": buckets,
        "bucket_hours": 1,
        "agents": out,
    })


@app.route("/api/dashboard/agent_load")
@login_required
def dashboard_agent_load():
    uid = current_user_id()
    rows = db.fetch_all(
        """
        SELECT a.id, a.name, a.role_title, a.status, a.max_queue_depth,
            a.is_lead, a.avatar_config,
            (SELECT COUNT(*) FROM agent_tasks t WHERE t.agent_id = a.id AND t.status IN ('queued','paused')) AS queue_depth,
            (SELECT COALESCE(SUM(s.cost_usd), 0)::float
             FROM run_steps s WHERE s.agent_id = a.id
               AND s.started_at >= NOW() - INTERVAL '1 day') AS today_cost
        FROM agents a
        WHERE a.user_id = %s
        ORDER BY a.is_lead DESC, a.id
        """,
        (uid,),
    )
    # Ensure numeric fields are plain floats for JSON
    for r in rows:
        r["today_cost"] = float(r.get("today_cost") or 0)
        r["queue_depth"] = int(r.get("queue_depth") or 0)
    return jsonify(rows)


# ============================================================================
# Avatar composer (public — used by <img> tags, no auth required)
# ============================================================================

from flask import Response as FlaskResponse


@app.route("/api/avatar/parts")
def avatar_parts():
    return jsonify(avatar.all_parts())


@app.route("/api/avatar/compose")
def avatar_compose():
    def _int_or_none(v):
        try:
            return int(v) if v else None
        except (TypeError, ValueError):
            return None

    cfg = {
        "body_type": request.args.get("body_type", "body_bust"),
        "body": request.args.get("body", "Shirt"),
        "hair": request.args.get("hair", "Medium"),
        "face": request.args.get("face", "Calm"),
        "facial_hair": request.args.get("facial_hair") or None,
        "accessory": request.args.get("accessory") or None,
        "bg": request.args.get("bg") or None,
        "vb": request.args.get("vb") or None,
        "w": _int_or_none(request.args.get("w")),
        "h": _int_or_none(request.args.get("h")),
    }
    if cfg["vb"]:
        cfg["vb"] = cfg["vb"].replace(",", " ")

    key = "compose|" + "|".join(str(v) for v in cfg.values())
    svg = avatar.compose_cached(key, lambda: avatar.compose_from_config(cfg))
    resp = FlaskResponse(svg, mimetype="image/svg+xml")
    resp.headers["Cache-Control"] = "public, max-age=3600"
    return resp


@app.route("/api/avatar/thumb/<category>/<name>")
def avatar_thumb(category: str, name: str):
    key = f"thumb|{category}|{name}"
    svg = avatar.compose_cached(key, lambda: avatar.compose_thumb(category, name))
    resp = FlaskResponse(svg, mimetype="image/svg+xml")
    resp.headers["Cache-Control"] = "public, max-age=86400"
    return resp


# ============================================================================
# Model clients (Phase 7)
# ============================================================================

@app.route("/api/model_clients/kinds")
@login_required
def model_clients_kinds():
    """Kind metadata for the Settings UI — what fields each kind expects."""
    from .services.model_clients import KIND_SCHEMAS
    return jsonify([
        {"kind": k, **v} for k, v in KIND_SCHEMAS.items()
    ])


@app.route("/api/model_clients")
@login_required
def model_clients_list():
    """Admin sees full list with grant counts; user sees only clients they
    can use (defaults + explicit grants)."""
    from .services import model_clients as mc
    user_row = db.fetch_one("SELECT id, role FROM as_users WHERE id = %s", (session["user_id"],))
    if user_row and user_row["role"] == "admin":
        return jsonify(mc.list_for_admin())
    return jsonify(mc.list_for_user(session["user_id"]))


@app.route("/api/model_clients/<int:cid>")
@login_required
def model_clients_get(cid: int):
    from .services import model_clients as mc
    row = mc.get(cid)
    if not row:
        return jsonify({"error": "not found"}), 404
    return jsonify(row)


@app.route("/api/model_clients", methods=["POST"])
@admin_required
def model_clients_create():
    from .services import model_clients as mc
    data = request.get_json() or {}
    try:
        cid = mc.create(
            name=data.get("name", ""),
            kind=data.get("kind", ""),
            description=data.get("description"),
            config=data.get("config") or {},
            credential=data.get("credential") or None,
            enabled=bool(data.get("enabled", True)),
            default_for_new_users=bool(data.get("default_for_new_users", False)),
            created_by=session["user_id"],
        )
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"id": cid})


@app.route("/api/model_clients/<int:cid>", methods=["PUT"])
@admin_required
def model_clients_update(cid: int):
    from .services import model_clients as mc
    data = request.get_json() or {}
    mc.update(
        cid,
        name=data.get("name"),
        description=data.get("description"),
        config=data.get("config"),
        credential=data.get("credential") or None,
        clear_credential=bool(data.get("clear_credential")),
        enabled=data.get("enabled"),
        default_for_new_users=data.get("default_for_new_users"),
    )
    return jsonify(mc.get(cid))


@app.route("/api/model_clients/<int:cid>", methods=["DELETE"])
@admin_required
def model_clients_delete(cid: int):
    from .services import model_clients as mc
    mc.remove(cid)
    return jsonify({"ok": True})


@app.route("/api/model_clients/<int:cid>/grants")
@admin_required
def model_clients_list_grants(cid: int):
    from .services import model_clients as mc
    return jsonify(mc.list_grants(cid))


@app.route("/api/model_clients/<int:cid>/grants", methods=["POST"])
@admin_required
def model_clients_grant(cid: int):
    from .services import model_clients as mc
    data = request.get_json() or {}
    uid = data.get("user_id")
    if not uid:
        return jsonify({"error": "user_id required"}), 400
    mc.grant(cid, int(uid), granted_by=session["user_id"])
    return jsonify({"ok": True})


@app.route("/api/model_clients/<int:cid>/grants/<int:uid>", methods=["DELETE"])
@admin_required
def model_clients_revoke(cid: int, uid: int):
    from .services import model_clients as mc
    mc.revoke(cid, uid)
    return jsonify({"ok": True})


# ============================================================================
# Main
# ============================================================================

if __name__ == "__main__":
    _startup()
    app.run(host="0.0.0.0", port=PORT, debug=False)
