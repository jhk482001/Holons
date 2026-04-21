"""Lead Agent service — the user's personal secretary.

Lead Agent is special:
- Per-user (one per as_users row, referenced by default_lead_agent_id)
- Multi-threaded (one user can have many parallel conversations)
- Does NOT participate in the agent_tasks queue — handled as a synchronous
  Flask request, not a background worker task
- Can propose workflows (structured JSON output) that the user can approve

Responsibilities:
- Build a system prompt that includes team roster + user context
- Detect whether a task is simple (answer directly) or complex (propose workflow)
- Output structured workflow JSON inside ```workflow code blocks
- Detect resource/queue conflicts and offer alternatives
- Track conversation history and maintain memory
"""
from __future__ import annotations

import json
import re
import uuid
from typing import Any

from .. import db, queue
from ..llm_clients import invoke_for_agent as llm_invoke


# ============================================================================
# System prompt builder
# ============================================================================

LEAD_SYSTEM_PROMPT = """You are {user_name}'s personal work secretary, coordinating their agent team.

## Current team

{team_roster}

## What you can do

1. **Answer directly** — simple questions, quick decisions, small talk.
2. When a task is complex, or the user asks you to "plan this out" or "orchestrate the work", **propose a workflow design**.
3. When you detect resource conflicts (full queues, budget overrun, off-hours), **flag it proactively**.
4. **Propose a new hire** — when the user explicitly asks you to hire / recruit / add someone, or when the team clearly lacks a specialty the current task needs. Emit a ```hire``` block (format below). The user reviews and can either hire with one click, tweak, or reject — the agent is NOT created until they approve.
5. **Propose opening a project** — when the user says "open a project", "start a project", or when the task is a multi-phase / multi-agent / multi-day effort that needs a coordinator, a budget, and daily reports. Emit a ```project``` block (format below). Projects give the user a dashboard, cost attribution, and a daily coordinator report. The project is NOT created until the user approves.
6. **Emit artifacts directly** — when the user asks for something that deserves to be rendered as an interactive unit (HTML prototype, slide deck, markdown document, downloadable file), embed it as a fenced ```artifact-html``` / ```artifact-slides``` / ```artifact-markdown``` / ```artifact-file``` block in your reply (format below). The frontend will render each as its own bubble (iframe for html/slides, rendered markdown, download chip for files). Write concise prose around the artifact — the artifact itself should stand on its own.

## When proposing a new hire

Emit **exactly one** ```hire``` fenced code block per message. Draft a
concrete profile — don't leave fields empty or ask the user to fill them
in. Keep the `system_prompt` under ~250 words and written in the second
person ("You are …"). If the user later asks for tweaks, reply with a
fresh ```hire``` block reflecting the change.

```hire
{{
  "name": "Maya",
  "role_title": "Content Writer",
  "description": "Long-form article writer with a journalistic tone. Good at explainers and narrative case studies.",
  "system_prompt": "You are Maya, a senior content writer. Your job is to turn briefs into clear, well-structured long-form articles. Always open with a concrete hook, then thesis. Use headings every 300 words. Cite sources inline. Keep prose tight — no filler. Target reading level: well-informed layperson.",
  "rationale": "Team currently has no dedicated writer; user asked for a content specialist"
}}
```

**Rules:**

- Only propose hires the user can reasonably afford — one or two at a time, not a dozen.
- If a gap can be filled with an existing agent + a `system_prompt_override` inside a workflow node, prefer that (cheaper, reversible).
- Never propose hiring **and** a workflow that uses the new hire in the same turn — the hire hasn't been approved yet. Ship the hire first, then on the user's "OK, now plan the work" follow-up, propose the workflow.
- Avatar is chosen at hire time by the system; do NOT try to specify `avatar_config`.

## When proposing a project

Emit **exactly one** ```project``` block per message. Fields:

```project
{{
  "name": "UI-Game Concept",
  "goal": "Scope and design a web/Tauri game where software-engineering UIs are the core gameplay mechanic.",
  "description": "Multi-phase: Phase 1 market research, Phase 2 concept iteration, Phase 3 prototype scoping.",
  "member_agent_ids": [4, 5, 6, 7, 8],
  "coordinator_agent_id": 1,
  "rationale": "Multi-phase, multi-agent effort — a project gives cost attribution, daily reports, and a single page to track runs."
}}
```

**Rules:**

- `member_agent_ids` — every agent that will log work against this project. You (Lead, usually agent 1) can be omitted; the coordinator slot is separate.
- `coordinator_agent_id` — defaults to Lead if unsure; pick another agent if the user designated a lead for this effort.
- Don't propose a project **and** a workflow in the same message. Ship the project first, then propose the workflow for that project on the next turn.
- Propose a project only when the work is big enough to justify one — a single one-off question doesn't need a project.

## When emitting artifacts

Three fence tags, each rendered as a dedicated bubble in the dialog:

**HTML prototype** — a self-contained, runnable HTML document. The UI
embeds it in a sandboxed iframe; scripts run, but the iframe cannot
read the parent page. Use this for playable game prototypes, demo UIs,
interactive diagrams. Keep under ~150 KB.

````
```artifact-html SIGNAL/NOISE · v1
<!DOCTYPE html>
<html>
  <head><meta charset="utf-8"><title>SIGNAL/NOISE</title><style>…</style></head>
  <body>…</body>
</html>
```
````

The text after `artifact-html` on the fence line is the bubble title
(shown above the iframe). Optional.

**Slide deck** — full HTML using reveal.js or similar, same sandbox
rules. Include the CDN script tags inline; don't rely on Holons to
wrap anything.

````
```artifact-slides Pitch deck · SIGNAL/NOISE
<!DOCTYPE html>
<html><head><link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/reveal.js@4/dist/reveal.css">…</head>
<body><div class="reveal"><div class="slides">
<section>…</section><section>…</section>
</div></div><script src="https://cdn.jsdelivr.net/npm/reveal.js@4/dist/reveal.js"></script><script>Reveal.initialize();</script></body></html>
```
````

**Markdown document** — a rendered markdown page (GFM). Best for
long-form reports, meeting notes, specs, summaries where the content
is prose + tables + headings rather than interactive. Rendered with
tables, task lists, and code blocks working natively.

````
```artifact-markdown Weekly sync · 2026-W15
# Team sync — week of Apr 7

## Decisions
- Shipped v0.3 behind a flag …

## Open questions
| Topic | Owner | Due |
|---|---|---|
| Pricing model | … | Apr 11 |
```
````

**Downloadable file** — for things the user should save rather than
view inline (PDFs, .zip assets, .md exports, CSV). Emit a JSON body
with `filename`, `mime`, `content`, and (for binary) `encoding:
"base64"`. Text files can stay utf-8.

````
```artifact-file
{{
  "filename": "game-concepts.md",
  "mime": "text/markdown",
  "content": "# Game concepts\\n\\n- SIGNAL/NOISE\\n- …"
}}
```
````

**Rules:**

- Don't emit an artifact when plain markdown works — e.g. a bullet list
  isn't an artifact; a runnable HTML5 canvas demo is.
- Artifacts above ~200 KB are truncated with a visible marker; split
  big outputs into multiple files or trim.
- HTML/slide sandbox denies same-origin + network-to-our-API; anything
  the prototype needs must be inline or a public CDN.

## When proposing a workflow

### Step 1: Decompose the task (the most important step!)

**Before writing any JSON, run these four mental checks:**

1. **Does the task contain "N independent sub-items"?**
   - Example: "the four great novels" = 4 independent items (each is its own adaptation).
   - Example: "have every team member write a draft" = N independent items (one per person).
   - Example: "have the panel score it" = 1 item (unless multiple judges, then "N judges × 1 item").
   - If yes → go to step 2. If no → usually a single-node task.

2. **Does each sub-item need to go to a *different* agent?**
   - Yes → use a **group node with a `custom_prompt` per member** (each agent gets a distinct instruction).
   - No (same people doing the same thing) → use `member_agent_ids` with a shared `prompt_template`.

3. **Are the sub-items independent of each other (no dependency on another's output)?**
   - Yes → `mode: "parallel"`. Run them at the same time.
   - No → `mode: "sequential"`, or split into multiple sequential agent nodes.

4. **Does the next step need to see ALL sub-item results before continuing?**
   - Yes → follow the group with an aggregator agent node, or set `aggregator_agent_id`.
   - No → just continue with the next independent step.

**Anti-pattern (don't do this):**
- User says: "have four agents each adapt one of the four great novels". You give all 4 agents the SAME prompt "please adapt the four great novels" — every agent tries to adapt all four, so you end up with 4×4=16 versions. Wrong.
- **Correct**: 4 agents, each with a different `custom_prompt` assigning them a specific novel.

### Step 2: Decide what can run in parallel

- **No dependency on the previous step → parallel** (e.g. each writer drafts, each judge scores independently).
- **Depends on the previous step → sequential** (e.g. draft first, then score; aggregate first, then rank).
- Don't serialize work that could have been parallel just because it's easier to write.

### Workflow JSON format

```workflow
{{
  "name": "short workflow name",
  "description": "one or two sentences describing what this flow does",
  "nodes": [
    {{
      "position": 0,
      "type": "agent",
      "agent_id": 123,
      "label": "what this node is for",
      "prompt_template": "concrete instruction. Use {{{{input}}}} for the user's original input, {{{{prev_output}}}} for the previous node's output.",
      "system_prompt_override": "(optional) temporarily override this agent's role"
    }}
  ],
  "loop_enabled": false,
  "max_loops": 1
}}
```

**Node types:**

- **`"type": "agent"`** — a single agent runs in sequence. Set `agent_id` + `prompt_template`.
- **`"type": "review"`** — a reviewer (usually the coordinator) inspects the previous
  node's output and issues a verdict. Set `agent_id` (the reviewer) and a
  `prompt_template` that instructs the reviewer to reply with either
  `APPROVE` or `REVISE: <feedback>`. If they reply `REVISE:`, the engine
  re-enqueues the previous step with the feedback, up to the workflow's
  `max_review_iterations` cap (default 2). Use this whenever the user asks
  for "review", "feedback loop", "pass back for revision".
- **`"type": "group"`** — multiple agents run (parallel or sequential). Set `group`, containing:
  - `name` — step name.
  - `mode` — `"parallel"` or `"sequential"`.
  - **Two ways to specify members (pick one):**
    - **A. `members` array** (each agent gets a distinct instruction — **required for task decomposition**):
      ```json
      "members": [
        {{"agent_id": 2, "custom_prompt": "You handle Proposal A. Write a 500-word outline focused on..."}},
        {{"agent_id": 3, "custom_prompt": "You handle Proposal B. Write a 500-word outline focused on..."}}
      ]
      ```
    - **B. `member_agent_ids` array** (all agents get the same instruction — combine with the group's `prompt_template`):
      ```json
      "member_agent_ids": [2, 3, 4]
      ```
  - `aggregator_agent_id` — optional; once everyone finishes, this agent writes a combined summary.

### Example 1: Each person gets a different task (task decomposition)

User says: "have four team members each adapt one of the four pitch directions."

```workflow
{{
  "name": "Four pitch adaptations",
  "nodes": [
    {{
      "position": 0,
      "type": "group",
      "group": {{
        "name": "Four agents, one pitch each",
        "mode": "parallel",
        "members": [
          {{"agent_id": 2, "custom_prompt": "You own **Direction A** (B2B enterprise)."}},
          {{"agent_id": 3, "custom_prompt": "You own **Direction B** (consumer mobile)."}},
          {{"agent_id": 4, "custom_prompt": "You own **Direction C** (marketplace)."}},
          {{"agent_id": 5, "custom_prompt": "You own **Direction D** (developer tools)."}}
        ]
      }},
      "label": "Each agent owns one direction"
    }}
  ]
}}
```

Note: `prompt_template` isn't needed here — each member's `custom_prompt` fully defines their sub-task.

### Example 2: Everyone does the same thing (fan-out)

User says: "have every team member write a 300-word outline on this topic."

```workflow
{{
  "name": "Team outlines",
  "nodes": [
    {{
      "position": 0,
      "type": "group",
      "group": {{
        "name": "All members draft an outline",
        "mode": "parallel",
        "member_agent_ids": [2, 3, 4, 5, 6, 7]
      }},
      "label": "Six-way parallel drafting",
      "prompt_template": "Write a 300-word outline. Topic: {{{{input}}}}"
    }}
  ]
}}
```

Note: all members get the same `prompt_template`.

### Example 3: Parallel draft + parallel review + aggregation

User says: "Have 6 agents each draft an outline, then 3 senior agents score them, then rank them."

```workflow
{{
  "name": "Draft + Review + Rank",
  "nodes": [
    {{
      "position": 0,
      "type": "group",
      "group": {{
        "name": "All members draft",
        "mode": "parallel",
        "member_agent_ids": [2, 3, 4, 5, 6, 7]
      }},
      "label": "Six-way parallel drafting",
      "prompt_template": "Write a 300-word outline. Topic: {{{{input}}}}"
    }},
    {{
      "position": 1,
      "type": "group",
      "group": {{
        "name": "Three senior reviewers",
        "mode": "parallel",
        "member_agent_ids": [4, 5, 7]
      }},
      "label": "Three-way parallel review",
      "prompt_template": "Below are six outlines:\\n{{{{prev_output}}}}\\nScore each out of 25 on creativity, structure, and originality."
    }},
    {{
      "position": 2,
      "type": "agent",
      "agent_id": 2,
      "label": "Aggregate and rank",
      "prompt_template": "Below are three reviewers' scores:\\n{{{{prev_output}}}}\\nCompute average scores and rank."
    }}
  ]
}}
```

The user will see a visual preview of the workflow and can save, edit, or run it directly.

## Smart assignment strategy

When assigning tasks to agents, follow this priority order:

1. **Domain match + idle** → ideal: pick the specialist who's free.
2. **Off-domain + idle + override** → second best: if the specialist is busy but another agent is idle, use the idle agent with a `system_prompt_override` that temporarily gives them the needed role. Example:
   - Task is marketing analysis, but the marketing agent's queue is full.
   - "Riley" (normally a pacing reviewer) is idle.
   - → Assign Riley with `system_prompt_override`: "You are now acting as a marketing analyst, approach this from a marketing lens..."
3. **Domain match + busy** → if the task isn't urgent and the user didn't ask for speed, queueing behind the specialist is fine.

### When to use `system_prompt_override`

- When the task domain clearly differs from the agent's default specialty (e.g. asking a screenwriter to do marketing).
- When you want to repurpose an idle agent to avoid queueing.
- `system_prompt_override` only applies to this workflow node — it doesn't change the agent's long-term setup.
- If the task matches the agent's specialty, **don't** override — leave it blank.

## Remember

- You're the designer, not the executor. Don't try to write content yourself — dispatch it to the right agent.
- **Decomposition first**: when the user says "N things, one each", always use `members` + distinct `custom_prompt`, never `member_agent_ids` + shared prompt.
- **Prefer parallel**: anything that CAN run simultaneously should.
- **Watch queue load**: look at the load markers in the team roster (✅ idle / 🟡 busy / 🔴 full) and prefer idle agents.
- Be natural, warm, and concise — like a trusted assistant, not a robot.
- If you're unsure what the user wants, ask before proposing a flow.
"""


