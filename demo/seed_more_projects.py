"""Seed four more ongoing projects for demo user `jay` (on top of
"Screenplay Brainstorm" which already exists). Idempotent — skips any
project with a matching name.

Run:
    python -m demo.seed_more_projects
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend import db  # noqa: E402


def _agents_by_name(user_id: int) -> dict[str, int]:
    rows = db.fetch_all(
        "SELECT id, name FROM agents WHERE user_id = %s", (user_id,)
    )
    return {r["name"]: r["id"] for r in rows}


def _exists(user_id: int, name: str) -> bool:
    return bool(db.fetch_one(
        "SELECT id FROM projects WHERE user_id = %s AND name = %s",
        (user_id, name),
    ))


def _create(user_id: int, spec: dict, agent_ids: dict[str, int]) -> int | None:
    if _exists(user_id, spec["name"]):
        print(f"· skip '{spec['name']}' (already exists)")
        return None
    coord_id = agent_ids.get(spec["coordinator"])
    pid = db.execute_returning(
        """INSERT INTO projects
           (user_id, name, description, goal, status, coordinator_agent_id)
           VALUES (%s, %s, %s, %s, %s, %s) RETURNING id""",
        (user_id, spec["name"], spec["description"], spec["goal"],
         spec.get("status", "active"), coord_id),
    )
    for member_name, alloc in spec["members"]:
        aid = agent_ids.get(member_name)
        if not aid:
            continue
        db.execute(
            """INSERT INTO project_members
               (project_id, agent_id, daily_alloc_pct, monthly_alloc_pct)
               VALUES (%s, %s, %s, %s)""",
            (pid, aid, alloc, alloc),
        )
    print(f"· created '{spec['name']}' id={pid} coord={spec['coordinator']}")
    return pid


def main():
    user = db.fetch_one("SELECT id FROM as_users WHERE username = 'jay'")
    if not user:
        print("! user 'jay' not found — run `python -m demo.seed_demo` first")
        sys.exit(2)
    uid = user["id"]
    agents = _agents_by_name(uid)

    projects = [
        {
            "name": "Pitch Deck Bakeoff",
            "description": (
                "A B2B fintech pitch runs through three founder lenses and "
                "three VC lenses back-to-back, ending in a single polished deck."
            ),
            "goal": (
                "Produce a revised pitch deck in markdown that addresses every "
                "VC critique, with an explicit diligence-questions appendix."
            ),
            "coordinator": "Patrick",
            "members": [
                ("Travis", 60), ("Brian", 60), ("Patrick", 100),
                ("Mike", 40), ("Marc", 40), ("Bill", 40),
            ],
        },
        {
            "name": "Character Development Workshop",
            "description": (
                "Deepen three main characters for the coastal mystery: "
                "backstory, wants / needs, moral tension, and an iconic scene each."
            ),
            "goal": (
                "Ship three character dossiers (500 words each) plus one "
                "signature-scene draft per character."
            ),
            "coordinator": "Leo",
            "members": [
                ("Eli", 60), ("Mia", 60), ("Leo", 80),
            ],
        },
        {
            "name": "Startup Due-Diligence Prep",
            "description": (
                "Prep a diligence pack for a hypothetical Series A investment "
                "in a vertical AI company. Market map, unit economics questions, "
                "product-vs-moat framing."
            ),
            "goal": (
                "Output a ten-question diligence memo with expected-answer "
                "ranges and deal-breaker thresholds."
            ),
            "coordinator": "Mike",
            "members": [
                ("Mike", 60), ("Marc", 40), ("Bill", 40),
            ],
        },
        {
            "name": "Season One Bible",
            "description": (
                "Expand the coastal-mystery premise into a full season-one bible: "
                "8-episode arc, character threads, theme per episode, cliffhangers."
            ),
            "goal": (
                "Produce a 10-page show bible suitable for a pilot sale packet."
            ),
            "coordinator": "Jade",
            "members": [
                ("Jade", 100), ("Eli", 50), ("Mia", 50), ("Leo", 70),
            ],
        },
    ]

    created = 0
    for spec in projects:
        if _create(uid, spec, agents) is not None:
            created += 1

    total = db.fetch_one(
        "SELECT COUNT(*) AS n FROM projects WHERE user_id = %s AND status = 'active'",
        (uid,),
    )
    print(f"\nDone. Created {created} new projects. "
          f"Total active projects for jay: {total['n']}.")


if __name__ == "__main__":
    main()
