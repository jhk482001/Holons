# Architecture

## Three surfaces, one backend

```
┌─────────────────────────┐   ┌─────────────────────────┐
│  Desktop overlay        │   │  Web console            │
│  (Tauri + React, Rust   │   │  (React + Vite)         │
│   transparent window)   │   │                         │
└────────────┬────────────┘   └────────────┬────────────┘
             │                             │
             │  HTTP to localhost:PORT     │
             ▼                             ▼
          ┌──────────────────────────────────────┐
          │  Backend (Flask + services)          │
          │                                      │
          │  ┌────────────┐   ┌──────────────┐   │
          │  │ Lead Agent │   │ Group Chat   │   │
          │  └────────────┘   └──────────────┘   │
          │  ┌────────────┐   ┌──────────────┐   │
          │  │ Engine     │   │ Queue/Worker │   │
          │  │ (workflows)│   │              │   │
          │  └────────────┘   └──────────────┘   │
          │  ┌──────────────────────────────┐    │
          │  │ LLM clients (Bedrock /       │    │
          │  │  Anthropic / OpenAI / Gemini │    │
          │  │  / MiniMax)                  │    │
          │  └──────────────────────────────┘    │
          └────────────────┬─────────────────────┘
                           │
                           ▼
          ┌──────────────────────────────────────┐
          │  Storage: SQLite (personal) OR       │
          │  Postgres + pgvector (managed)       │
          └──────────────────────────────────────┘
```

- **Desktop** is a Tauri shell that embeds the built web bundle and spawns the
  Python backend as a child process ("sidecar"). The Rust side is kept thin —
  window management, tray, click-through, port discovery.
- **Web console** is plain Vite + React. Same API shape as desktop, just
  served from a dev server or static dist. In production server mode, it's
  served by the Flask app.
- **Backend** is Flask + a small services layer. The HTTP surface is CRUD +
  chat endpoints; all business logic lives under `backend/services/*`.

## Core concepts

| Concept | What it is |
|---|---|
| **Agent** | A persona with a name, role title, avatar, system prompt, and model binding. Each belongs to a user. |
| **Lead agent** | The user's default agent — acts as secretary. Answers directly or proposes workflows. Exactly one per user. |
| **Group** | An ordered collection of agents + a mode (`parallel` or `sequential`) + optional aggregator. Reusable. |
| **Group chat** | A conversational thread scoped to one group. User speaks, members reply per the group's mode. |
| **Workflow** | A directed sequence of nodes (agent or group) with prompt templates. Dispatched as a **run**. |
| **Run** | One execution of a workflow. Produces a series of `run_steps`, each capturing an agent turn. |
| **Skill / Tool / MCP** | Items in the **library**. Skills are reusable prompt snippets; tools are built-in Python functions; MCP entries point at external tool servers. |

## Data flow — typical chat turn

The Lead chat endpoint has both a batch and a streaming variant. They
share `_prepare_lead_call()` + `_finalise_lead_message()` so workflow /
hire / project / artifact parsing can never drift between the two.

```
User types in Dialog
    ↓
POST /api/lead/chat            (batch)         OR
POST /api/lead/chat/stream     (SSE)
    ↓
backend/services/lead_agent.chat()  /  chat_streaming()
    ├─ resolve/create thread
    ├─ INSERT user message into lead_messages
    ├─ build system prompt = LEAD_SYSTEM_PROMPT + team roster
    ├─ load last 20 messages into history
    ├─ invoke_for_agent / invoke_streaming_for_agent
    │    └─ llm_clients/{bedrock|claude_native|openai_compat|...}.py
    │       └─ records the call to llm_calls (kind='lead')
    │       └─ stream variant yields ("chunk", text) events along the way
    ├─ parse ```workflow / ```hire / ```project / ```artifact-* blocks
    └─ INSERT lead's reply into lead_messages
    ↓
Batch:  Response: { thread_id, response, proposed_workflow?, ... }
Stream: SSE events: thread → chunk × N → complete (same shape)
    ↓
Frontend re-fetches messages (react-query invalidation)
```

Group chat has analogous `/api/group-chat/<thread>/send` (batch) and
`/send/stream` (SSE) plus `/continue` and `/continue/stream` for the
"let them continue N rounds" loop. Stream events are tagged with
`agent_id` so multiple member bubbles can render side-by-side.

## Data flow — workflow run

```
User approves proposed workflow OR clicks "Run" on a saved one
    ↓
POST /api/workflows/:id/run  { input }
    ↓
backend/engine.dispatch_workflow()
    └─ INSERT runs row; enqueue first node into agent_tasks queue
    ↓
backend/worker.py  (1–N workers polling agent_tasks)
    ├─ pick up task
    ├─ resolve agent or group
    ├─ fan-out to group members (parallel: spawn all; sequential: chain)
    ├─ invoke LLM per member
    ├─ persist run_step rows
    └─ enqueue next node with prev_output
    ↓
When last node finishes, engine posts a completion message to the lead
thread (if dispatched from chat) AND emits a bell notification.
```