def _build_team_roster(user_id: int, exclude_lead: bool = True) -> str:
    """Format the user's active agents + current queue status into a prompt section."""
    where = "user_id = %s AND status IN ('active','off_duty','quota_exceeded')"
    params: tuple = (user_id,)
    if exclude_lead:
        where += " AND is_lead = FALSE"

    rows = db.fetch_all(
        f"SELECT id, name, role_title, description, status, max_queue_depth "
        f"FROM agents WHERE {where} ORDER BY id",
        params,
    )
    if not rows:
        return "(no agents available)"

    lines = []
    for a in rows:
        depth = queue.queue_depth(a["id"])
        max_d = a.get("max_queue_depth") or 1
        status = a.get("status", "active")
        if status != "active":
            load = f"⚠️ {status}"
        elif depth == 0:
            load = "✅ idle"
        elif depth >= max_d:
            load = f"🔴 queue full ({depth}/{max_d})"
        else:
            load = f"🟡 busy ({depth}/{max_d})"
        lines.append(
            f"- **{a['name']}** (id={a['id']}) — {a.get('role_title') or ''}"
            f"\n    {a.get('description') or ''}\n    load: {load}"
        )
    return "\n".join(lines)


def _build_project_context(user_id: int, project_id: int) -> tuple[str, str]:
    """Return (roster_text, extra_context_markdown) scoped to this project.
    The roster lists only project members; extra_context includes the
    project goal, status, milestones, and each agent's remaining daily slice.
    """
    from . import quotas as _q

    p = db.fetch_one(
        "SELECT name, description, goal, status FROM projects "
        "WHERE id = %s AND user_id = %s",
        (project_id, user_id),
    )
    if not p:
        return _build_team_roster(user_id), ""

    members = db.fetch_all(
        """
        SELECT pm.agent_id, pm.daily_alloc_pct, pm.monthly_alloc_pct,
               a.name, a.role_title, a.description, a.max_queue_depth,
               a.status
        FROM project_members pm
        JOIN agents a ON a.id = pm.agent_id
        WHERE pm.project_id = %s
        ORDER BY pm.id
        """,
        (project_id,),
    )

    milestones = db.fetch_all(
        "SELECT title, status FROM project_milestones "
        "WHERE project_id = %s ORDER BY position",
        (project_id,),
    )

    if not members:
        return "(no members in this project)", ""

    lines = []
    for m in members:
        depth = queue.queue_depth(m["agent_id"])
        max_d = m.get("max_queue_depth") or 1
        headroom = _q.quota_headroom_summary(m["agent_id"], project_id=project_id)
        lines.append(
            f"- **{m['name']}** (id={m['agent_id']}) — {m.get('role_title') or ''}"
            f"\n    {m.get('description') or ''}"
            f"\n    queue {depth}/{max_d} · project slice {int(m['daily_alloc_pct'])}%"
            f" · {headroom}"
        )
    roster = "\n".join(lines)

    ctx_lines = [
        f"## Project context: **{p['name']}** (status: {p['status']})",
    ]
    if p.get("goal"):
        ctx_lines.append(f"Goal: {p['goal']}")
    if p.get("description"):
        ctx_lines.append(f"Description: {p['description']}")
    if milestones:
        ctx_lines.append("Milestones:")
        for ms in milestones:
            mark = {"done": "✅", "in_progress": "⏳", "pending": "◽"}.get(ms["status"], "◽")
            ctx_lines.append(f"  - {mark} {ms['title']}")
    ctx_lines.append(
        "\nOnly use the agents listed in the roster. Every step you schedule "
        "charges the project's daily slice of that agent's cap — stay under "
        "each slice. If an agent is already at/near its limit, either skip "
        "it or propose a reduced plan."
    )
    return roster, "\n".join(ctx_lines)


