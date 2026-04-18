# Projects — Holons' management layer

A **project** in Holons wraps a long-lived goal, the team chasing it, and a
budget. Think of it as a cell in your AI company: one coordinator, a few
members, a pot of tokens to spend.

## What a project is for

- Keep a multi-day initiative coherent. "Write the pilot script", "prep
  the Series A raise", "run Q3 content calendar".
- Give a small team of agents its own **budget** — a slice of each
  member's daily/monthly cap dedicated to *this* project.
- Have a **coordinator** (one of the agents) who plans tasks within the
  team, respects the budget, and files a daily update.
- Collect every output, every run, every cost into one page you can open
  weeks later and know what happened.

## The moving parts

| | |
|---|---|
| **Members** | Agents assigned to the project. Each has a `daily_alloc_pct` and `monthly_alloc_pct` that slices their global quota. |
| **Coordinator** | One of the members, designated as the project's Lead. Plans workflows, respects allocation, and writes daily summaries. |
| **Milestones** | Ordered phases of work with status (`pending / in_progress / done`). Coordinator sees them in every chat. |
| **Daily reports** | Coordinator-authored markdown summaries, one per day, posted into your Lead inbox and (optionally) webhook. |
| **Outputs** | Every run's `final_output` rolled up in one place, with a download button. |
| **Activity** | Timeline of project mutations — status flips, member changes, milestone ticks. |

## Walkthrough

### 1. Create a project

Sidebar → **Projects** → **+ New project**.

Required: a name and at least one member. Optional but recommended:
- **Goal** — one sentence describing what "done" means. Coordinator
  sees this every time it plans.
- **Coordinator** — one of the members. Without a coordinator, you can't
  use the in-project chat (you'd just be talking to plain Lead).
- **Daily allocation %** per member — sliders. 100% = full quota. 30% =
  this project may spend at most 30% of that agent's daily cost/token cap.

### 2. Add milestones

Project Detail → **Milestones** → **+ Add milestone**. Click the status
glyph to cycle `pending → in_progress → done`. The coordinator's prompt
reads the milestone list on every turn.

### 3. Chat with the coordinator

Project Detail → **Chat with …**. Tell it what the team should do next.
The coordinator:
- Sees only this project's agents in its roster.
- Knows each member's remaining allocation for today.
- Produces a runnable workflow proposal attached to this project — when
  you click *Run Now*, every step is budget-gated.

### 4. Watch the dashboard

- **Daily usage by member (14 days)** on the project page — stack bar
  chart grouped by agent.
- **Dashboard → Agents near quota** — agents ≥ 80% of their daily cap,
  colored yellow (warning) / red (capped).
- **Dashboard → Usage by project / agent / team** — three stacked
  charts for a 14-day view.

### 5. Read the daily report

Once the project sees activity, the scheduler fires
`project_reports.generate()` every ~30 min for any project missing
today's report. The coordinator writes a short markdown summary covering
progress, per-member contributions, budget burn, and next up. You get it:

- In your Lead inbox (with a link back to the project).
- As a bell notification.
- As a POST to your configured webhook (`Settings → Webhook URL`).
- In the **Daily reports** panel on the project page.

### 6. Collect outputs

The **Outputs** panel on the project page lists every run that produced a
`final_output`, newest first. Each has a download button for the raw
markdown.

### 7. Pause / mark done

Header has **Pause / Resume / Mark done**. A paused project stops all
further step dispatch (`can_run` rejects with "project paused"). A done
project is read-only; activity feed is preserved.

## Review loops

When the coordinator plans a workflow, it can insert `node_type: "review"`
nodes that inspect a previous step and reply `APPROVE` or
`REVISE: <feedback>`. On `REVISE`, the engine re-enqueues the previous
step with the feedback as `prev_output`. Capped by
`workflows.max_review_iterations` (default 2) per position so loops
can't run forever.

This lets you build patterns like: "writer drafts → coordinator reviews
→ writer revises → coordinator approves → editor polishes".

## Budgets & auto top-up

Hard caps live on the agent row (`daily_cost_quota`, `daily_token_quota`,
`monthly_cost_quota`, `monthly_token_quota`). Set them in
**Agents → (pick) → Budget**.

The project's allocation % slices those hard caps. If a member is at
100% daily cap and a project wants to dispatch a step, the quotas service
blocks. If the scheduler fires a step that would be blocked by a per-day
cost cap, **Settings → Auto top-up** can grant a small per-day extension
(off by default, hard global ceiling of $5 per top-up, 10/day).

## API tokens (for scripting)

**Settings → API tokens → New token**. You'll see the raw token exactly
once. Pass it as `Authorization: Bearer hlns_…` to any `/api/*` endpoint.
Scoped to your user; same permissions as a session cookie.

## FAQ

**Can a run belong to multiple projects?** No — each run is attributed to
one project (or none, adhoc). That keeps costs addable without
double-counting. If you need cross-project effort, run separate workflows
and aggregate offline.

**What happens to runs when a project is deleted?** The project row is
removed; run attribution is `ON DELETE SET NULL` so the runs survive but
show as `(adhoc)` on dashboards.

**Can I edit a past daily report?** The coordinator re-runs produce a
force update if you pass `{force: true}` to `/api/projects/:id/reports/generate`,
which overwrites that date's summary. Use the button in the Reports panel.