## Group chat mode specifics

See [`backend/services/group_chat.py`](../backend/services/group_chat.py).

- **Parallel**: take a snapshot of history once, call every member with the
  same context, persist replies. Members don't see each other's current-round
  replies.
- **Sequential**: loop over members; reload history between each so later
  speakers see earlier ones.
- **Continue rounds**: agents converse among themselves for N rounds
  (1–10). Larger history window (`_CONTINUE_HISTORY_LIMIT = 80`). No user
  message in between.

Runtime groups created by the Lead agent's workflow proposals are flagged
`is_ephemeral = true` and hidden from the user's Groups list.

## Storage

Two backends, one facade at `backend/db.py`:

- **SQLite** — default for personal desktop mode. File at
  `~/.agent_company/data.db`.
- **Postgres + pgvector** — managed / multi-user mode. Schema in
  `backend/schema.py`. Runs via `docker compose up postgres`.

Schema migrations are idempotent `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`
statements at the bottom of the DDL list — applied on every `_startup()`.
SQLite's `CREATE TABLE IF NOT EXISTS` + try/except handles both additions and
retries.

## LLM client abstraction

`backend/llm_clients/invoke_for_agent(...)` looks up the agent's
`model_client_id`, dispatches to the right adapter:

| Adapter | Providers |
|---|---|
| `bedrock.py` | AWS Bedrock (Claude, Titan) |
| `claude_native.py` | Anthropic API directly |
| `openai_compat.py` | OpenAI, Azure OpenAI, any compatible endpoint |
| `gemini.py` | Google Gemini |
| `minimax.py` | MiniMax |

Adapters normalize the response to a common dict (`{text, input_tokens,
output_tokens, cost_usd, model_id}`), so services don't branch on provider.

## Desktop ↔ sidecar boundary

Rust spawns the sidecar as a child process, reads stdout line-by-line until
it sees `PORT=XXXX`, then drains the rest. The frontend invokes
`start_sidecar` once during `ConnectionSetup` → Personal mode → waits for
`/api/health` → stores the port in the Tauri store.

Click-through (cursor passes through transparent areas) is a DOM mousemove
listener that calls `set_ignore_cursor_events` per hover zone.
See [`desktop/src/DesktopApp.tsx`](../desktop/src/DesktopApp.tsx)
`useClickThrough`.

## Projects + quotas + coordinator

Adds a management layer on top of the core chat/workflow primitives.

```
                  ┌──────────────────────────────┐
                  │  Project (long-lived goal)   │
                  │  + coordinator_agent_id      │
                  │  + goal / description        │
                  │  + status (active/paused/…)  │
                  └──────────────┬───────────────┘
                                 │
        ┌────────────────────────┼────────────────────────┐
        │                        │                        │
  project_members          project_milestones     project_reports
  (agent_id,              (title, due_date,       (daily coordinator
   daily_alloc_pct,        status)                 summary + metrics)
   monthly_alloc_pct)
        │
        └─ slices each agent's global quota by the pct.
           See `backend/services/quotas.py`.
```

**Attribution**: every `runs` + `run_steps` row gets a nullable
`project_id`. When a workflow is dispatched with `project_id`, engine
writes it on `runs`; every step INSERT copies it from the parent run via a
sub-select.

**Quota enforcement**: the existing rolling-window `agent_quotas` table
still governs absolute caps. Projects slice those caps by %. Before each
LLM call, `backend/services/quotas.can_run(agent_id, project_id)` checks
both. Worker catches the resulting RuntimeError and pauses the task rather
than crashing the run.

**Coordinator**: an agent flagged as `projects.coordinator_agent_id`.
`services/coordinator.chat()` wraps `lead_agent.chat()` but:
- Restricts the roster to project members.
- Injects project goal + milestones + remaining allocation into the prompt.
- Threads live in `lead_conversations` with id prefix `proj-<pid>-*` so
  the main Dialog view excludes them.

**Daily reports**: `services/project_reports.generate()` asks the
coordinator to summarize the last 24h of project activity, stores it in
`project_reports` (unique per (project, date)), posts a pointer into the
user's Lead thread, emits a notification, and optionally POSTs a webhook
to `as_users.report_webhook_url`. Scheduler fires once per 30 min across
all active projects with today's activity.

**Review loop**: a workflow node with `node_type='review'` runs like a
normal agent step; if its response begins with `REVISE: …`, the engine
re-enqueues the previous position with the feedback as `prev_output`.
Capped per position by `workflows.max_review_iterations` (default 2).

## Auth surfaces

- Session cookie (Flask `session["user_id"]`) — web frontend.
- `X-Desktop-Token` header — Tauri app after Personal / Enterprise setup.
- `Authorization: Bearer hlns_…` — user-issued API tokens for scripts. See
  [`api_tokens` table](../backend/schema.py) and `/api/me/api-tokens`.

All three paths resolve to a `user_id` via `login_required` + the helpers
around it in `backend/app.py`.

