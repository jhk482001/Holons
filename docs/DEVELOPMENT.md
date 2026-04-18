# Development

Working on Holons locally.

## One-time setup

```bash
git clone https://github.com/jhk482001/Holons.git
cd agent-company

# Python
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Frontend deps
cd frontend && npm install && cd ..

# Desktop deps (optional — only if you'll touch the desktop app)
cd desktop && npm install && cd ..

# Config
cp .env.example .env
cp env.config.example env.config
# Edit .env for your local Postgres creds (dev defaults work with the compose file).
# env.config is only needed for managed-cloud deployments — leave it blank in dev.
```

## Running locally

Pick one DB mode:

### A. SQLite (simpler — no Docker)

```bash
DB_BACKEND=sqlite python -m backend.standalone --port 8087
```

First run auto-creates `admin` / `admin` + a starter team (Ava / Noah / Riley).

### B. Postgres (mirrors production)

```bash
docker compose up -d postgres
python -m backend.app     # reads .env
```

Visit **http://localhost:5050** for pgAdmin (admin@localhost / admin).

Either way, run the frontend in a second shell:

```bash
cd frontend && npm run dev   # http://localhost:5173
```

The Vite dev server proxies `/api` → `http://localhost:8087`.

### Seed demo data (optional)

```bash
python -m demo.seed_demo
# Creates user `jay` / `demo` with two showcase teams.
```

## Running the desktop app in dev

```bash
cd desktop
npm run tauri dev
```

The Tauri app:
- Spawns the Python backend as a subprocess (via `backend.standalone`).
- Serves the frontend from Vite at `localhost:1420`.
- Shows a transparent overlay with agent busts over your desktop.

Changes to `.tsx` hot-reload. Changes to `lib.rs` trigger a Rust recompile.

## Tests

```bash
# Backend (SQLite-based)
python -m pytest backend/tests -v

# Frontend (type-check + build)
cd frontend && npm run build
```

No end-to-end suite yet. Playwright-based smoke tests live under
`demo/e2e/` (used by `.github/workflows/ci.yml` to generate screenshots).

## DB migrations

Schema changes are idempotent `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`
statements appended at the bottom of `backend/schema.py` /
`backend/schema_sqlite.py`. They run on every `_startup()` — no migration
tool, no version numbers. Rules:

- **Additive only**: never drop columns or change types inside a migration.
  If a change isn't backward-compatible, ship a dual-read period first.
- **SQLite has no `ADD COLUMN IF NOT EXISTS`** — `create_all_sqlite` swallows
  "duplicate column" errors, so you just write `ALTER TABLE ... ADD COLUMN`
  and it no-ops on reruns.

## Making prompt changes

LLM system prompts live in:

- `backend/services/lead_agent.py` — `LEAD_SYSTEM_PROMPT`
- `backend/services/group_chat.py` — `_GROUP_SYSTEM_SUFFIX`
- `backend/standalone.py` — per-agent `system_prompt` inserted on first run
- `demo/seed_demo.py` — per-agent `system_prompt` for demo data

When you change a prompt, delete the DB (or the affected agent row) and let
first-run / seed recreate — existing agents keep their old prompt because
prompts are per-row, not per-code.

## Code style

See [CONTRIBUTING.md](../CONTRIBUTING.md). Short version:

- Python: stdlib first, `snake_case`, 4-space indent.
- TS: strict mode, React Query for server state, i18n via `t()`.
- Rust: `cargo fmt` + `cargo clippy -- -D warnings`.
- **No commented-out code** in PRs. If you're unsure, delete it and rely on
  git history.

## Useful scripts

| Command | Does |
|---|---|
| `python -m backend.standalone --port 8087` | Start backend on SQLite |
| `python -m backend.app` | Start backend on Postgres (reads `.env`) |
| `python -m demo.seed_demo` | Create demo user + agents |
| `cd frontend && npm run dev` | Vite dev server |
| `cd desktop && npm run tauri dev` | Desktop app in dev mode |
| `bash build/build_sidecar.sh` | PyInstaller → single-file backend |
| `bash build/build_dmg.sh` | Full desktop release build |

## Gotchas

- **Alembic / Django-like migrations don't exist** on purpose. Schema is
  additive + idempotent. Respect that; don't add a migration framework
  without discussion.
- **Model API keys**: never check in. Use the Library → Model Clients UI or
  set env vars read by the adapter modules.
- **Port conflicts**: default backend port 8087, Vite 5173, pgAdmin 5050,
  Postgres 5432, Tauri dev 1420. If any collide with something you run, set
  `PORT=` in `.env` / use `--port` for the backend, and update
  `frontend/vite.config.ts`'s proxy target.
