"""Standalone entry point for personal/all-in-one mode.

Starts a lightweight Flask server with SQLite backend on an auto-selected
port. Used by the Tauri desktop app's sidecar when running in personal mode.

First run:
  - Creates admin user with default credentials admin / admin
  - Creates 3 starter agents: Ava (lead secretary), Noah (writer), Riley (reviewer)
  - Seeds model clients, skills, tools

Usage:
    python -m backend.standalone              # auto port
    python -m backend.standalone --port 9123  # fixed port

Environment:
    DB_BACKEND=sqlite  (set automatically by this script)
    SQLITE_PATH=...    (optional, defaults to ~/.agent_company/data.db)
"""
from __future__ import annotations

import argparse
import json
import os
import random
import socket
import uuid


_AVATAR_BODIES = [
    "ArmsCrossed", "BlazerBlackTee", "ButtonShirt", "Coffee", "Device",
    "DotJacket", "Dress", "Explaining", "FurJacket", "Gaming", "Geek",
    "Hoodie", "Paper", "PocketShirt", "PointingUp", "PoloSweater",
    "Shirt", "ShirtCoat", "ShirtFilled", "SportyShirt", "StripedShirt",
    "Sweater", "SweaterDots", "Thunder", "Turtleneck", "Whatever",
]
_AVATAR_HAIRS = [
    "Afro", "Bald", "BaldSides", "BaldTop", "Bangs", "BangsFilled",
    "BantuKnots", "Beanie", "Bun", "BunCurly", "BunFancy", "Buns",
    "CornRows", "CornRowsFilled", "FlatTop", "FlatTopLong", "HatHip",
    "Long", "LongAfro", "LongBangs", "LongCurly", "Medium", "MediumBangs",
    "MediumBangsFilled", "MediumLong", "MediumShort", "MediumStraight",
    "Mohawk", "Pomp", "Short", "ShortCurly", "ShortMessy", "ShortVolumed",
    "ShortWavy", "Turban", "Twists", "TwistsVolumed",
]
_AVATAR_FACES = [
    "Awe", "Blank", "Calm", "CalmNM", "Cheeky", "CheersNM", "Cute",
    "Driven", "EatingHappy", "Explaining", "LoveGrin", "LoveGrinTeeth",
    "Serious", "Smile", "SmileBig", "SmileLol", "SmileNM", "SmileTeeth",
    "Solemn",
]