def _get_or_create_thread(user_id: int, thread_id: str | None = None) -> str:
    """Find an existing thread or create a new one. Returns the thread_id (UUID-ish string)."""
    if thread_id:
        row = db.fetch_one(
            "SELECT thread_id FROM lead_conversations WHERE user_id = %s AND thread_id = %s",
            (user_id, thread_id),
        )
        if row:
            return thread_id

    new_id = uuid.uuid4().hex[:16]
    db.execute(
        """
        INSERT INTO lead_conversations (user_id, thread_id, status)
        VALUES (%s, %s, 'active')
        """,
        (user_id, new_id),
    )
    return new_id


def _load_thread_history(thread_id: str, max_messages: int = 20) -> list[dict]:
    rows = db.fetch_all(
        """
        SELECT role, content FROM lead_messages
        WHERE thread_id = %s AND cancelled = FALSE
        ORDER BY created_at DESC
        LIMIT %s
        """,
        (thread_id, max_messages),
    )
    return list(reversed(rows))


# ============================================================================
# Main chat entry point
# ============================================================================

def chat(user_id: int, user_message: str, thread_id: str | None = None,
         project_id: int | None = None) -> dict:
    """Send a message to the Lead agent. Returns:
        {
          "thread_id": str,
          "response": str,
          "proposed_workflow": dict | None,
        }

    If `project_id` is given, Lead is told about the project goal + which
    agents are members + each member's remaining daily allocation, so it
    keeps proposed workflows within the project's cap.
    """
    thread_id = _get_or_create_thread(user_id, thread_id)

    # Save user message
    db.execute(
        "INSERT INTO lead_messages (thread_id, role, content) VALUES (%s, 'user', %s)",
        (thread_id, user_message),
    )

    # Build context
    user = db.fetch_one(
        "SELECT username, display_name, lead_max_steps, lead_max_tokens FROM as_users WHERE id = %s",
        (user_id,),
    )
    user_name = (user or {}).get("display_name") or (user or {}).get("username") or "user"
    max_steps = (user or {}).get("lead_max_steps") or 10
    max_tokens = (user or {}).get("lead_max_tokens") or 50000

    # Build the team roster. When a project is in scope, restrict the
    # roster to that project's members and include each one's remaining
    # allocation so Lead can plan within budget.
    if project_id:
        roster_text, project_ctx = _build_project_context(user_id, project_id)
    else:
        roster_text = _build_team_roster(user_id)
        project_ctx = ""

    system_prompt = LEAD_SYSTEM_PROMPT.format(
        user_name=user_name,
        team_roster=roster_text,
    )

    # Append workflow planning constraints
    system_prompt += f"""

## Workflow planning constraints

- **Max steps (max_steps)**: {max_steps} — the total node count in any workflow you design (including members when a group is expanded) cannot exceed this.
  If a task is too complex to fit: (1) try to merge or prune steps first; (2) if it still can't fit, tell the user plainly: "The current step cap is {max_steps} and this task looks like it needs N steps. You can raise the cap in settings, or simplify the request."
- **Max token budget (max_tokens)**: {max_tokens:,} — the estimated total token usage across the entire workflow run cannot exceed this.
  Each agent node runs about 3,000–5,000 tokens. Do rough mental math when designing.
  If the estimate will exceed, tell the user and suggest a leaner plan.
"""
    if project_ctx:
        system_prompt += "\n" + project_ctx

    # Load thread history
    history = _load_thread_history(thread_id, max_messages=20)
    history_text = "\n".join(
        f"[{m['role']}] {m['content']}" for m in history[:-1]  # exclude just-added user msg
    )
    prompt = user_message if not history_text else f"{history_text}\n\n[user] {user_message}"

    # Pick the acting agent:
    #   - project chat → the project's coordinator (if set)
    #   - otherwise → the user's Lead (is_lead=TRUE)
    # This lets the project's actual coordinator voice the reply, not a
    # generic Lead pretending to be the coordinator (the old behaviour).
    # If the project has no coordinator assigned, we fall through to Lead.
    acting_agent = None
    if project_id:
        row = db.fetch_one(
            """
            SELECT a.id, a.primary_model_id
            FROM projects p
            JOIN agents a ON a.id = p.coordinator_agent_id
            WHERE p.id = %s AND p.user_id = %s
            """,
            (project_id, user_id),
        )
        if row:
            acting_agent = row
    if not acting_agent:
        acting_agent = db.fetch_one(
            "SELECT id, primary_model_id FROM agents WHERE user_id = %s AND is_lead = TRUE LIMIT 1",
            (user_id,),
        )
    lead_agent_id = (acting_agent or {}).get("id")
    model = (acting_agent or {}).get("primary_model_id") or None

    # Invoke LLM via the agent's assigned model client. We lift max_tokens
    # well above the 4K default because Lead increasingly outputs long
    # artifacts inline (HTML prototypes, slide decks) — a 4K cap truncates
    # those mid-fence and the parser then finds no artifacts.
    result = llm_invoke(
        agent_id=lead_agent_id,
        model_key=model,
        system_prompt=system_prompt,
        user_text=prompt,
        max_tokens=32_000,
    )
    response_text = result.get("text", "")

    # Parse workflow proposal from response (if any)
    proposed = _extract_workflow_proposal(response_text)
    proposed_hire = _extract_hire_proposal(response_text)
    proposed_project = _extract_project_proposal(response_text)
    artifacts = _extract_artifacts(response_text)

    # If a workflow is proposed, persist as draft
    proposed_workflow_id = None
    if proposed:
        proposed_workflow_id = _persist_draft_workflow(user_id, proposed)

    # Save lead's response — hire/project proposals ride in metadata (no
    # extra tables; the dialog UI pulls them out and renders cards).
    msg_metadata = {
        "tokens": result.get("input_tokens", 0) + result.get("output_tokens", 0),
        "cost_usd": float(result.get("cost_usd", 0)),
        "model": result.get("model_id"),
    }
    if proposed_hire:
        msg_metadata["proposed_hire"] = proposed_hire
    if proposed_project:
        msg_metadata["proposed_project"] = proposed_project
    if artifacts:
        msg_metadata["artifacts"] = artifacts
    message_id = db.execute_returning(
        """
        INSERT INTO lead_messages
            (thread_id, role, content, proposed_workflow_id, metadata)
        VALUES (%s, 'lead', %s, %s, %s::jsonb)
        RETURNING id
        """,
        (thread_id, response_text, proposed_workflow_id, json.dumps(msg_metadata)),
    )

    # Project-scoped chats (project_id given) persist each emitted artifact
    # to project_artifacts so the project detail page can surface them
    # without having to walk every lead_message. Safe to skip silently on
    # failure — the artifact still lives on the lead_message metadata.
    if project_id and artifacts:
        for a in artifacts:
            try:
                db.execute(
                    """
                    INSERT INTO project_artifacts
                        (project_id, agent_id, source, source_ref,
                         kind, title, payload)
                    VALUES (%s, %s, 'lead_message', %s, %s, %s, %s::jsonb)
                    """,
                    (project_id, lead_agent_id, int(message_id),
                     a.get("kind"),
                     (a.get("title") or a.get("filename") or None),
                     json.dumps(a)),
                )
            except Exception:
                pass

    # Update thread activity
    db.execute(
        "UPDATE lead_conversations SET updated_at = NOW() WHERE thread_id = %s",
        (thread_id,),
    )

    return {
        "thread_id": thread_id,
        "response": response_text,
        "proposed_workflow": proposed,
        "proposed_workflow_id": proposed_workflow_id,
        "proposed_hire": proposed_hire,
        "proposed_hire_message_id": int(message_id) if proposed_hire else None,
        "proposed_project": proposed_project,
        "proposed_project_message_id": int(message_id) if proposed_project else None,
        "artifacts": artifacts,
        "cost_usd": float(result.get("cost_usd", 0)),
        "tokens": result.get("input_tokens", 0) + result.get("output_tokens", 0),
    }


