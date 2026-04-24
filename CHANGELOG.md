# Changelog

All notable changes to Holons are documented here. The format roughly
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and
versions use [SemVer](https://semver.org/).

## [Unreleased]

### Added

- **Unified LLM call ledger (`llm_calls`)** — every model invocation
  (agent run, Lead chat, Lead proxy, skill extraction, project report,
  model-client test) writes one normalised row with user / agent /
  run / model / cost / tokens / duration / error. Closes the
  long-standing gap where only `run_steps` had reliable cost data.
- **Admin → Usage tab** — cross-user report with 6 headline widgets,
  stacked-by-user daily cost chart, top users / top models lists,
  kind breakdown, and a filterable records pane. 30 s auto-refresh.
- **Per-user default model client** — new
  `as_users.default_model_client_id` routes background LLM paths
  (skill extraction, project reports) through a chosen client before
  falling back to the agent's primary. PersonalTab gains the dropdown.
- **Soft-warning quota thresholds** — `user_quotas.daily_warn_pct` +
  `monthly_warn_pct` (default 80, clamped 10–95). Dashboard shows a
  new user-quota bar that flips orange at `warn_pct`, red at 100%.
- **Admin quota editor** — per-user limits + warn_pct editable from
  User Management tab.
- **Group chat aggregator** — sequential groups now honour
  `aggregator_agent_id`: members reply in order, then the aggregator
  synthesises into a single recommendation with a role-hint prompt.
- **Build-version badge** — `1.0.0+YYYYMMDD-HHMM.<sha>[-dirty]` baked
  into every desktop build; shown in the Setup/Login brand header,
  the overlay top-right pill, and the tray menu (disabled row).
- **Preflight schema upgrade flow** — new personal-mode sidecars
  detect missing tables / columns in an existing
  `~/.agent_company/data.db` and offer "Back up and upgrade" /
  "Upgrade without backup" / "Cancel" before booting Flask.
- **Bust color selection** — per-agent in the desktop cast bar
  (default / black / Holons orange) via right-click menu.
- **Chat panel anchoring** — desktop chat panel positions itself next
  to the selected bust according to facing direction, clamped to
  viewport margins; collapses to compact mode for empty threads.

### Fixed

- `group_chat.py::_generate_reply` now tags calls with `kind='group'`
  and populates `user_id` so Admin Usage attributes team rooms
  correctly (previously logged as generic `system`).
- `/api/backup/info` no longer returns 403 to non-admin callers;
  responds with `{exportable: false, reason: "admin only"}` so
  PersonalTab's BackupSection renders the disabled state cleanly.
- SQLite schema mirror parity sweep: 6 missing tables + 28 missing
  columns backfilled; `create_all_sqlite` gains a two-pass retry so
  ALTERs that target later-defined CREATEs succeed on fresh installs.
- `im_channels.manager.start_all` skips gracefully when the
  `im_bindings` table is missing instead of crashing the backend.

### Repo

- `mcp_test/` and `rag_test/` moved to `.gitignore` — throwaway
  servers for local prototyping, not product surface.


## [0.5.0] — 2026-04-22

### Added

- **Workspaces** — first-class scratchpad filesystems that agents can
  read and write across a workflow. Each workspace is a directory on
  the backend host (`~/.agent_company/workspaces/` in personal mode,
  `/var/lib/holons/workspaces/` in enterprise, overridable via
  `HOLONS_WORKSPACE_ROOT`). Cross-user isolation via subdir; every file
  op funnels through a realpath + commonpath clamp so a prompt that
  calls `file_write("../../etc/passwd", ...)` bounces. Includes
  `workspaces` table + `/api/workspaces/...` REST endpoints, plus a
  `/workspaces` list page and `/workspaces/:id` detail page with flat
  file tree, markdown/plain-text viewer, and `.zip` download.
- **File tools** — `file_write` / `file_read` / `file_list` /
  `file_glob` / `file_delete`, registered into the built-in tool
  registry. Tasks pick up their workspace binding from either the
  enqueued payload (per-node) or the run-level `runs.workspace_id`, so
  agents can plumb artifacts between turns without copy-paste. 10 MiB
  per-file cap.
- **Code execution sandbox** — new `run_code(lang, code, timeout_s)`
  tool supporting `python / node / bash / sh`. Three pluggable
  runners:
    - `LocalSubprocessRunner` (default personal): wraps call in
      `sandbox-exec` on macOS with a profile that denies network and
      only permits writes inside the workspace, plus `RLIMIT_AS` +
      timeout.
    - `DockerRunner`: `docker run --rm --network=none --cap-drop=ALL
      --user=nobody` with memory / CPU caps. Enterprise opt-in.
    - `DisabledRunner` (default enterprise): returns a clear error.
  Selected via `CODE_EXECUTION_BACKEND=disabled|local|docker`. Per-user
  `as_users.enable_code_execution` flag gates use — Settings → Personal
  has a toggle that opens a warning modal the first time it flips on.
- **Named handoff payloads** — workflow nodes gain an `input_bindings`
  JSONB column. Each binding names an upstream value
  (`{"source": "original_input" | "prev_output", "as": "brief"}` or
  `{"from_node_position": 3, "as": "architect_doc"}`), and the engine
  resolves them into `{{name}}` placeholders in the node's prompt
  template. Back-compat with existing `{{input}} / {{prev_output}}`
  templates is preserved.
- **Unified Library experience** — `/skills` page retired and its
  content folded into `/library?tab=skill` as a "Self-learned skills"
  section below the shared-asset grid. Skill rows and asset rows share
  a Cards / List view toggle (stored in localStorage, default List).
  Self-learned skills reach parity with shared assets: enable/disable
  checkbox, `times_used` + `last_used_at` tracked and displayed,
  kebab-menu with a confirmation modal replaces the old always-visible
  Reject button.
- **Skill extractor** — full audit trail columns
  (`extraction_model_id`, `extraction_input_tokens`,
  `extraction_output_tokens`, `extraction_cost_usd`,
  `extraction_prompt_preview`, `extraction_response_preview`,
  `extraction_at`). Per-user `skills_auto_approve` flag (ON by default)
  so extracted skills immediately inject into the source agent's system
  prompt. Engine now reads `agent_skills` during `execute_task` and
  bumps `times_used` + `last_used_at` on every injection. Output
  language of the extractor follows the user's `language` setting —
  molly (en) gets English skill titles, jay (zh-TW) gets Chinese.
- **Agent card polish** — status text replaced with a colored dot
  (active=green with halo, paused=yellow, offline/off_duty=gray,
  budget_exceeded/quota_exceeded=red). "Queue depth limit" line
  removed from the card and moved into the agent detail page's Budget
  / Quotas tab, where the other caps already live.
- **Prompts in English** — every LLM-facing system prompt in the
  backend (skill extractor, Lead team roster + default agent voice,
  `chat_with_agent`, `lead_proxy`, `escalation` peer consult, quota
  notifications) now uses English. The extractor's user-facing
  *output* still honours the owning user's `language` so the two
  concerns are decoupled.

### Fixed

- Postgres `NUMERIC` columns (`extraction_cost_usd`, `confidence`) come
  back from Flask jsonify as strings. The Skills page called
  `.toFixed()` on them and crashed with `TypeError`. Added a
  defensive `num()` coercion helper at the edge.
- `POST /api/agents` silently dropped `tool_config` — the PUT handler
  honoured it but the create path did not. Brand-new agents always
  started with an empty tool set, which surfaced as "my BackendArchitect
  won't actually call `file_write`, it just emits code as chat text."
- Project workflow engine: four coordinated bugs fixed in one pass:
  - `scheduler._tick()` now passes `project_id` to `dispatch_workflow`
    via a new `schedules.project_id` column + UI dropdown.
  - Workflow-path steps that emit `artifact-*` fences are persisted to
    `project_artifacts`, not just kept on the lead_message.
  - `lead_agent.chat` resolves the acting agent from the project's
    `coordinator_agent_id` when `project_id` is set, falling back to
    Lead only if none is assigned.
  - Projects auto-add the coordinator to `project_members` on create /
    update so the quota middleware doesn't reject the coord's own
    dispatches. One-shot schema backfill for existing rows.

### Changed

- `workflow_nodes` gains `input_bindings JSONB DEFAULT '[]'`. Existing
  nodes with no bindings behave exactly as before.
- `runs` and `agent_tasks` gain a `workspace_id` column so the engine
  can thread workspace context through every task it enqueues from a
  given dispatch.
- `agent_skills` gains `last_used_at` (in addition to `times_used`)
  so the Library view can show "used N× · last X ago" parity with
  shared assets.
- `as_users` gains `skills_auto_approve BOOLEAN DEFAULT TRUE` and
  `enable_code_execution BOOLEAN DEFAULT FALSE`.

### Known limitations

- Workflow editor UI still needs a form to edit `input_bindings` —
  for v0.5 you can set them via direct API PUT. Editor UI is a v0.6
  candidate.
- `run_code` stdout is not yet rendered in the RunDetail page (you
  see it via the DB / zip download). UI trace panel is next.
- DockerRunner does not yet stream logs; stdout arrives at the end.

### Release notes

Verified end-to-end with a live BackendArchitect agent (Haiku 4.5 /
Bedrock) that was asked to build a Flask todo API + pytest suite in an
empty workspace. Over 10 tool-loop turns it wrote `app.py` (767 B),
`test_app.py` (1975 B), iterated through three `run_code` attempts
that failed on PATH / import issues, rewrote its own test file, then
ran `python3 -m pytest -v -p no:logging` successfully (6/6 tests
passing). 30 s wall-clock, $0.0535 in Bedrock spend. Artefacts live in
the workspace and survive restart. This is the "agency-agents style"
workflow the Tier-1 redesign was meant to unlock.

97 → 98 regression tests passing. Playwright smoke (admin + molly +
jay) across every major page: zero console errors.

## [0.4.0] — 2026-04-21

### Added

- **IM channel bindings** — Lead is now reachable from your phone.
  First platform is Telegram; Slack and LINE follow behind the same
  `BasePlatformAdapter` abstraction. Per-user bot tokens, saved in
  Settings → Channels; polling or webhook transport per binding.
  Commands include `/help /runs /status /workflows /run <id> [input]
  /run_status <id> /projects /project <id> /hire <role>` plus free
  text which goes straight to Lead chat. Session continuity across
  platforms — starting a thread on the web and replying from Telegram
  continues the same Lead conversation.
- **Artifact rich delivery over IM** — html / slides / markdown / file
  artifacts emitted by Lead get uploaded as real documents in Telegram
  (multipart sendDocument / sendPhoto, stdlib only). Short markdown
  goes inline as a regular message. Slack + LINE fall back to a
  "see web UI" breadcrumb until their file APIs are wired (next release).
- **Backup & export** for the personal-mode SQLite store. New
  `GET /api/backup/download` streams a consistent snapshot taken via
  `sqlite3.Connection.backup()` (safe under concurrent writes).
  Settings → Personal gains a "Backup & export" section showing
  backend / path / size / mtime, a one-click download, and a
  collapsible "How to restore" recipe. Complemented by a new
  `docs/install.md` section explaining the upgrade/downgrade story.
- **Model client UX** — four interlocking affordances so users stop
  getting "silent failure" from misconfigured LLMs:
  - `GET /api/model_clients/kinds/<kind>/sample` returns an example
    config + credential pair per provider. Create/edit modal shows
    "View sample" with Copy / Download .json / Use-as-template.
  - `POST /api/model_clients/<id>/test` fires a 5-token round-trip
    through the real LLM pipeline; result writes back to
    `last_test_at / last_test_status / last_test_message` columns.
  - Per-card status pill: 🟢 working / 🔴 failed / ⚪ never tested,
    hover for timestamp + error.
  - Global yellow banner in the app shell whenever no usable model
    client exists (0 clients / all disabled / all failed). Links
    straight to Settings → Models.
- **Ad-hoc codesigned macOS bundle** — `tauri.conf.json` sets
  `signingIdentity: "-"` so `cargo-bundle` invokes `codesign -s -`
  after assembly. The .app now carries a valid signature (flags
  `adhoc,runtime`), so macOS shows the real "unidentified developer"
  dialog instead of the misleading "is damaged" false-positive. Still
  not Apple Developer-ID signed, but the first-open UX is dramatically
  better.
- **Live-DB regression harness** at `tests/regression/` — pytest suite
  that spins up a throwaway user per test and cascades-cleans after,
  so it runs safely against a backend holding real tenant data
  (unlike the `tests/test_*.py` suite which TRUNCATEs the schema).
  Covers auth, agents CRUD, dashboard, workflows, projects, Lead
  threads (listing only — skips Bedrock spend), cast_layout round-trip,
  IM bindings + webhook endpoint, model-client sample + test,
  backup download. 82 tests, ~10s locally. Used as the gate before
  every feature-branch push since v0.4 development started.

### Changed

- `im_channels.router.dispatch` now returns a `DispatchResult(text,
  artifacts)` instead of `str | None`. Lets the manager / webhook
  endpoint hand the artifact list to the adapter for native rich
  delivery rather than stripping it into a breadcrumb.
- Mock replenishment-order dedup in the demo ERP MCP — agents running
  hourly sweeps no longer pile up N identical draft POs per day.
  (This is in the demo-side `Holons-demo/mocks/mcp_erp.py`, not
  product code, but worth flagging.)

### Fixed

- Nothing structural in this cycle; v0.4 is mostly additive. Minor:
  `python-telegram-bot` isn't needed — adapter uses stdlib `urllib`
  only, keeping the dep footprint unchanged from v0.3.

### Known limitations

- Slack + LINE adapters don't yet implement rich artifact delivery
  (`send_artifact` falls through to the base class no-op and the
  router breadcrumbs the text). Telegram is fully featured.
- Webhook HMAC verification isn't enforced — the URL-path secret is
  the only authorisation. For production deployments, add a reverse
  proxy that verifies Slack's `X-Slack-Signature` and LINE's
  `X-Line-Signature` headers.
- Desktop bundle is ad-hoc signed only. Apple Developer ID +
  notarization is the next upgrade — requires a paid Apple account,
  out of scope for v0.4.

### Release notes

Verified end-to-end: scheduler enabled for both demo tenants
(jay/molly), 1,070 fake historical runs seeded across 7 days for
dashboard population, real Telegram bot round-trip tested via
regression harness with mocked urllib. Model-client Test endpoint
round-tripped against live Bedrock (15 input + 4 output tokens,
1075 ms, $0.0001 per test click).

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

[0.5.0]: https://github.com/jhk482001/Holons/releases/tag/v0.5.0
[0.4.0]: https://github.com/jhk482001/Holons/releases/tag/v0.4.0
[0.3.0]: https://github.com/jhk482001/Holons/releases/tag/v0.3.0
[0.2.0]: https://github.com/jhk482001/Holons/releases/tag/v0.2.0
[0.1.0]: https://github.com/jhk482001/Holons/releases/tag/v0.1.0