def _random_avatar() -> dict:
    return {
        "body": random.choice(_AVATAR_BODIES),
        "hair": random.choice(_AVATAR_HAIRS),
        "face": random.choice(_AVATAR_FACES),
    }


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _first_run_setup():
    """Called once when the DB has no users. Creates the default admin + agents."""
    try:
        from . import db, worker
    except ImportError:
        from backend import db, worker
    from werkzeug.security import generate_password_hash

    user_id = db.execute_returning(
        "INSERT INTO as_users (username, password_hash, display_name, role) "
        "VALUES (%s, %s, %s, %s) RETURNING id",
        ("admin", generate_password_hash("admin", method="pbkdf2:sha256"),
         "Admin", "admin"),
    )

    print("FIRST_RUN=true login with admin / admin", flush=True)

    # Default team — names are personal names; role stays in role_title.
    # Agents are presented name-first throughout the UI.

    # --- Lead agent: Ava (secretary) ---
    lead_id = db.execute_returning(
        """INSERT INTO agents
           (user_id, owner_user_id, name, role_title, description,
            system_prompt, is_lead, avatar_config, status)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
        (user_id, user_id, "Ava", "Secretary",
         "Coordinates your agent team. Answers simple questions directly or proposes a workflow for complex tasks.",
         "You are Ava, the user's personal secretary. Coordinate the agent team, answer quick questions "
         "yourself, and propose workflow designs for more complex tasks. Be warm, clear, and concise.",
         True,
         json.dumps(_random_avatar()),
         "active"),
    )
    db.execute("UPDATE as_users SET default_lead_agent_id = %s WHERE id = %s",
               (lead_id, user_id))

    # --- Example agent 1: Noah (content writer) ---
    db.execute_returning(
        """INSERT INTO agents
           (user_id, owner_user_id, name, role_title, description,
            system_prompt, avatar_config, status)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
        (user_id, user_id, "Noah", "Content Writer",
         "Writes drafts, outlines, articles, and creative content.",
         "You are Noah, a skilled content writer. Given a topic or brief, produce well-structured, "
         "engaging written content. Adapt your tone to the audience. Use clear paragraphs, "
         "headings where appropriate, and a compelling narrative flow.",
         json.dumps(_random_avatar()),
         "active"),
    )

    # --- Example agent 2: Riley (quality reviewer) ---
    db.execute_returning(
        """INSERT INTO agents
           (user_id, owner_user_id, name, role_title, description,
            system_prompt, avatar_config, status)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
        (user_id, user_id, "Riley", "Quality Reviewer",
         "Reviews content for clarity, accuracy, and quality. Provides structured feedback.",
         "You are Riley, a meticulous quality reviewer. Evaluate given content on: "
         "(1) clarity and readability, (2) factual accuracy, (3) structure and flow, "
         "(4) tone appropriateness. Provide 3–5 specific, actionable suggestions.",
         json.dumps(_random_avatar()),
         "active"),
    )

    # Seed a welcome thread from the Lead so the user sees an unread message
    # in the Dialog Center the first time they log in. Uses absolute-from-root
    # links so they work whether the user is on /dialog in the web console or
    # on the desktop cast bar (Lead chat panel opens the same thread).
    thread_id = uuid.uuid4().hex[:16]
    db.execute(
        """INSERT INTO lead_conversations (user_id, thread_id, title, status)
           VALUES (%s, %s, %s, 'active')""",
        (user_id, thread_id, "Welcome to Holons"),
    )
    welcome = (
        "Hi, I'm **Ava** — your Lead agent. I coordinate the rest of your team, "
        "answer quick questions, and can design multi-step workflows when a task "
        "needs more than one agent.\n\n"
        "A few things to set up before we get started:\n\n"
        "1. **Connect a model provider first.** This app does **not** ship with "
        "any AWS / Anthropic / OpenAI credentials baked in. Open "
        "[Settings → Model connections](/settings) and fill in your own keys. "
        "Until a connection is configured, I can't actually run any LLM calls.\n"
        "2. **Meet your team** on the [Employees page](/agents) — you'll see "
        "me plus two starter teammates (Noah, a content writer; Riley, a quality "
        "reviewer). You can rename, reconfigure, or remove any of us, and add "
        "more specialised agents.\n"
        "3. **Watch your usage** on the [Dashboard](/dashboard) — token spend, "
        "cost, and per-agent activity all roll up there. Set daily / monthly "
        "quotas from [Settings](/settings) once you know your budget.\n\n"
        "The full web console has more than what the desktop cast bar shows — "
        "click any link above (or open the tray menu → *Open web settings*) to "
        "jump over. Ping me here whenever you're ready."
    )
    db.execute(
        """INSERT INTO lead_messages (thread_id, role, content, metadata)
           VALUES (%s, 'lead', %s, %s)""",
        (thread_id, welcome, json.dumps({"welcome": True})),
    )

    # Start workers for the new agents
    worker.registry().start_all_active()

    print("Created admin user (admin/admin) + Ava (lead) + Noah + Riley", flush=True)


def main():
    parser = argparse.ArgumentParser(description="Agent Company standalone server")
    parser.add_argument("--port", type=int, default=0, help="Port (0 = auto)")
    parser.add_argument("--db", type=str, default=None, help="SQLite DB path")
    args = parser.parse_args()

    # Force SQLite backend
    os.environ["DB_BACKEND"] = "sqlite"
    if args.db:
        os.environ["DATABASE_URL"] = args.db

    port = args.port or find_free_port()
    os.environ["PORT"] = str(port)

    # Print port first so Tauri can read it immediately
    print(f"PORT={port}", flush=True)

    # Import and start app — use try/except for PyInstaller compatibility
    try:
        from .app import app, _startup
        from . import db
    except ImportError:
        from backend.app import app, _startup
        from backend import db
    _startup()

    # First-run setup if DB is empty
    existing = db.fetch_one("SELECT id FROM as_users LIMIT 1")
    if not existing:
        _first_run_setup()

    print(f"Standalone server ready on http://localhost:{port}", flush=True)
    app.run(host="127.0.0.1", port=port, debug=False)


if __name__ == "__main__":
    main()
