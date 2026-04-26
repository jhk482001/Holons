# Security policy

Holons is a small open-source project (one developer, MIT-licensed). I take
security reports seriously even though there's no formal team — please follow
the disclosure flow below so the fix lands before the bug becomes a public
exploit.

## Supported versions

Only the latest `main` is supported. The `1.0.x` line is the active release;
older tags do not receive backports. If you've been running an unreleased
build, the answer is almost always "please upgrade to current main first".

## Reporting a vulnerability

**Please do not file public Issues or Discussions for vulnerabilities.**

Instead, email **[holons.agent@gmail.com](mailto:holons.agent@gmail.com)** with:

- A short description of the issue and its impact.
- A minimal proof-of-concept (curl / script / screenshot) if you have one.
- The Holons version you're running (tray menu in desktop, `git rev-parse --short HEAD` in dev).
- Whether you'd like to be credited in the fix's changelog entry, and how.

I'll acknowledge receipt within **3 business days** and aim to ship a fix
within **14 days** for high-severity issues. If I haven't replied in a week,
please feel free to nudge — Gmail occasionally hides things in spam.

## Out of scope

The following are known design choices, not vulnerabilities:

- **Unsigned macOS / Windows binaries.** The .dmg / .msi are signed ad-hoc
  and not notarized — adding a paid signing cert is on the roadmap. Until
  then, the install instructions explicitly say "right-click → Open Anyway".
- **Self-hosted Postgres credentials in `.env`.** Operators set their own
  `DATABASE_URL`; we don't bundle production credentials.
- **`admin` / `admin` default login on first run.** Personal mode auto-seeds
  a single-user admin so the .dmg works out of the box. Self-hosted operators
  are expected to change the password (or seed their own users) before
  exposing the instance to other people.
- **Encrypted credentials at rest.** Model client / MCP / RAG credentials
  are encrypted with a per-install Fernet key persisted at
  `~/.agent_company/.encryption-key`. If an attacker has read access to that
  file, they have read access to the credentials. This is by design — the
  threat model assumes the user's machine is trusted.
- **No multi-factor auth.** Single-tenant personal-mode design. Self-hosted
  multi-user deployments rely on the operator to put a reverse proxy with
  SSO / 2FA in front if they need it.

If you think one of the above *is* actually exploitable in a way I haven't
thought of, please report it anyway — I'd rather hear about it.

## Coordinated disclosure

I'll work with you on a disclosure timeline that gives users a chance to
upgrade before details are public. Default is 30 days from confirmed fix,
but happy to negotiate based on severity.

Thanks for helping keep Holons safe.
