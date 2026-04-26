<!-- Thanks for the PR! Quick filing-cabinet metadata: -->

## What this changes

<!-- 1-3 sentences. The "why" matters more than the "what" — the diff
already shows what changed; the description should explain what problem
this solves. -->

## Linked issue / discussion

<!-- "Closes #123" / "Refs #456" / "Discussion: #789". If this is a
larger change, please link an Issue or Discussion that was triaged first
— maintainer time is limited and surprise PRs that touch architecture
are often pushed back to "let's discuss this in an Issue first". -->

## How I tested this

<!-- Pick whichever apply, delete the rest:

- Manual: clicked through X / Y / Z in the dialog, schedule fired and
  produced a `done` run.
- Automated: added test_X to tests/test_Y.py, runs locally with
  `pytest tests/test_Y.py -v`.
- Type-check / build: `tsc --noEmit` clean, `npm run build` clean.
- N/A — pure docs / typo fix.
-->

## Reviewer notes

<!-- Anything specific you want the reviewer to look at? Anything you're
unsure about? Areas you didn't touch on purpose? -->

## Checklist

- [ ] One PR, one concern. (If this fixes two unrelated bugs, please split it.)
- [ ] Tests added for behaviour changes (or a note in *Reviewer notes* explaining why none).
- [ ] No destructive migrations (`DROP COLUMN`, `TRUNCATE`, force-push CI steps).
- [ ] No secrets / credentials in the diff.
- [ ] Lints + types clean locally (CI will check too).
