# Changelog

All notable changes to Holons are documented here. The format roughly
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and
versions use [SemVer](https://semver.org/).

## [0.3.0] — 2026-04-20

### Added

- **Artifacts as first-class agent output.** Four fenced block types
  (`artifact-html`, `artifact-slides`, `artifact-markdown`,
  `artifact-file`) render as dedicated bubbles inside a Lead message —
  sandboxed iframe for html/slides, GFM-rendered markdown, download
  chip for files. Raises what an agent can produce from "text that
  describes a thing" to "the thing itself".
- **Project-scoped artifacts.** When the coordinator emits an artifact
  inside a project chat, it persists to a new `project_artifacts`
  table and surfaces in the project detail page's Artifacts section,
  attributed to the producing agent.
- **Tabbed project usage chart** — same 14-day spend window,
  switchable between `by member` / `by workflow` / `by model client`.
  Workflow labels auto-suffix with 📅 when any schedule points at
  them, making recurring scheduled work visually distinct from ad-hoc
  dispatches on the same project page. The model-client slice matters
  as soon as a tenant starts mixing Haiku (cheap execution) with
  Sonnet (reasoning).
- **Cast-bar UX parity with the desktop overlay.** Left/right scroll
  arrows when members overflow narrow screens, right-click facing
  direction (face left / face right) persisted to cast_layout.facing,
  and a chest-level "busy…" pill animates on any bust whose agent has
  queued work (polls `/api/dashboard/agent_load`).
- **Two-tab project chat.** "Ask <coordinator>" (project-scoped
  thread) and "Ask Lead" (global thread) — users can escalate from a
  project context to global Lead without leaving the page.
- **Coordinator card** on the project detail page is now visually
  distinct: accent border, soft glow, 👑 icon, full-width
  "Coordinator" pill (was an unreadable 9px "COORD" badge).
- **Dashboard refactor.** Agent load widget pulled up above the charts;
  Agent Timeline gets its own full-width panel; the three usage charts
  (project / member / team) move into a horizontal 3-col grid below.
  Every agent load card gets an embedded 24h heatmap strip, replacing
  the standalone heatmap section.
- **MCP integration guide** at `docs/mcp-integration.md` — covers the
  protocol subset Holons supports, the two config paths (legacy
  `agent_mcp_servers` vs. asset library), auto-migration, the
  deduplication rule, tool naming convention, auth, response size
  limits, and a stdlib-only minimum-viable MCP server example.

### Fixed

- **Engine crashed on any agent with an MCP configured.** The startup
  migration copies every `agent_mcp_servers` row into `asset_items` +
  `agent_assets`, and the engine then read MCP tools from both paths
  and passed duplicates to Bedrock Converse — which rejects the whole
  request with `tool mcp__<X>__<Y> is already defined at
  toolConfig.tools.N`. Added dedup on `(server_name, tool_name)`.
- **Cron-trigger schedules silently fell back to 1-hour intervals.**
  `backend.services.scheduler` imports `croniter` for next-run
  computation, but it wasn't listed in `requirements.txt`. Added.
- **i18n hole in the Teams page.** A duplicate top-level `groups` key
  in both language files caused the second definition to overwrite
  the first — all `groups.title / groups.subtitle / groups.empty` etc.
  rendered as literal keys. Merged and de-duplicated.
- **Hardcoded strings** in `HireBubble`, `ArtifactBubble`, `Workflows`
  (TEMPLATE/DRAFT badges), `RunDetail`, `Schedules`, `Records`,
  `AgentDetail`, `Dashboard`, and `Layout` (`Projects` nav item) —
  all now route through `t()`.
- **"自動化" in the Chinese nav** renamed to "Workflow" — matches the
  canonical product term and stays consistent with all other places
  Workflow is used untranslated.

### Changed

- `/api/usage/daily` gains `group_by=model_client` (joins
  `model_clients`) and annotates `group_by=workflow` labels with 📅
  based on an EXISTS check against `schedules`.

### Release notes

Verified end-to-end on Postgres with a scheduler-driven daily
market brief (Mei / Sonnet) and daily report (Lead / Haiku) firing on
cron, plus a six-step quote flow (Sales CRM → Finance ERP+accounting
→ Lead approve) running under a project's quota. Image generation via
a mock MCP wrapping `amazon.nova-canvas-v1:0` on Bedrock
ap-northeast-1.

## [0.2.0] — 2026-04-19

### Added

- **Lead can propose new hires.** When the team lacks a specialty or
  the user asks for a specific role, Lead drafts a profile (name,
  role, description, system prompt, rationale) in the chat as an
  editable card. One click and the agent is created, worker started,
  ready for work. API: `POST /api/lead/hire_proposals/<msg_id>/accept`.
- **Lead can propose opening a Project.** For multi-phase / multi-agent
  work, Lead drafts a project with goal, member list, and coordinator.
  One click and the project is created; subsequent workflow dispatches
  can be scoped to it via `project_id`. Gives automatic cost
  attribution, daily coordinator reports, and a single page to track
  the effort. API: `POST /api/lead/project_proposals/<msg_id>/accept`.
- **`HireBubble` component** in the Dialog Center — render/edit/accept
  proposed hires without leaving the chat.
- **README** tour + feature matrix updated to reflect Lead's three
  propose-and-approve surfaces (workflow, hire, project).

### Changed

- **SQLite ↔ Postgres schema parity** for the workflow engine path.
  The Postgres schema was the only well-tested one; SQLite was missing
  columns the engine and quota services query. Aligned `workflows`,
  `runs`, `run_steps`, `as_users`, `agent_quotas`, `agent_tasks`, and
  `user_quotas`. SQL translator also now handles `date_trunc(...)` and
  more `INTERVAL` variants.

### Fixed

- `model_clients.create()` was using `fetch_one` for an
  `INSERT ... RETURNING id` query, which on SQLite leaves the
  transaction uncommitted — breaking any follow-up FK check against
  that new row. Switched to `execute_returning` (which commits).

### Known limitations

- The workflow engine's parallel-group aggregator still uses
  `pg_try_advisory_xact_lock` and `conn.cursor()` context managers —
  both Postgres-only. Single-agent and sequential workflows on SQLite
  work; parallel-group aggregation still requires Postgres. Filed as
  tech debt for a future release.

### Release notes

Verified end-to-end on Postgres with a 10-round designer → reviewer
iteration scoped under a Lead-proposed project: 60 run steps, $8.21,
1.9M tokens, 22 game-design concepts produced and scored.

## [0.1.0] — 2026-04-18

First public release.

### Added

- Flask + React + Tauri desktop overlay
- Personal mode: single-binary sidecar + SQLite, no Docker required
- Enterprise mode: Postgres + pgvector, multi-user
- Lead agent, teams, group chat rooms, visual workflow editor
- Projects with per-agent quotas, daily reports, audit log
- Pluggable LLMs: Bedrock, Anthropic, OpenAI, Gemini, MiniMax
- MIT license

[0.3.0]: https://github.com/jhk482001/Holons/releases/tag/v0.3.0
[0.2.0]: https://github.com/jhk482001/Holons/releases/tag/v0.2.0
[0.1.0]: https://github.com/jhk482001/Holons/releases/tag/v0.1.0