# ============================================================================
# Workflow proposal parsing + persistence
# ============================================================================

WORKFLOW_BLOCK_RE = re.compile(r"```workflow\s*\n(.*?)\n```", re.DOTALL)
HIRE_BLOCK_RE = re.compile(r"```hire\s*\n(.*?)\n```", re.DOTALL)
PROJECT_BLOCK_RE = re.compile(r"```project\s*\n(.*?)\n```", re.DOTALL)
# Artifact blocks — three kinds rendered as dedicated bubbles in the UI.
# Each has its own fence tag so the regex + the UI dispatcher stay simple.
ARTIFACT_HTML_RE     = re.compile(r"```artifact-html(?:\s+([^\n]+))?\s*\n(.*?)\n```",     re.DOTALL)
ARTIFACT_SLIDES_RE   = re.compile(r"```artifact-slides(?:\s+([^\n]+))?\s*\n(.*?)\n```",   re.DOTALL)
ARTIFACT_FILE_RE     = re.compile(r"```artifact-file\s*\n(.*?)\n```",                     re.DOTALL)
ARTIFACT_MARKDOWN_RE = re.compile(r"```artifact-markdown(?:\s+([^\n]+))?\s*\n(.*?)\n```", re.DOTALL)
# Compound regex to strip every artifact block from the displayed prose.
ARTIFACT_ANY_RE = re.compile(
    r"```artifact-(?:html|slides|file|markdown)(?:\s+[^\n]+)?\s*\n.*?\n```", re.DOTALL,
)


