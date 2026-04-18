"""Default asset library seeds — popular public MCP servers + built-in tools.

Called from schema.create_all() on every startup. Each entry is upserted
idempotently using a `metadata.seed_key` marker so repeat startups don't
create duplicates. Admins can edit/disable/delete seeded rows freely — the
marker is only used for insert-or-skip detection.

Philosophy: we ship metadata only (name / description / URL placeholder /
docs link). No credentials are stored — operators fill them in via the
Library UI. Seeded rows are owned by the first admin user in the system.
"""
from __future__ import annotations

import json
import logging

from .. import db

log = logging.getLogger("agent_company.asset_seeds")


# ============================================================================
# Seed definitions. Adding an entry here + restarting backend = new row in
# the library (unless the seed_key already exists). Existing seeded rows
# are left alone so admin edits aren't clobbered.
# ============================================================================

SEEDS: list[dict] = [
    # ---------- example skills (reusable prompts / playbooks) ----------
    # These give the Skill tab something to look at on a fresh install
    # and act as starting templates. Admins can edit/rename/delete freely.
    {
        "seed_key": "skill:brand_voice",
        "kind": "skill",
        "name": "Brand Voice Guide",
        "description": "Voice guide to keep writing on-brand — tone, person, banned phrases.",
        "config": {
            "content_md": (
                "# Brand Voice\n\n"
                "## Principles\n"
                "- Tone: confident, warm, never hyped.\n"
                "- Person: address the reader as \"you\"; refer to the company as \"we\".\n"
                "- Cadence: short sentences; avoid stacking modifiers.\n\n"
                "## Banned phrases\n"
                "- Absolutes: \"the only\", \"the best\", \"industry-leading\".\n"
                "- Marketing cliches: \"revolutionary\", \"disruptive\", \"game-changing\".\n\n"
                "## Examples\n"
                "❌ We are the industry-leading AI solution provider.\n"
                "✅ We help teams hand repetitive work off to AI.\n"
            ),
        },
        "metadata": {"category": "writing"},
    },
    {
        "seed_key": "skill:qa_checklist",
        "kind": "skill",
        "name": "Quality Checklist",
        "description": "Self-review checklist for agents to run before delivering output.",
        "config": {
            "content_md": (
                "# Quality Checklist\n\n"
                "Before delivering any output, confirm:\n\n"
                "- [ ] Goal: does the output actually answer the original task?\n"
                "- [ ] Completeness: have all sub-tasks been addressed?\n"
                "- [ ] Accuracy: are facts, numbers, and citations verifiable?\n"
                "- [ ] Consistency: style, terminology, and formatting uniform?\n"
                "- [ ] Readability: paragraphs, headings, lists easy to scan?\n"
                "- [ ] Conciseness: any padding or repetition?\n"
                "- [ ] Honesty: are uncertain or unverified claims flagged?\n"
            ),
        },
        "metadata": {"category": "review"},
    },
    {
        "seed_key": "skill:meeting_summary",
        "kind": "skill",
        "name": "Meeting Summary Template",
        "description": "Standard meeting-notes format covering decisions, actions, and open questions.",
        "config": {
            "content_md": (
                "# Meeting Summary Template\n\n"
                "## Meta\n"
                "- Date / time:\n"
                "- Topic:\n"
                "- Attendees:\n\n"
                "## Key points\n"
                "(three to five, one sentence each)\n\n"
                "## Decisions\n"
                "(each with an owner)\n\n"
                "## Action items\n"
                "| # | Item | Owner | Due |\n"
                "|---|------|-------|-----|\n\n"
                "## Open questions\n"
                "(things that need follow-up)\n"
            ),
        },
        "metadata": {"category": "template"},
    },
    {
        "seed_key": "skill:bug_report",
        "kind": "skill",
        "name": "Bug Report Template",
        "description": "Structured bug-report format — repro, expected, actual, environment.",
        "config": {
            "content_md": (
                "# Bug Report\n\n"
                "## Summary\n"
                "(one-sentence description)\n\n"
                "## Steps to reproduce\n"
                "1. \n2. \n3. \n\n"
                "## Expected behavior\n\n"
                "## Actual behavior\n\n"
                "## Environment\n"
                "- Browser / OS:\n"
                "- Version / commit:\n"
                "- User account:\n\n"
                "## Extras\n"
                "(screenshots, logs, related issues)\n"
            ),
        },
        "metadata": {"category": "template"},
    },
    {
        "seed_key": "skill:code_review",
        "kind": "skill",
        "name": "Code Review Guide",
        "description": "What to focus on when reviewing code — correctness, readability, performance, tests.",
        "config": {
            "content_md": (
                "# Code Review Guide\n\n"
                "## Priorities\n"
                "1. **Correctness** — any bugs? are edge cases handled?\n"
                "2. **Safety** — injection, auth, or leaking sensitive data?\n"
                "3. **Readability** — are names clear? is the flow intuitive?\n"
                "4. **Tests** — are there tests? do they cover the path?\n"
                "5. **Performance** — obvious N+1s or unnecessary loops?\n\n"
                "## Feedback tone\n"
                "- Prefer \"I'd consider...\" over \"you should...\".\n"
                "- Distinguish must-fix / nice-to-have / pure question.\n"
                "- Call out good design decisions explicitly.\n"
            ),
        },
        "metadata": {"category": "engineering"},
    },
    {
        "seed_key": "skill:user_interview",
        "kind": "skill",
        "name": "User Interview Script",
        "description": "Open-ended interview template for product research — avoids leading questions.",
        "config": {
            "content_md": (
                "# User Interview Script\n\n"
                "## Warm-up\n"
                "- Can you walk me through what you do?\n"
                "- What does a typical day look like?\n\n"
                "## Problem discovery\n"
                "- When did you last run into X?\n"
                "- How did you handle it?\n"
                "- Did you try other approaches? Why did you drop them?\n\n"
                "## Current solution\n"
                "- What tools do you use for this today?\n"
                "- If that tool disappeared tomorrow, what would you do?\n\n"
                "## Wrap-up\n"
                "- Anything I didn't ask about that you'd like to share?\n"
                "- Okay if I follow up with more questions later?\n\n"
                "## Principles\n"
                "- Don't ask hypotheticals (\"would you use X?\").\n"
                "- Ask about the past, not the future.\n"
                "- Ask \"why\" at least three times.\n"
            ),
        },
        "metadata": {"category": "research"},
    },

    # ---------- built-in tools (our own Python functions) ----------
    {
        "seed_key": "tool:current_time",
        "kind": "tool",
        "name": "Current Time",
        "description": "Read the current time (optional timezone). No user configuration needed.",
        "config": {
            "module": "backend.tools.current_time",
            "fn": "handler",
        },
        "metadata": {
            "docs_url": "",
            "category": "builtin",
        },
    },
    {
        "seed_key": "tool:http_get",
        "kind": "tool",
        "name": "HTTP GET",
        "description": "Whitelisted HTTP GET (wikipedia, github, anthropic, example, httpbin).",
        "config": {
            "module": "backend.tools.http_get",
            "fn": "handler",
        },
        "metadata": {
            "docs_url": "",
            "category": "builtin",
        },
    },
    {
        "seed_key": "tool:search_skills",
        "kind": "tool",
        "name": "Search Skills",
        "description": "ILIKE search across the agent's own skill library.",
        "config": {
            "module": "backend.tools.search_skills",
            "fn": "handler",
        },
        "metadata": {
            "docs_url": "",
            "category": "builtin",
        },
    },

    # ---------- popular public MCP servers ----------
    # URLs are placeholders — the operator fills in the real endpoint +
    # credential via the Library UI after seed.
    {
        "seed_key": "mcp:google_drive",
        "kind": "mcp",
        "name": "Google Drive",
        "description": "List, read, and search Google Drive files. Requires an OAuth token.",
        "config": {
            "url": "",
            "default_disabled": True,  # admin must enable after filling creds
        },
        "metadata": {
            "docs_url": "https://github.com/modelcontextprotocol/servers/tree/main/src/gdrive",
            "category": "google",
            "icon": "📁",
        },
        "enabled": False,
    },
    {
        "seed_key": "mcp:google_docs",
        "kind": "mcp",
        "name": "Google Docs",
        "description": "Read, create, and edit Google Docs. Shares Google auth with Drive.",
        "config": {"url": ""},
        "metadata": {
            "docs_url": "https://github.com/modelcontextprotocol/servers/tree/main/src/gdocs",
            "category": "google",
            "icon": "📄",
        },
        "enabled": False,
    },
    {
        "seed_key": "mcp:google_slides",
        "kind": "mcp",
        "name": "Google Slides",
        "description": "Read, create, and edit slide decks. Shares Google auth with Drive.",
        "config": {"url": ""},
        "metadata": {
            "docs_url": "https://developers.google.com/slides",
            "category": "google",
            "icon": "📊",
        },
        "enabled": False,
    },
    {
        "seed_key": "mcp:google_calendar",
        "kind": "mcp",
        "name": "Google Calendar",
        "description": "Query, create, and edit calendar events.",
        "config": {"url": ""},
        "metadata": {
            "docs_url": "https://developers.google.com/calendar",
            "category": "google",
            "icon": "📅",
        },
        "enabled": False,
    },
    {
        "seed_key": "mcp:github",
        "kind": "mcp",
        "name": "GitHub",
        "description": "GitHub repo / issue / PR read and write. Requires a personal access token.",
        "config": {"url": ""},
        "metadata": {
            "docs_url": "https://github.com/modelcontextprotocol/servers/tree/main/src/github",
            "category": "dev",
            "icon": "🐙",
        },
        "enabled": False,
    },
    {
        "seed_key": "mcp:fetch",
        "kind": "mcp",
        "name": "Fetch (web scraper)",
        "description": "General HTTP fetch + HTML-to-text. Official public MCP server.",
        "config": {"url": ""},
        "metadata": {
            "docs_url": "https://github.com/modelcontextprotocol/servers/tree/main/src/fetch",
            "category": "web",
            "icon": "🌐",
        },
        "enabled": False,
    },
]


