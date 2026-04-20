# Live-DB regression suite

These tests run against the **running backend on localhost:8087** and
touch only a throwaway user per run — so `jay`, `molly`, `admin`, and
any real tenant data stay untouched. Intended to gate every
feature-branch push.

## Run

```
# 1. backend must already be up
DB_BACKEND=postgres DATABASE_URL=... python3 -m backend.app

# 2. in another shell, from repo root
python3 -m pytest tests/regression -v
```

## What's covered

- `test_auth.py` — /me, login, logout, bad password
- `test_agents.py` — CRUD
- `test_dashboard.py` — summary, agent_load, heatmap, quota_overview,
  usage/daily for every group_by key
- `test_workflows.py` — list
- `test_projects.py` — list, create, per-project sub-endpoints
- `test_lead_threads.py` — listing only (no Bedrock spend)
- `test_cast_layout.py` — hidden_agents + facing round-trip
- `test_im_bindings.py` — Telegram binding CRUD, rejection paths
- `test_search.py` — empty + no-match queries

## Deliberate omissions

- **No `POST /api/lead/chat` or `POST /api/workflows/:id/run`** — these
  spend Bedrock credits. Run those manually or in an end-to-end smoke
  before a release.
- **No Slack/LINE binding success** until those adapters land + the
  user provides test tokens.