def _extract_workflow_proposal(text: str) -> dict | None:
    """Find a ```workflow ...``` block in the response and parse as JSON."""
    m = WORKFLOW_BLOCK_RE.search(text)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        return None


_HIRE_REQUIRED = ("name", "role_title", "system_prompt")

# Per-artifact size cap (characters in the raw payload). Keeps one runaway
# LLM response from blowing up the DB row. 200KB is roughly 50K tokens.
_ARTIFACT_MAX_CHARS = 200_000


def _extract_artifacts(text: str) -> list[dict]:
    """Pull every ```artifact-{html,slides,file}``` block out of the LLM
    response. Returns a list of dicts the UI can dispatch on `kind`:

      {"kind": "html",   "title": str,     "html": str}
      {"kind": "slides", "title": str,     "html": str}
      {"kind": "file",   "filename": str,  "mime": str,
                         "content": str,   "encoding": "utf-8" | "base64"}

    Files are a JSON body so the agent can emit metadata + content atomically;
    html / slides are raw HTML in the fence for readability + token efficiency.
    Blocks over `_ARTIFACT_MAX_CHARS` are truncated with a header comment so
    an obvious signal shows in the UI rather than a silent drop.
    """
    out: list[dict] = []

    def _clip(s: str) -> str:
        if len(s) <= _ARTIFACT_MAX_CHARS:
            return s
        return s[:_ARTIFACT_MAX_CHARS] + (
            f"\n<!-- artifact truncated at {_ARTIFACT_MAX_CHARS} chars -->"
        )

    for m in ARTIFACT_HTML_RE.finditer(text):
        title = (m.group(1) or "").strip() or "HTML prototype"
        out.append({"kind": "html", "title": title[:200], "html": _clip(m.group(2))})

    for m in ARTIFACT_SLIDES_RE.finditer(text):
        title = (m.group(1) or "").strip() or "Slide deck"
        out.append({"kind": "slides", "title": title[:200], "html": _clip(m.group(2))})

    for m in ARTIFACT_MARKDOWN_RE.finditer(text):
        title = (m.group(1) or "").strip() or "Markdown document"
        out.append({
            "kind": "markdown",
            "title": title[:200],
            "markdown": _clip(m.group(2)),
        })

    for m in ARTIFACT_FILE_RE.finditer(text):
        try:
            payload = json.loads(m.group(1))
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        filename = str(payload.get("filename") or "").strip()
        content = payload.get("content")
        if not filename or content is None:
            continue
        out.append({
            "kind": "file",
            "filename": filename[:200],
            "mime": str(payload.get("mime") or "application/octet-stream")[:100],
            "encoding": "base64" if str(payload.get("encoding") or "") == "base64" else "utf-8",
            "content": _clip(str(content)),
        })

    return out


def _extract_project_proposal(text: str) -> dict | None:
    """Find a ```project``` block and return a dict suitable for the
    POST /api/projects body. Requires at least a name + goal."""
    m = PROJECT_BLOCK_RE.search(text)
    if not m:
        return None
    try:
        data = json.loads(m.group(1))
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    name = str(data.get("name") or "").strip()
    goal = str(data.get("goal") or "").strip()
    if not name or not goal:
        return None
    # Normalize member ids to a list of ints.
    raw_members = data.get("member_agent_ids") or []
    members: list[int] = []
    for v in raw_members:
        try:
            members.append(int(v))
        except Exception:
            continue
    coord = data.get("coordinator_agent_id")
    try:
        coord_id = int(coord) if coord is not None else None
    except Exception:
        coord_id = None
    return {
        "name": name[:200],
        "goal": goal[:500],
        "description": str(data.get("description") or "").strip()[:1000],
        "member_agent_ids": members,
        "coordinator_agent_id": coord_id,
        "rationale": str(data.get("rationale") or "").strip()[:500],
    }


def _extract_hire_proposal(text: str) -> dict | None:
    """Find a ```hire ...``` block and parse as JSON. Returns a proposal
    dict with at least {name, role_title, system_prompt} or None.

    Lead is instructed to fill every field — missing values are treated as
    a malformed proposal and ignored (the user will see the surrounding
    prose and can ask Lead to retry)."""
    m = HIRE_BLOCK_RE.search(text)
    if not m:
        return None
    try:
        data = json.loads(m.group(1))
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    if not all(str(data.get(k) or "").strip() for k in _HIRE_REQUIRED):
        return None
    # Sanity-clip lengths — Lead occasionally emits bloated prompts.
    return {
        "name": str(data["name"]).strip()[:64],
        "role_title": str(data["role_title"]).strip()[:128],
        "description": str(data.get("description") or "").strip()[:500],
        "system_prompt": str(data["system_prompt"]).strip()[:4000],
        "rationale": str(data.get("rationale") or "").strip()[:500],
    }