def seed_default_assets() -> None:
    """Upsert default asset rows. Idempotent via metadata.seed_key marker.

    Owner is the first admin in the system — if there are no admins yet
    (fresh install before seed_v2), this is a no-op and will run on the
    next startup after an admin exists.
    """
    admin = db.fetch_one(
        "SELECT id FROM as_users WHERE role = 'admin' ORDER BY id LIMIT 1"
    )
    if not admin:
        log.info("asset_seeds: no admin yet, skipping seed")
        return
    admin_id = admin["id"]

    for entry in SEEDS:
        seed_key = entry["seed_key"]
        existing = db.fetch_one(
            "SELECT id FROM asset_items WHERE metadata ->> 'seed_key' = %s",
            (seed_key,),
        )
        if existing:
            continue

        metadata = {"seed_key": seed_key, **entry.get("metadata", {})}
        enabled = entry.get("enabled", True)
        db.execute(
            """
            INSERT INTO asset_items
              (kind, name, description, owner_user_id, enabled,
               config, metadata)
            VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s::jsonb)
            """,
            (
                entry["kind"],
                entry["name"],
                entry["description"],
                admin_id,
                enabled,
                json.dumps(entry.get("config", {})),
                json.dumps(metadata),
            ),
        )
        log.info("asset_seeds: seeded %s (%s)", entry["name"], seed_key)
