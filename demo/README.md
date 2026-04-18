# Demo seed

Idempotent script that provisions a demo user `jay` with two showcase setups:

1. **Screenwriting Room** — Jade (showrunner, lead) · Eli (screenwriter) · Mia (script doctor) · Leo (structure consultant), plus a sequential "Writers Room" group.
2. **Startup Pitch Council** — three founder archetypes (Travis/Brian/Patrick) and three VC archetypes (Mike/Marc/Bill), plus a pre-built 3-round **Pitch Deck** workflow.

## Run

```bash
# 1. boot the backend at least once so schema is created
DB_BACKEND=sqlite python -m backend.standalone

# 2. (optional, in another shell) seed the demo data
DB_BACKEND=sqlite python -m demo.seed_demo
```

Login: `jay` / `demo`.

Re-running is a no-op if `jay` already exists. Delete the row (or restart against an empty DB) to re-seed.

## What's worth clicking

- **Groups → Writers Room → Open chat**: sequential room where each member riffs off the previous reply.
- **Workflows → Pitch Deck — 3 rounds → Run**: feed it a one-liner idea ("a debit card for teenagers" etc.); watch founders propose, VCs critique, final markdown deck land in the run output.

The founder / VC personas are archetypal, not real people — names are generic first names and prompts describe the *type* of operator, not specific individuals.
