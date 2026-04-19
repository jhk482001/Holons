# Changelog

All notable changes to Holons are documented here. The format roughly
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and
versions use [SemVer](https://semver.org/).

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

[0.2.0]: https://github.com/jhk482001/Holons/releases/tag/v0.2.0
[0.1.0]: https://github.com/jhk482001/Holons/releases/tag/v0.1.0
