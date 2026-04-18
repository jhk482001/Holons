"""Seed two showcase setups under a demo user `jay` / `demo`.

Usage (from the repo root):
    DB_BACKEND=sqlite python -m demo.seed_demo

Adds to the running database:
  * User  `jay` (password `demo`, role user)
  * Team A — Screenwriting Room
      - Jade (showrunner / lead)
      - Eli  (screenwriter)
      - Mia  (script doctor / editor)
      - Leo  (story structure consultant)
      - Group: "Writers Room" (sequential)
  * Team B — Startup Pitch Council
      - Travis, Brian, Patrick — 3 founder-archetype mentors
      - Mike, Marc, Bill        — 3 VC-archetype reviewers
      - Group: "Founders Round" (parallel)
      - Group: "VC Review"      (parallel)
      - Workflow: "Pitch Deck — 3 rounds"

Idempotent: if `jay` already exists, the script exits without touching anything.

Requires the main schema to be present — run `backend.standalone` once first
so tables exist, then run this script.
"""
from __future__ import annotations

import json
import os
import sys

# Ensure project root is on sys.path when running as a script.
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from backend import db  # noqa: E402
from werkzeug.security import generate_password_hash  # noqa: E402


# ---------------------------------------------------------------------------
# Screenwriting Room
# ---------------------------------------------------------------------------

SCREENWRITING_AGENTS = [
    {
        "name": "Jade",
        "role_title": "Showrunner",
        "is_lead": True,
        "avatar": {"body": "BlazerBlackTee", "hair": "LongBangs", "face": "Driven"},
        "description": (
            "Leads the writers' room. Breaks a concept into acts, assigns beats, "
            "and keeps the creative direction coherent."
        ),
        "system_prompt": (
            "You are Jade, the showrunner. You run this writers' room. Given a premise, "
            "break it into a three-act structure, decide what each member should tackle, "
            "and keep the tone unified. Speak like a thoughtful head writer — decisive, "
            "warm, and clear about what a scene needs to accomplish."
        ),
    },
    {
        "name": "Eli",
        "role_title": "Screenwriter",
        "is_lead": False,
        "avatar": {"body": "Coffee", "hair": "ShortMessy", "face": "Explaining"},
        "description": "Drafts scenes and dialogue with a voice-driven, character-first style.",
        "system_prompt": (
            "You are Eli, a screenwriter. Given a scene brief, draft it in screenplay "
            "format (INT./EXT., action lines, dialogue). Keep action lines active and "
            "vivid. Let characters speak with distinct voices — don't make them "
            "interchangeable. Usually draft 1–2 scenes at a time unless asked for more."
        ),
    },
    {
        "name": "Mia",
        "role_title": "Script Doctor",
        "is_lead": False,
        "avatar": {"body": "PoloSweater", "hair": "Medium", "face": "Serious"},
        "description": "Edits drafts for pacing, dialogue clarity, and scene economy.",
        "system_prompt": (
            "You are Mia, a script doctor. Review drafts for pacing, dialogue "
            "clarity, redundant lines, and scene economy. Give surgical notes — line "
            "by line if the scene needs it, holistic when the structure needs work. "
            "Be warm but direct; flag what's working alongside what isn't."
        ),
    },
    {
        "name": "Leo",
        "role_title": "Story Structure Consultant",
        "is_lead": False,
        "avatar": {"body": "ShirtCoat", "hair": "Pomp", "face": "Calm"},
        "description": "Audits plot shape, character arcs, and thematic cohesion.",
        "system_prompt": (
            "You are Leo, a structure consultant. Audit stories for plot shape, "
            "character arc, and thematic cohesion. Think in terms of turning points, "
            "midpoints, and escalation. Point out when a story's stakes are flat or "
            "when a character has no arc."
        ),
    },
]


# ---------------------------------------------------------------------------
# Startup Pitch Council
# ---------------------------------------------------------------------------

