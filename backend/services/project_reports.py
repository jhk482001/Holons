"""Daily project report — the coordinator writes a short summary of the
day's work (progress, agent contributions, budget burn, next steps),
stored in `project_reports` and surfaced via notification + a message
into the user's Lead thread with a link back to the project page.

Callers:
  * `/api/projects/:id/report/generate` — on-demand.
  * `scheduler` — idempotent daily run (UPSERT by unique(project, date)).
"""
from __future__ import annotations

from datetime import datetime, timezone

from .. import db
from . import notifications
from ..llm_clients import invoke_for_agent as llm_invoke


_PROMPT_TEMPLATE = """You are the coordinator of project **{project_name}**.
Write a concise daily report in markdown for today ({today}).

**Goal**: {goal}

**Team today**:
{team_lines}

**Today's runs + steps (most recent first)**:
{runs_lines}

**Budget burn today**: {budget_line}

Output structure (markdown):
1. One-paragraph status summary — what happened, where we stand.
2. "Per-member highlights" — 1 line each for members who contributed.
3. "Budget" — 1 line.
4. "Next up" — 2–3 bullets. If the project is paused / done / blocked, say so here.

Keep it tight (200–350 words). No preamble. Do not fabricate — only reference
work that actually appears in the run list.
"""


def _gather_context(project_id: int) -> dict:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    proj = db.fetch_one(
        "SELECT name, goal, status, coordinator_agent_id FROM projects WHERE id = %s",
        (project_id,),
    ) or {}

    members = db.fetch_all(
        """
        SELECT a.id, a.name, a.role_title,
               pm.daily_alloc_pct, pm.monthly_alloc_pct
        FROM project_members pm JOIN agents a ON a.id = pm.agent_id
        WHERE pm.project_id = %s ORDER BY pm.id
        """,
        (project_id,),
    )

    runs = db.fetch_all(
        """
        SELECT r.id, r.status, r.started_at, r.finished_at,
               w.name AS workflow_name,
               (SELECT COUNT(*) FROM run_steps s WHERE s.run_id = r.id) AS steps,
               (SELECT COALESCE(SUM(s.cost_usd), 0)::float FROM run_steps s
                WHERE s.run_id = r.id) AS cost
        FROM runs r LEFT JOIN workflows w ON w.id = r.workflow_id
        WHERE r.project_id = %s
          AND r.started_at >= NOW() - INTERVAL '1 day'
        ORDER BY r.id DESC LIMIT 20
        """,
        (project_id,),
    )

    cost_today = db.fetch_one(
        """
        SELECT COALESCE(SUM(cost_usd), 0)::float AS c,
               COALESCE(SUM(input_tokens + output_tokens), 0) AS t
        FROM run_steps
        WHERE project_id = %s AND started_at >= NOW() - INTERVAL '1 day'
        """,
        (project_id,),
    ) or {}

    return {
        "today": today,
        "project": proj,
        "members": members,
        "runs": runs,
        "cost_today": cost_today,
    }


def _format_prompt(ctx: dict) -> str:
    p = ctx["project"]
    team_lines = "\n".join(
        f"- {m['name']} ({m.get('role_title') or 'agent'}) · "
        f"daily slice {int(m.get('daily_alloc_pct') or 100)}%"
        for m in ctx["members"]
    ) or "(no members)"

    if ctx["runs"]:
        runs_lines = "\n".join(
            f"- run #{r['id']} {r.get('workflow_name') or '-'} · "
            f"{r['status']} · {int(r.get('steps') or 0)} steps · "
            f"${float(r.get('cost') or 0):.3f}"
            for r in ctx["runs"]
        )
    else:
        runs_lines = "(no runs in the last 24h)"

    cost = float(ctx["cost_today"].get("c") or 0)
    tokens = int(ctx["cost_today"].get("t") or 0)
    budget_line = f"${cost:.3f} · {tokens:,} tokens across all members"

    return _PROMPT_TEMPLATE.format(
        project_name=p.get("name", "?"),
        today=ctx["today"],
        goal=p.get("goal") or "(no explicit goal set)",
        team_lines=team_lines,
        runs_lines=runs_lines,
        budget_line=budget_line,
    )