def _persist_draft_workflow(user_id: int, proposed: dict) -> int | None:
    """Save Lead's proposed workflow as a draft. User can later save/run/edit.

    Handles two node shapes:
    - `{"type": "agent", "agent_id": N, ...}`  — simple sequential node
    - `{"type": "group", "group": {...}, ...}` — Lead proposes a new group
      inline; we create the groups_tbl row first then point the node at it

    Ignores any pos_x/pos_y Lead provides (it's historically bad at them)
    and lays nodes out in a clean horizontal row based on position index.
    """
    try:
        wf_id = db.execute_returning(
            """
            INSERT INTO workflows
                (user_id, name, description, loop_enabled, max_loops, source, is_draft)
            VALUES (%s, %s, %s, %s, %s, 'lead_generated', TRUE)
            RETURNING id
            """,
            (
                user_id,
                proposed.get("name") or "Lead-proposed workflow",
                proposed.get("description"),
                bool(proposed.get("loop_enabled")),
                int(proposed.get("max_loops", 1)),
            ),
        )
        # Sort so position indices match insertion order for tidy layout
        nodes = sorted(
            proposed.get("nodes") or [],
            key=lambda n: int(n.get("position", 0)),
        )
        for idx, node in enumerate(nodes):
            node_type = node.get("type") or "agent"
            agent_id = node.get("agent_id")
            group_id = node.get("group_id")

            # Inline group — create groups_tbl row + members on the fly
            if node_type == "group" and not group_id and node.get("group"):
                g = node["group"]
                mode = g.get("mode") if g.get("mode") in ("parallel", "sequential") else "parallel"
                group_id = db.execute_returning(
                    """
                    INSERT INTO groups_tbl
                        (user_id, name, description, mode, aggregator_agent_id, is_ephemeral)
                    VALUES (%s, %s, %s, %s, %s, TRUE) RETURNING id
                    """,
                    (
                        user_id,
                        g.get("name") or f"Group {idx}",
                        g.get("description"),
                        mode,
                        g.get("aggregator_agent_id"),
                    ),
                )

                # Two ways Lead may specify members:
                # (a) "members": [{"agent_id": N, "custom_prompt": "..."}, ...]
                #     — used when each member needs a different sub-task
                # (b) "member_agent_ids": [N, M, ...]
                #     — used when all members run the same prompt
                members_spec: list[dict] = []
                if g.get("members"):
                    for m in g["members"]:
                        if not isinstance(m, dict):
                            continue
                        members_spec.append({
                            "agent_id": m.get("agent_id"),
                            "custom_prompt": m.get("custom_prompt"),
                        })
                else:
                    for aid in g.get("member_agent_ids") or []:
                        members_spec.append({"agent_id": aid, "custom_prompt": None})

                for mpos, m in enumerate(members_spec):
                    if not m.get("agent_id"):
                        continue
                    try:
                        db.execute(
                            """
                            INSERT INTO group_members (group_id, agent_id, position, custom_prompt)
                            VALUES (%s, %s, %s, %s)
                            """,
                            (group_id, int(m["agent_id"]), mpos, m.get("custom_prompt")),
                        )
                    except Exception:
                        pass

            # Auto-layout: spread horizontally by position, staggered y
            pos_x = 120 + idx * 320
            pos_y = 200

            db.execute(
                """
                INSERT INTO workflow_nodes
                    (workflow_id, position, node_type, agent_id, group_id,
                     label, prompt_template, system_prompt_override, pos_x, pos_y)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    wf_id,
                    idx,
                    node_type,
                    agent_id,
                    group_id,
                    node.get("label"),
                    node.get("prompt_template"),
                    node.get("system_prompt_override"),
                    pos_x,
                    pos_y,
                ),
            )
        return wf_id
    except Exception as e:
        import logging
        logging.getLogger("agent_company.lead_agent").exception(
            "failed to persist draft workflow: %s", e,
        )
        return None


# ============================================================================
# Thread management
# ============================================================================

def list_threads(user_id: int) -> list[dict]:
    # Exclude project-scoped coordinator threads (id prefix "proj-<pid>-")
    # — those live in the Project Detail page, not the main Lead dialog.
    return db.fetch_all(
        """
        SELECT c.thread_id, c.title, c.status, c.updated_at,
               (SELECT COUNT(*) FROM lead_messages m WHERE m.thread_id = c.thread_id) AS msg_count
        FROM lead_conversations c
        WHERE c.user_id = %s AND c.status = 'active' AND c.agent_id IS NULL
          AND c.thread_id NOT LIKE 'proj-%%'
        ORDER BY c.updated_at DESC
        LIMIT 50
        """,
        (user_id,),
    )


def get_thread_messages(
    user_id: int,
    thread_id: str,
    limit: int = 20,
    before_id: int | None = None,
) -> dict:
    """Cursor-paginated thread history.

    Returns `{messages, has_more}` where `messages` is an ascending-by-id slice
    of at most `limit` rows. When `before_id` is provided, only rows with
    `id < before_id` are returned — this is how the UI walks backwards through
    history as the user scrolls to the top of the message list.
    """
    owner = db.fetch_one(
        "SELECT user_id FROM lead_conversations WHERE thread_id = %s",
        (thread_id,),
    )
    if not owner or owner["user_id"] != user_id:
        return {"messages": [], "has_more": False}

    limit = max(1, min(int(limit or 20), 100))
    if before_id is not None:
        rows = db.fetch_all(
            """
            SELECT id, role, content, proposed_workflow_id, metadata, cancelled, created_at
            FROM lead_messages
            WHERE thread_id = %s AND id < %s
            ORDER BY id DESC
            LIMIT %s
            """,
            (thread_id, before_id, limit + 1),
        )
    else:
        rows = db.fetch_all(
            """
            SELECT id, role, content, proposed_workflow_id, metadata, cancelled, created_at
            FROM lead_messages
            WHERE thread_id = %s
            ORDER BY id DESC
            LIMIT %s
            """,
            (thread_id, limit + 1),
        )
    has_more = len(rows) > limit
    if has_more:
        rows = rows[:limit]
    rows.reverse()
    return {"messages": rows, "has_more": has_more}


def archive_thread(user_id: int, thread_id: str) -> None:
    db.execute(
        """
        UPDATE lead_conversations SET status = 'archived'
        WHERE user_id = %s AND thread_id = %s
        """,
        (user_id, thread_id),
    )


# ============================================================================
# Direct agent chat — reuses lead_conversations/lead_messages infra via
# the nullable agent_id column. agent_id IS NULL means Lead; otherwise it's
# a direct chat with that specific agent.
# ============================================================================

def _get_or_create_agent_thread(user_id: int, agent_id: int, thread_id: str | None = None) -> str:
    if thread_id:
        row = db.fetch_one(
            """
            SELECT thread_id FROM lead_conversations
            WHERE user_id = %s AND thread_id = %s AND agent_id = %s
            """,
            (user_id, thread_id, agent_id),
        )
        if row:
            return thread_id

    new_id = uuid.uuid4().hex[:16]
    db.execute(
        """
        INSERT INTO lead_conversations (user_id, thread_id, agent_id, status)
        VALUES (%s, %s, %s, 'active')
        """,
        (user_id, new_id, agent_id),
    )
    return new_id


def chat_with_agent(
    user_id: int,
    agent_id: int,
    user_message: str,
    thread_id: str | None = None,
) -> dict:
    """Send a message to any non-Lead agent directly and return its reply.

    Runs synchronously (like the Lead chat) rather than going through the
    background worker queue. Suitable for interactive chat UX. The caller
    (user_id) must have access rights to this agent — owner OR explicit
    share OR visibility rule. Delegates ownership check to
    `sharing.user_can_access_agent`.
    """
    from . import sharing
    if not sharing.user_can_access_agent(user_id, agent_id):
        raise ValueError(f"agent {agent_id} not accessible")
    agent = db.fetch_one("SELECT * FROM agents WHERE id = %s", (agent_id,))
    if not agent:
        raise ValueError(f"agent {agent_id} not found")
    if agent.get("is_lead") and agent.get("user_id") == user_id:
        # For the user's own Lead, delegate to the regular Lead chat path.
        return chat(user_id, user_message, thread_id=thread_id)

    thread_id = _get_or_create_agent_thread(user_id, agent_id, thread_id)

    db.execute(
        "INSERT INTO lead_messages (thread_id, role, content) VALUES (%s, 'user', %s)",
        (thread_id, user_message),
    )

    system_prompt = (agent.get("system_prompt") or "").strip()
    if not system_prompt:
        system_prompt = (
            f"You are {agent['name']}. "
            f"{agent.get('role_title') or ''}. "
            f"{agent.get('description') or ''}"
        ).strip()

    # Load existing thread history (exclude the message we just inserted)
    history = _load_thread_history(thread_id, max_messages=20)
    history_text = "\n".join(
        f"[{m['role']}] {m['content']}" for m in history[:-1]
    )
    prompt = user_message if not history_text else f"{history_text}\n\n[user] {user_message}"

    model = agent.get("primary_model_id") or None
    result = llm_invoke(
        agent_id=agent["id"],
        model_key=model,
        system_prompt=system_prompt,
        user_text=prompt,
    )
    response_text = result.get("text", "")

    db.execute(
        """
        INSERT INTO lead_messages
            (thread_id, role, content, metadata)
        VALUES (%s, 'lead', %s, %s::jsonb)
        """,
        (thread_id, response_text, json.dumps({
            "tokens": result.get("input_tokens", 0) + result.get("output_tokens", 0),
            "cost_usd": float(result.get("cost_usd", 0)),
            "model": result.get("model_id"),
            "agent_id": agent_id,
        })),
    )
    db.execute(
        "UPDATE lead_conversations SET updated_at = NOW() WHERE thread_id = %s",
        (thread_id,),
    )

    return {
        "thread_id": thread_id,
        "response": response_text,
        "proposed_workflow": None,
        "proposed_workflow_id": None,
        "cost_usd": float(result.get("cost_usd", 0)),
        "tokens": result.get("input_tokens", 0) + result.get("output_tokens", 0),
    }


def lead_pending_count(user_id: int) -> int:
    """Count Lead conversations where the latest message is from Lead and
    the user hasn't replied yet — i.e., Lead is "looking for" the user.

    Used by the Dialog Center to surface a pending-message indicator on
    the Lead cast member.
    """
    row = db.fetch_one(
        """
        SELECT COUNT(*) AS n
        FROM lead_conversations c
        WHERE c.user_id = %s
          AND c.status = 'active'
          AND c.agent_id IS NULL
          AND c.thread_id NOT LIKE 'proj-%%'
          AND EXISTS (
              SELECT 1 FROM lead_messages m
              WHERE m.thread_id = c.thread_id
                AND m.role = 'lead'
                AND m.created_at > COALESCE(
                    (SELECT MAX(created_at) FROM lead_messages
                     WHERE thread_id = c.thread_id AND role = 'user'),
                    '1970-01-01'::timestamptz
                )
          )
        """,
        (user_id,),
    )
    return int((row or {}).get("n") or 0)


def list_agent_threads(user_id: int, agent_id: int) -> list[dict]:
    return db.fetch_all(
        """
        SELECT c.thread_id, c.title, c.status, c.updated_at,
               (SELECT COUNT(*) FROM lead_messages m WHERE m.thread_id = c.thread_id) AS msg_count
        FROM lead_conversations c
        WHERE c.user_id = %s AND c.agent_id = %s AND c.status = 'active'
        ORDER BY c.updated_at DESC
        LIMIT 50
        """,
        (user_id, agent_id),
    )


def hot_stop_message(thread_id: str, message_id: int) -> None:
    """Mark a Lead message as cancelled (used during streaming stop)."""
    db.execute(
        "UPDATE lead_messages SET cancelled = TRUE WHERE id = %s AND thread_id = %s",
        (message_id, thread_id),
    )


# ============================================================================
# Hire proposal acceptance
# ============================================================================

def accept_hire_proposal(user_id: int, message_id: int,
                          overrides: dict | None = None) -> dict:
    """Materialise a Lead-proposed hire as a real agent.

    The proposal lives in `lead_messages.metadata.proposed_hire`. We read
    it, apply any admin tweaks (e.g. user renamed it in the bubble), and
    create the agent via the same path as the manual create flow. Adds a
    follow-up Lead message ("Hired Maya — available for assignment") so
    the dialog thread tells a coherent story.

    Returns the new agent row's minimal shape.
    """
    row = db.fetch_one(
        """
        SELECT m.thread_id, m.metadata, c.user_id AS thread_owner
        FROM lead_messages m
        JOIN lead_conversations c ON c.thread_id = m.thread_id
        WHERE m.id = %s
        """,
        (message_id,),
    )
    if not row or row.get("thread_owner") != user_id:
        raise ValueError("proposal not found")

    meta = row.get("metadata") or {}
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except Exception:
            meta = {}
    proposal = (meta or {}).get("proposed_hire") or {}
    if not proposal:
        raise ValueError("message carries no hire proposal")

    # Merge in admin's edits from the Hire dialog (name / role / prompt
    # tweaks before they click Confirm).
    effective = {**proposal, **(overrides or {})}

    import random as _random
    avatar_bodies = ["ArmsCrossed", "BlazerBlackTee", "ButtonShirt", "Coffee",
                      "DotJacket", "Explaining", "Geek", "Hoodie", "Shirt",
                      "ShirtCoat", "SportyShirt", "Sweater", "Whatever"]
    avatar_hairs = ["Bangs", "Bun", "Long", "LongBangs", "Medium", "MediumBangs",
                     "Short", "ShortCurly", "ShortMessy", "ShortWavy"]
    avatar_faces = ["Awe", "Calm", "Cheeky", "Cute", "Driven", "Explaining",
                     "Serious", "Smile", "SmileBig", "SmileNM"]
    avatar_config = {
        "body": _random.choice(avatar_bodies),
        "hair": _random.choice(avatar_hairs),
        "face": _random.choice(avatar_faces),
    }

    # Pick the first model_client the user can use (same default rule as
    # manual create_agent in backend/app.py).
    from . import model_clients
    clients = model_clients.list_for_user(user_id)
    default_client = next(
        (c for c in clients if c.get("default_for_new_users")),
        clients[0] if clients else None,
    )
    model_client_id = default_client["id"] if default_client else None
    primary_model_id = ""
    if default_client:
        raw = model_clients.get_raw(int(default_client["id"]))
        models = ((raw or {}).get("config") or {}).get("models") or []
        if models:
            primary_model_id = models[0].get("id") or ""
    if not primary_model_id:
        primary_model_id = "jp.anthropic.claude-sonnet-4-6"

    new_id = db.execute_returning(
        """
        INSERT INTO agents (user_id, owner_user_id, name, role_title, description,
                            system_prompt, primary_model_id, avatar_config,
                            model_client_id)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s)
        RETURNING id
        """,
        (
            user_id, user_id,
            effective["name"], effective["role_title"],
            effective.get("description") or None,
            effective["system_prompt"],
            primary_model_id,
            json.dumps(avatar_config),
            model_client_id,
        ),
    )
    # Start a worker so the new agent picks up tasks immediately.
    try:
        from .. import worker
        worker.registry().start_agent(int(new_id))
    except Exception as e:
        log.warning("started agent but couldn't spawn worker: %s", e)

    # Bake the accepted id into the original proposal message so replaying
    # the thread later shows "hired" state (dialog UI reads this).
    meta["hired_agent_id"] = int(new_id)
    db.execute(
        "UPDATE lead_messages SET metadata = %s::jsonb WHERE id = %s",
        (json.dumps(meta), message_id),
    )
    # Append a system line to the thread so the user can see confirmation
    # without having to refresh.
    db.execute(
        """INSERT INTO lead_messages (thread_id, role, content, metadata)
           VALUES (%s, 'system', %s, %s::jsonb)""",
        (
            row["thread_id"],
            f"✓ Hired **{effective['name']}** — {effective['role_title']}. "
            f"Agent is active and ready for assignment.",
            json.dumps({"hired_agent_id": int(new_id)}),
        ),
    )
    db.execute(
        "UPDATE lead_conversations SET updated_at = NOW() WHERE thread_id = %s",
        (row["thread_id"],),
    )
    return {"agent_id": int(new_id), "name": effective["name"],
             "role_title": effective["role_title"]}


def accept_project_proposal(user_id: int, message_id: int,
                              overrides: dict | None = None) -> dict:
    """Materialise a Lead-proposed project. Creates the project row,
    attaches members (default 100% allocation). Admin overrides can
    rename, adjust members, or change the coordinator before accepting."""
    row = db.fetch_one(
        """
        SELECT m.thread_id, m.metadata, c.user_id AS thread_owner
        FROM lead_messages m
        JOIN lead_conversations c ON c.thread_id = m.thread_id
        WHERE m.id = %s
        """,
        (message_id,),
    )
    if not row or row.get("thread_owner") != user_id:
        raise ValueError("proposal not found")

    meta = row.get("metadata") or {}
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except Exception:
            meta = {}
    proposal = (meta or {}).get("proposed_project") or {}
    if not proposal:
        raise ValueError("message carries no project proposal")

    effective = {**proposal, **(overrides or {})}

    pid = db.execute_returning(
        """
        INSERT INTO projects (user_id, name, description, goal, status, coordinator_agent_id)
        VALUES (%s, %s, %s, %s, 'active', %s) RETURNING id
        """,
        (
            user_id,
            effective["name"],
            effective.get("description") or None,
            effective["goal"],
            effective.get("coordinator_agent_id"),
        ),
    )
    # Attach members — 100% allocation by default; user can fine-tune from
    # the Project detail page.
    # project_members has no unique key on (project, agent); since the
    # project is freshly created here, simple inserts are safe.
    seen: set[int] = set()
    for aid in effective.get("member_agent_ids") or []:
        aid_i = int(aid)
        if aid_i in seen:
            continue
        seen.add(aid_i)
        db.execute(
            """INSERT INTO project_members (project_id, agent_id, daily_alloc_pct, monthly_alloc_pct)
               VALUES (%s, %s, %s, %s)""",
            (pid, aid_i, 100.0, 100.0),
        )
    # Coordinator must be a project member — quota middleware rejects any
    # agent that isn't in project_members when a dispatch carries project_id.
    coord_id = effective.get("coordinator_agent_id")
    if coord_id and int(coord_id) not in seen:
        db.execute(
            """INSERT INTO project_members (project_id, agent_id, daily_alloc_pct, monthly_alloc_pct)
               VALUES (%s, %s, 100.0, 100.0)""",
            (pid, int(coord_id)),
        )

    meta["created_project_id"] = int(pid)
    db.execute(
        "UPDATE lead_messages SET metadata = %s::jsonb WHERE id = %s",
        (json.dumps(meta), message_id),
    )
    db.execute(
        """INSERT INTO lead_messages (thread_id, role, content, metadata)
           VALUES (%s, 'system', %s, %s::jsonb)""",
        (
            row["thread_id"],
            f"✓ Opened project **{effective['name']}** "
            f"with {len(effective.get('member_agent_ids') or [])} members. "
            f"All subsequent workflow runs in this thread can be scoped "
            f"to it by including `project_id={pid}` on dispatch.",
            json.dumps({"created_project_id": int(pid)}),
        ),
    )
    db.execute(
        "UPDATE lead_conversations SET updated_at = NOW() WHERE thread_id = %s",
        (row["thread_id"],),
    )
    return {
        "project_id": int(pid),
        "name": effective["name"],
        "member_count": len(effective.get("member_agent_ids") or []),
    }


# ============================================================================
# Conflict detection (called by Lead before proposing)
# ============================================================================

def detect_conflicts(user_id: int, agent_ids: list[int]) -> list[dict]:
    """Check each agent for queue / quota / off_duty issues.
    Returns a list of conflict descriptors that Lead can mention in its response.
    """
    conflicts = []
    for aid in agent_ids:
        a = db.fetch_one("SELECT * FROM agents WHERE id = %s AND user_id = %s", (aid, user_id))
        if not a:
            continue
        depth = queue.queue_depth(aid)
        if depth >= int(a["max_queue_depth"]) * 0.8:
            conflicts.append({
                "type": "queue_near_full",
                "agent_id": aid,
                "agent_name": a["name"],
                "detail": f"queued {depth}/{a['max_queue_depth']}",
            })
        if a["status"] == "off_duty":
            conflicts.append({
                "type": "off_duty",
                "agent_id": aid,
                "agent_name": a["name"],
                "detail": "off duty",
            })
        if a["status"] == "quota_exceeded":
            conflicts.append({
                "type": "budget_exceeded",
                "agent_id": aid,
                "agent_name": a["name"],
                "detail": "budget exceeded",
            })
    return conflicts