PITCH_AGENTS = [
    # --- Founders (round-1 mentors) ---
    {
        "name": "Travis",
        "role_title": "Founder — Marketplace / Operations",
        "is_lead": False,
        "avatar": {"body": "BlazerBlackTee", "hair": "Short", "face": "Driven"},
        "description": "Founder archetype who scaled a hard-logistics two-sided marketplace.",
        "system_prompt": (
            "You are Travis, a founder who scaled a marketplace business from zero to "
            "global. You push pitches to be sharp on unit economics, supply-side "
            "acquisition, and operational density by city/region. Blunt, metrics-first, "
            "impatient with fluff. Always ask: 'what's the take-rate and the CAC?'"
        ),
    },
    {
        "name": "Brian",
        "role_title": "Founder — Consumer / Brand",
        "is_lead": False,
        "avatar": {"body": "PocketShirt", "hair": "MediumStraight", "face": "Smile"},
        "description": "Founder archetype who built a category-defining consumer brand.",
        "system_prompt": (
            "You are Brian, a founder who built a beloved consumer brand. You push "
            "pitches to feel human — what's the emotional job? what does the hero user "
            "say in a sentence? — and to nail trust and design as moats. Thoughtful, "
            "story-led, allergic to buzzwords."
        ),
    },
    {
        "name": "Patrick",
        "role_title": "Founder — Developer Infra",
        "is_lead": False,
        "avatar": {"body": "ButtonShirt", "hair": "Medium", "face": "Serious"},
        "description": "Founder archetype who built a dev-first infrastructure company.",
        "system_prompt": (
            "You are Patrick, a founder who built a developer-first infrastructure "
            "company. You push pitches to be crisp on distribution-to-developers, "
            "time-to-first-value, and the 'why now' of the platform shift. Precise, "
            "writerly, clinical about what the integration surface looks like."
        ),
    },
    # --- VCs (round-2 reviewers) ---
    {
        "name": "Mike",
        "role_title": "Partner — Pragmatist VC",
        "is_lead": False,
        "avatar": {"body": "Sweater", "hair": "GrayShort", "face": "Serious"},
        "description": "Senior partner focused on traction, retention, and execution risk.",
        "system_prompt": (
            "You are Mike, a senior VC known for pragmatism. Grade pitches on traction "
            "evidence, retention cohorts, defensibility over 5 years, and whether this "
            "team can execute through the next 18 months. Terse, skeptical, numbers-first."
        ),
    },
    {
        "name": "Marc",
        "role_title": "Partner — Thesis-driven VC",
        "is_lead": False,
        "avatar": {"body": "BlazerBlackTee", "hair": "Short", "face": "Explaining"},
        "description": "Thesis-driven investor who trades in strong worldviews about tech shifts.",
        "system_prompt": (
            "You are Marc, a thesis-driven investor. Grade pitches on 'why now', the "
            "shape of the platform shift, and whether the founders have an opinionated "
            "take that would offend conventional wisdom. Generous, big-picture, "
            "intolerant of pitches that could have been written 5 years ago."
        ),
    },
    {
        "name": "Bill",
        "role_title": "Partner — Metrics-driven VC",
        "is_lead": False,
        "avatar": {"body": "PoloSweater", "hair": "ShortVolumed", "face": "Calm"},
        "description": "Operator-turned-investor who grades everything on unit economics.",
        "system_prompt": (
            "You are Bill, an operator-turned-investor. Grade pitches on CAC/LTV, payback "
            "period, gross margin, and the rate-of-learning of the team. Zero patience "
            "for unit economics that don't work at scale. Concise, spreadsheet-minded."
        ),
    },
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _insert_user(username: str, password: str, display_name: str) -> int:
    return db.execute_returning(
        "INSERT INTO as_users (username, password_hash, display_name, role) "
        "VALUES (%s, %s, %s, %s) RETURNING id",
        (username, generate_password_hash(password, method="pbkdf2:sha256"),
         display_name, "user"),
    )


def _insert_agent(user_id: int, spec: dict) -> int:
    return db.execute_returning(
        """INSERT INTO agents
           (user_id, owner_user_id, name, role_title, description,
            system_prompt, is_lead, avatar_config, status)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
        (user_id, user_id, spec["name"], spec["role_title"], spec["description"],
         spec["system_prompt"], spec.get("is_lead", False),
         json.dumps(spec["avatar"]), "active"),
    )


def _insert_group(user_id: int, name: str, description: str, mode: str,
                  member_ids: list[int], aggregator_id: int | None = None) -> int:
    gid = db.execute_returning(
        """INSERT INTO groups_tbl
           (user_id, name, description, mode, aggregator_agent_id, is_ephemeral)
           VALUES (%s, %s, %s, %s, %s, FALSE) RETURNING id""",
        (user_id, name, description, mode, aggregator_id),
    )
    for i, aid in enumerate(member_ids):
        db.execute(
            "INSERT INTO group_members (group_id, agent_id, position) "
            "VALUES (%s, %s, %s)",
            (gid, aid, i),
        )
    return gid


def _insert_fake_activity(user_id: int, agent_ids: dict[str, int],
                          workflow_id: int) -> None:
    """Seed synthetic run + agent_task + run_step data so the Dashboard
    shows non-zero cost / queue-depth / recent-activity. Timestamps are
    computed in Python as ISO strings so it works on SQLite and Postgres.
    """
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)

    def _ts(hours: float = 0, minutes: float = 0) -> str:
        return (now - timedelta(hours=hours, minutes=minutes)).strftime(
            "%Y-%m-%d %H:%M:%S"
        )

    # Completed runs today — drives summary.today_cost + summary.today_runs
    for spec in [
        {"cost": 0.42, "hours_ago": 3, "steps": [
            ("Jade", 0.09), ("Eli",  0.11),
            ("Mia",  0.08), ("Leo",  0.14),
        ]},
        {"cost": 0.68, "hours_ago": 6, "steps": [
            ("Travis", 0.14), ("Brian",  0.13), ("Patrick", 0.15),
            ("Mike",   0.11), ("Marc",   0.08), ("Bill",    0.07),
        ]},
        {"cost": 0.31, "hours_ago": 10, "steps": [
            ("Jade", 0.12), ("Eli",  0.10), ("Mia",  0.09),
        ]},
    ]:
        run_id = db.execute_returning(
            """INSERT INTO runs
               (workflow_id, user_id, status, started_at, finished_at,
                total_cost_usd)
               VALUES (%s, %s, 'done', %s, %s, %s) RETURNING id""",
            (workflow_id, user_id,
             _ts(hours=spec["hours_ago"]),
             _ts(hours=spec["hours_ago"] - 0.2),
             spec["cost"]),
        )
        for i, (agent_name, step_cost) in enumerate(spec["steps"]):
            aid = agent_ids.get(agent_name)
            if not aid:
                continue
            db.execute(
                """INSERT INTO run_steps
                   (run_id, agent_id, iteration, role_label,
                    cost_usd, input_tokens, output_tokens, duration_ms,
                    started_at, model_id)
                   VALUES (%s, %s, 0, 'agent',
                           %s, %s, %s, %s, %s, %s)""",
                (run_id, aid,
                 step_cost, 1800, 620, 45000 + i * 2000,
                 _ts(hours=spec["hours_ago"], minutes=-i * 2),
                 "anthropic-claude-3.5"),
            )

    # Queue depth — a few queued/paused tasks so "busy" agents render
    for agent_name, status, minutes_ago in [
        ("Jade",    "queued", 4),
        ("Eli",     "queued", 7),
        ("Mia",     "paused", 15),
        ("Travis",  "queued", 2),
        ("Marc",    "queued", 9),
    ]:
        aid = agent_ids.get(agent_name)
        if not aid:
            continue
        db.execute(
            """INSERT INTO agent_tasks
               (agent_id, status, created_at, priority_num, payload)
               VALUES (%s, %s, %s, 5, %s::jsonb)""",
            (aid, status, _ts(minutes=minutes_ago), json.dumps({
                "label": f"Demo task seeded for {agent_name}",
                "demo": True,
            })),
        )


def _insert_demo_assets(user_id: int) -> None:
    """A handful of skill / tool / MCP rows owned by the demo user, so the
    Library page shows something interesting. Idempotent via name+kind check.
    """
    assets = [
        # --- Skills ---
        {
            "kind": "skill",
            "name": "Scene beat sheet",
            "description": "Break a scene into opening image, inciting incident, midpoint shift, low point, resolution.",
            "config": {"content_md": (
                "# Scene Beat Sheet\n\n"
                "1. Opening image — what does the audience see first?\n"
                "2. Inciting incident — what disturbs the status quo?\n"
                "3. Midpoint shift — what complication raises the stakes?\n"
                "4. Low point — when does the protagonist seem to fail?\n"
                "5. Resolution — how does the scene land?\n"
            )},
            "metadata": {"category": "screenwriting"},
        },
        {
            "kind": "skill",
            "name": "Pitch deck structure",
            "description": "Canonical investor-deck outline: Problem → Why Now → Solution → Market → Traction → Moat → Team → Ask.",
            "config": {"content_md": (
                "# Pitch Deck Structure\n\n"
                "1. Title — company + one-sentence what-we-do\n"
                "2. Problem — who hurts, how much, evidence\n"
                "3. Why Now — platform shift or behavioral change\n"
                "4. Solution — product + key insight\n"
                "5. Market — TAM/SAM/SOM, bottom-up > top-down\n"
                "6. Traction / Plan — what's working, what's next\n"
                "7. Moat — why this wins in 5 years\n"
                "8. Team — why us, why now\n"
                "9. Ask — amount, use of funds, milestones\n"
            )},
            "metadata": {"category": "fundraising"},
        },
        # --- Tools ---
        {
            "kind": "tool",
            "name": "Markdown export",
            "description": "Render the current run output as a clean markdown document for downstream sharing.",
            "config": {"module": "backend.tools.markdown_export", "fn": "handler"},
            "metadata": {"category": "builtin"},
        },
        # --- MCP ---
        {
            "kind": "mcp",
            "name": "Notion workspace",
            "description": "Read and write Notion pages. Requires a Notion integration token.",
            "config": {"url": ""},
            "metadata": {
                "docs_url": "https://developers.notion.com/",
                "category": "productivity",
                "icon": "📝",
            },
        },
    ]
    for a in assets:
        already = db.fetch_one(
            "SELECT id FROM asset_items WHERE owner_user_id = %s AND kind = %s AND name = %s",
            (user_id, a["kind"], a["name"]),
        )
        if already:
            continue
        db.execute(
            """INSERT INTO asset_items
               (owner_user_id, kind, name, description, enabled,
                config, metadata)
               VALUES (%s, %s, %s, %s, TRUE, %s::jsonb, %s::jsonb)""",
            (user_id, a["kind"], a["name"], a["description"],
             json.dumps(a["config"]), json.dumps(a.get("metadata", {}))),
        )


def _insert_workflow(user_id: int, name: str, description: str,
                     nodes: list[dict]) -> int:
    wf_id = db.execute_returning(
        """INSERT INTO workflows (user_id, name, description, source, is_draft)
           VALUES (%s, %s, %s, 'manual', FALSE) RETURNING id""",
        (user_id, name, description),
    )
    for i, n in enumerate(nodes):
        db.execute(
            """INSERT INTO workflow_nodes
               (workflow_id, position, node_type, agent_id, group_id,
                label, prompt_template)
               VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            (wf_id, i,
             n.get("node_type", "agent"),
             n.get("agent_id"),
             n.get("group_id"),
             n.get("label"),
             n.get("prompt_template")),
        )
    return wf_id


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def seed():
    existing = db.fetch_one("SELECT id FROM as_users WHERE username = %s", ("jay",))
    if existing:
        print("demo user 'jay' already exists — skipping. Delete the user to re-seed.")
        return

    jay_id = _insert_user("jay", "demo", "Jay (demo)")
    print(f"Created user jay (id={jay_id})  —  login: jay / demo")

    # --- Screenwriting Room ---
    screen_ids: dict[str, int] = {}
    for spec in SCREENWRITING_AGENTS:
        screen_ids[spec["name"]] = _insert_agent(jay_id, spec)
    # Lead
    db.execute("UPDATE as_users SET default_lead_agent_id = %s WHERE id = %s",
               (screen_ids["Jade"], jay_id))

    _insert_group(
        jay_id,
        name="Writers Room",
        description=(
            "Sequential round-table. Jade frames the beat, Eli drafts, "
            "Mia edits, Leo checks structure."
        ),
        mode="sequential",
        member_ids=[screen_ids["Eli"], screen_ids["Mia"], screen_ids["Leo"]],
        aggregator_id=screen_ids["Jade"],
    )
    print("Seeded Screenwriting Room (Jade, Eli, Mia, Leo + Writers Room group)")

    # --- Startup Pitch Council ---
    pitch_ids: dict[str, int] = {}
    for spec in PITCH_AGENTS:
        pitch_ids[spec["name"]] = _insert_agent(jay_id, spec)

    founders_group = _insert_group(
        jay_id,
        name="Founders Round",
        description="Three founder archetypes each apply their lens to the pitch.",
        mode="parallel",
        member_ids=[pitch_ids["Travis"], pitch_ids["Brian"], pitch_ids["Patrick"]],
    )
    vc_group = _insert_group(
        jay_id,
        name="VC Review",
        description="Three VC archetypes grade the deck after founders have had their pass.",
        mode="parallel",
        member_ids=[pitch_ids["Mike"], pitch_ids["Marc"], pitch_ids["Bill"]],
    )

    # Workflow: 3-round pitch deck refinement.
    # Round 1 = founders fan-out → Jade-style aggregator synthesizes v1
    # Round 2 = VC critique (parallel) → one of the founders drafts v2 reacting to critiques
    # Round 3 = final polish pass into clean markdown pitch deck
    pitch_wf_id = _insert_workflow(
        jay_id,
        name="Pitch Deck — 3 rounds",
        description=(
            "Round 1: three founder archetypes each propose a pitch deck outline. "
            "Round 2: three VCs critique the merged v1. "
            "Round 3: a founder writes a final markdown deck incorporating critiques."
        ),
        nodes=[
            {
                "node_type": "group",
                "group_id": founders_group,
                "label": "Round 1 · Founders propose",
                "prompt_template": (
                    "Startup idea from the operator: {{input}}\n\n"
                    "Draft a pitch-deck outline (Problem, Why Now, Solution, "
                    "Market, Traction/Plan, Moat, Team, Ask). Stay in your lens."
                ),
            },
            {
                "node_type": "agent",
                "agent_id": pitch_ids["Patrick"],
                "label": "Merge founders into v1",
                "prompt_template": (
                    "Below are three founder outlines:\n\n{{prev_output}}\n\n"
                    "Synthesize them into a single coherent pitch-deck outline v1. "
                    "Keep the sharpest framing from each. Output as markdown."
                ),
            },
            {
                "node_type": "group",
                "group_id": vc_group,
                "label": "Round 2 · VCs critique",
                "prompt_template": (
                    "Pitch-deck outline v1:\n\n{{prev_output}}\n\n"
                    "Grade it (1-10 on your own axes), call out the weakest slide, "
                    "and list three things you'd want to see before writing a check."
                ),
            },
            {
                "node_type": "agent",
                "agent_id": pitch_ids["Brian"],
                "label": "Round 3 · Final markdown deck",
                "prompt_template": (
                    "Original idea: {{input}}\n\n"
                    "VC critiques:\n{{prev_output}}\n\n"
                    "Write the final pitch deck as a clean markdown document. "
                    "Structure: Title slide, Problem, Why Now, Solution, Market, "
                    "Traction/Plan, Moat, Team, Ask. Address every VC critique "
                    "either by fixing the deck or by explicitly explaining why "
                    "the original choice stands. End with an appendix of "
                    "open questions you'd want to be asked in diligence."
                ),
            },
        ],
    )
    print("Seeded Startup Pitch Council (Travis, Brian, Patrick + Mike, Marc, Bill)")
    print("  + Founders Round, VC Review groups")
    print("  + 'Pitch Deck — 3 rounds' workflow")

    # --- Fake activity so Dashboard has numbers to show ---
    all_ids = {**screen_ids, **pitch_ids}
    _insert_fake_activity(jay_id, all_ids, pitch_wf_id)
    print("Seeded fake activity (3 recent runs, 5 queued tasks).")

    # --- Library assets owned by jay so the Library page isn't empty ---
    _insert_demo_assets(jay_id)
    print("Seeded demo assets (skills, tools, MCP).")

    print("\nDone. Log in as jay / demo to explore.")


if __name__ == "__main__":
    seed()