def generate(project_id: int, *, force: bool = False) -> dict:
    """Produce (or refresh) today's project report. Idempotent per date
    unless `force=True` — re-running the same day updates the summary.
    """
    ctx = _gather_context(project_id)
    proj = ctx["project"]
    coord_id = proj.get("coordinator_agent_id")
    if not coord_id:
        return {"error": "project has no coordinator"}

    existing = db.fetch_one(
        "SELECT id FROM project_reports WHERE project_id = %s AND report_date = %s",
        (project_id, ctx["today"]),
    )
    if existing and not force:
        return {"id": existing["id"], "already_exists": True}

    prompt = _format_prompt(ctx)
    coord = db.fetch_one(
        "SELECT name, system_prompt, primary_model_id FROM agents WHERE id = %s",
        (coord_id,),
    ) or {}

    # Project's owning user — needed so llm_calls rows land with the
    # right user_id. projects table already has user_id.
    _proj_user_row = db.fetch_one(
        "SELECT user_id FROM projects WHERE id = %s", (project_id,),
    ) or {}
    result = llm_invoke(
        agent_id=coord_id,
        model_key=coord.get("primary_model_id"),
        system_prompt=(coord.get("system_prompt") or "") +
                      "\n\nYou write concise, honest daily project reports.",
        user_text=prompt,
        user_id=_proj_user_row.get("user_id"),
        kind="project_report",
        prefer_user_default=True,
    )
    summary = (result.get("text") or "").strip() or "(no report generated)"

    metrics = {
        "today_cost_usd": float(ctx["cost_today"].get("c") or 0),
        "today_tokens": int(ctx["cost_today"].get("t") or 0),
        "today_runs": len(ctx["runs"]),
    }

    if existing:
        db.execute(
            """UPDATE project_reports
               SET summary_md = %s, metrics = %s::jsonb,
                   coordinator_agent_id = %s
               WHERE id = %s""",
            (summary, _jsonb(metrics), coord_id, existing["id"]),
        )
        report_id = existing["id"]
    else:
        report_id = db.execute_returning(
            """INSERT INTO project_reports
               (project_id, report_date, coordinator_agent_id,
                summary_md, metrics)
               VALUES (%s, %s, %s, %s, %s::jsonb) RETURNING id""",
            (project_id, ctx["today"], coord_id, summary, _jsonb(metrics)),
        )

    # Post a short pointer into the user's main Lead thread so they see it.
    user_id_row = db.fetch_one(
        "SELECT user_id FROM projects WHERE id = %s", (project_id,)
    ) or {}
    user_id = user_id_row.get("user_id")
    if user_id:
        _post_to_lead_thread(user_id, project_id, proj.get("name", "?"),
                             report_id, summary)
        notifications.emit(
            user_id,
            "project_report",
            severity="info",
            title=f"Daily report: {proj.get('name')}",
            body=f"{coord.get('name') or 'Coordinator'} filed today's update.",
            action_payload={"project_id": project_id, "report_id": report_id},
        )
        _fire_webhook(user_id, project_id, proj.get("name"), report_id, summary, metrics)

    return {"id": report_id, "summary_md": summary, "metrics": metrics}


def _fire_webhook(user_id: int, project_id: int, project_name: str | None,
                  report_id: int, summary_md: str, metrics: dict) -> None:
    """Best-effort POST to the user's configured report webhook. Failures
    are logged and swallowed — we never block report generation on external
    endpoints.
    """
    import urllib.request
    import urllib.error
    import logging

    row = db.fetch_one(
        "SELECT report_webhook_url FROM as_users WHERE id = %s", (user_id,)
    ) or {}
    url = (row.get("report_webhook_url") or "").strip()
    if not url or not url.startswith(("http://", "https://")):
        return
    payload = {
        "event": "project_report",
        "project_id": project_id,
        "project_name": project_name,
        "report_id": report_id,
        "summary_md": summary_md,
        "metrics": metrics,
    }
    try:
        req = urllib.request.Request(
            url,
            data=_jsonb(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=5) as _:
            pass
    except Exception as e:
        logging.getLogger("agent_company.reports").warning(
            "report webhook failed: %s", e
        )


def _post_to_lead_thread(user_id: int, project_id: int, project_name: str,
                         report_id: int, summary_md: str) -> None:
    """Append a one-liner to the user's main Lead thread pointing at the report.
    Reuses lead_agent's thread helpers so it shows up in the user's inbox.
    """
    from . import lead_agent
    thread_id = lead_agent.latest_thread_or_create(user_id)
    snippet = summary_md.splitlines()[0].strip() if summary_md else ""
    body = (
        f"📋 **Project update — {project_name}**\n\n"
        f"{snippet}\n\n[Open project](/projects/{project_id}) "
        f"· report #{report_id}"
    )
    import json as _json
    db.execute(
        """INSERT INTO lead_messages (thread_id, role, content, metadata)
           VALUES (%s, 'lead', %s, %s::jsonb)""",
        (thread_id, body, _json.dumps({
            "event": "project_report",
            "project_id": project_id,
            "report_id": report_id,
        })),
    )


def _jsonb(d: dict) -> str:
    import json as _json
    return _json.dumps(d)


def list_reports(project_id: int, limit: int = 60) -> list[dict]:
    return db.fetch_all(
        """SELECT id, report_date, coordinator_agent_id, summary_md, metrics,
                  created_at
           FROM project_reports
           WHERE project_id = %s
           ORDER BY report_date DESC LIMIT %s""",
        (project_id, limit),
    )
