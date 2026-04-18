"""End-to-end smoke test for the project + quota feature.

Flow:
  1. Log in as `jay` / `demo`.
  2. Ensure a "Screenplay Brainstorm" project exists with Jade (coord),
     Eli, Mia, Leo as members — each daily allocation 100%.
  3. Cap each member's per-agent daily cost at $2 by upserting an
     `agent_quotas` row.
  4. Send a broad brief to the coordinator chat; receive either a direct
     response or a proposed workflow.
  5. If a workflow was proposed, run it with project attribution and
     poll until finished (or a quota block happens).
  6. Report usage by member + per-project totals.

Run from repo root:
    python -m demo.test_project_e2e
"""
from __future__ import annotations

import json
import os
import sys
import time
from urllib.parse import urljoin

import urllib.request
import urllib.error


BASE = os.environ.get("AC_BASE", "http://localhost:8087")
USERNAME = "jay"
PASSWORD = "demo"
PROJECT_NAME = "Screenplay Brainstorm"
AGENT_DAILY_COST_CAP = 2.00  # USD per agent per day

BRIEF = (
    "Brainstorm a mystery thriller set in a small coastal town. "
    "Break the premise into act structure, sketch three main characters, "
    "and draft a 500-word outline of act one. Keep it to one lean pass per "
    "member — don't overspend. Produce a compact markdown document at the end."
)


class Client:
    def __init__(self):
        self.cookie = None

    def _req(self, method: str, path: str, body: dict | None = None):
        url = urljoin(BASE, path)
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(
            url, data=data, method=method,
            headers={"Content-Type": "application/json",
                     **({"Cookie": self.cookie} if self.cookie else {})},
        )
        try:
            with urllib.request.urlopen(req, timeout=180) as r:
                body_bytes = r.read()
                # Save session cookie on first response
                sc = r.getheader("Set-Cookie")
                if sc and "session=" in sc:
                    self.cookie = sc.split(";")[0]
                return json.loads(body_bytes.decode("utf-8")) if body_bytes else {}
        except urllib.error.HTTPError as e:
            print(f"  HTTP {e.code} on {method} {path}: {e.read().decode()[:200]}")
            raise

    def post(self, path, body=None): return self._req("POST", path, body)
    def get(self, path):              return self._req("GET", path)
    def put(self, path, body=None):   return self._req("PUT", path, body)


# ---------------------------------------------------------------------------
# Helpers that hit the DB directly (quotas are DB-level for this demo).
# ---------------------------------------------------------------------------

def upsert_agent_cap(agent_id: int, cost_cap: float):
    from backend import db
    existing = db.fetch_one(
        "SELECT id FROM agent_quotas WHERE agent_id = %s AND name = %s",
        (agent_id, "demo-daily-cap"),
    )
    if existing:
        db.execute(
            """UPDATE agent_quotas
               SET max_cost_usd = %s, window_type = 'daily', enabled = TRUE,
                   hard_limit = TRUE, current_cost_usd = 0, current_tokens = 0
               WHERE id = %s""",
            (cost_cap, existing["id"]),
        )
        return existing["id"]
    return db.execute_returning(
        """INSERT INTO agent_quotas
           (agent_id, name, window_type, max_cost_usd, hard_limit, enabled)
           VALUES (%s, 'demo-daily-cap', 'daily', %s, TRUE, TRUE)
           RETURNING id""",
        (agent_id, cost_cap),
    )


def clear_project(name: str, user_id: int):
    from backend import db
    db.execute(
        "DELETE FROM projects WHERE name = %s AND user_id = %s",
        (name, user_id),
    )


def wait_for_run(client: Client, run_id: int, timeout_s: int = 300):
    deadline = time.time() + timeout_s
    last_status = None
    while time.time() < deadline:
        run = client.get(f"/api/runs/{run_id}")
        s = run.get("status")
        if s != last_status:
            print(f"  run #{run_id} status: {s}")
            last_status = s
        if s in ("done", "error"):
            return run
        time.sleep(2)
    print(f"  timed out waiting for run #{run_id}")
    return None


# ---------------------------------------------------------------------------
# Main flow
# ---------------------------------------------------------------------------

