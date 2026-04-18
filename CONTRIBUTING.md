# Contributing

Thanks for taking the time to contribute. This project is small — feedback,
bug reports, and small focused PRs are more valuable than large architecture
rewrites.

## Ground rules

- **One PR, one concern.** Easier to review, easier to revert.
- **Explain *why*.** The PR description should answer: what problem does this
  solve, and what alternatives did you consider?
- **Tests when behaviour changes.** `backend/tests` runs against SQLite so
  tests are fast; frontend uses vitest.
- **No destructive commands without asking.** Don't add migrations that drop
  columns, `rm -rf`, force-push CI steps, or anything that can't be undone.

## Dev setup

See **[docs/DEVELOPMENT.md](docs/DEVELOPMENT.md)** for a full walkthrough. The
short version:

```bash
git clone https://github.com/jhk482001/Holons.git
cd Holons
cp .env.example .env
docker compose up -d postgres
pip install -r requirements.txt
cd frontend && npm install && cd ..

# run backend + frontend in separate shells
python -m backend.standalone --port 8087
cd frontend && npm run dev
```

## Coding conventions

### Python
- Python 3.9+, 4-space indent, `snake_case`.
- Keep dependencies light. Favor stdlib + the packages already in
  `requirements.txt` over new ones.
- Module layout: `backend/services/*.py` for business logic,
  `backend/app.py` for HTTP routes only (no business logic in routes).
- DB access through the thin `backend.db` facade so SQLite + Postgres stay in
  sync. Use `%s` placeholders and `db.execute_returning` for INSERTs that need
  an id back.

### TypeScript / React
- TypeScript strict-mode; no `any` unless you explicitly justify it.
- State: React Query for server state, component state for UI state. Avoid a
  global store unless proven necessary.
- i18n: user-facing strings go through `t("key")` with both `en` and `zh-TW`
  locales in `frontend/src/i18n/`.

### Rust / Tauri
- `cargo fmt && cargo clippy -- -D warnings` before pushing.
- Keep the Rust side thin — prefer moving logic to the Python sidecar.

### Commit messages
- Short, imperative first line: `fix: group chat not scrolling` / `feat:
  aggregator can be null`.
- Body (if needed) explains *why*, not *what*.

## Security / secrets

- Never commit `env.config`, `.env`, SQLite DBs, or keys. The `.gitignore`
  covers these — just don't override it.
- If you discover a secret was committed: **do not open a public PR**. Email
  the maintainer first.

## Pull request checklist

- [ ] One logical change
- [ ] Tests for new behaviour (or a note why tests don't make sense here)
- [ ] Updated docs if public behaviour changed
- [ ] `python -m pytest backend/tests` + `cd frontend && npm run build` pass
- [ ] No committed secrets, DBs, or build artifacts