def main():
    from backend import db

    print(f"· Logging in as {USERNAME} / {PASSWORD}")
    c = Client()
    me = c.post("/api/login", {"username": USERNAME, "password": PASSWORD})
    user_id = me["id"]
    print(f"  user_id={user_id}")

    # Clean slate for the project (but keep agents + teams seeded earlier).
    clear_project(PROJECT_NAME, user_id)

    # Find the screenwriting team. They were seeded by demo.seed_demo.
    agent_ids: dict[str, int] = {}
    for name in ("Jade", "Eli", "Mia", "Leo"):
        row = db.fetch_one(
            "SELECT id FROM agents WHERE user_id = %s AND name = %s",
            (user_id, name),
        )
        if not row:
            print(f"  ! missing agent {name} — run `python -m demo.seed_demo` first")
            sys.exit(2)
        agent_ids[name] = row["id"]
    print("  agents:", agent_ids)

    # Cap each member at $2/day to exercise quota enforcement.
    for name, aid in agent_ids.items():
        qid = upsert_agent_cap(aid, AGENT_DAILY_COST_CAP)
        print(f"  capped {name} at ${AGENT_DAILY_COST_CAP:.2f}/day (quota id={qid})")

    # Create the project with Jade as coordinator.
    res = c.post("/api/projects", {
        "name": PROJECT_NAME,
        "description": "Test: mystery thriller in a coastal town.",
        "goal": "Produce a 500-word markdown outline within a tight per-agent budget.",
        "coordinator_agent_id": agent_ids["Jade"],
        "members": [
            {"agent_id": agent_ids[n], "daily_alloc_pct": 100, "monthly_alloc_pct": 100}
            for n in ("Jade", "Eli", "Mia", "Leo")
        ],
    })
    pid = res["id"]
    print(f"· Created project id={pid}")

    # Ask the coordinator to draft a workflow.
    print("· Sending brief to coordinator chat…")
    chat = c.post(f"/api/projects/{pid}/chat", {"message": BRIEF})
    print(f"  coordinator reply (first 400 chars):\n  {chat.get('response', '')[:400]}…")
    proposed_id = chat.get("proposed_workflow_id")

    if not proposed_id:
        print("· Coordinator did not propose a workflow. "
              "Answering directly and stopping here.")
    else:
        print(f"· Coordinator proposed workflow #{proposed_id}. Running…")
        try:
            run = c.post(f"/api/workflows/{proposed_id}/run", {
                "input": BRIEF,
                "project_id": pid,
                "trigger_source": "api",
            })
            rid = run.get("run_id")
            print(f"  run_id={rid}")
            final = wait_for_run(c, rid, timeout_s=360)
            if final and final.get("final_output"):
                print("· Final output (first 600 chars):\n",
                      final["final_output"][:600], "\n…")
            elif final:
                print(f"· Run finished with status={final.get('status')} "
                      f"and no final_output captured.")
        except Exception as e:
            print(f"  run dispatch failed: {e}")

    # Report usage attribution.
    print("\n· Usage summary:")
    usage = c.get(f"/api/usage/daily?group_by=agent&project_id={pid}&days=1")
    total = sum(r["cost"] for r in usage["rows"])
    for r in usage["rows"]:
        print(f"  {r['date']} {r['label']:6}  ${r['cost']:.3f}  {r['tokens']:,} tokens")
    print(f"  Project total today: ${total:.3f}")

    # Show whether anyone hit their cap (flags from agent_quotas table).
    caps = db.fetch_all(
        """
        SELECT a.name, aq.current_cost_usd::float AS spent, aq.max_cost_usd::float AS cap
        FROM agent_quotas aq JOIN agents a ON a.id = aq.agent_id
        WHERE a.user_id = %s AND aq.name = 'demo-daily-cap'
        """,
        (user_id,),
    )
    print("\n· Per-agent quota state:")
    for r in caps:
        pct = (r["spent"] / r["cap"]) * 100 if r["cap"] else 0
        mark = "🔴" if r["spent"] >= r["cap"] else "🟢"
        print(f"  {mark} {r['name']:6}  ${r['spent']:.3f}/${r['cap']:.2f} ({pct:.0f}%)")

    print("\nDone.")


if __name__ == "__main__":
    main()
