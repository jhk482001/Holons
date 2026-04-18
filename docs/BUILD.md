# Build

Three targets: **desktop binary**, **web Docker image**, **sidecar only**.

## Prerequisites

- Python 3.9+ with `pip`
- Node 18+ with `npm`
- Rust toolchain (`rustup install stable`) — desktop only
- Xcode Command Line Tools (macOS) / build-essential (Linux) / Build Tools (Windows)
- PyInstaller: `pip install pyinstaller`

## Desktop binary (`.dmg` / `.msi` / `.AppImage`)

One command builds everything — Python sidecar → Vite build → Tauri bundle:

```bash
bash build/build_dmg.sh
```

The script runs:

1. `build/build_sidecar.sh` — PyInstaller packages `backend.standalone` into a
   single-file executable at `desktop/src-tauri/sidecar/agent-company-backend`.
2. `npm run build` in `desktop/` — Vite emits `desktop/dist/`.
3. `npx tauri build` — Rust release build + bundle `.app`, `.dmg`,
   `.exe`/`.msi`, `.AppImage`/`.deb` depending on the host OS.

Artifacts land in:

```
desktop/src-tauri/target/release/bundle/
├── macos/Holons.app
├── dmg/Holons_<version>_<arch>.dmg
├── msi/Holons_<version>_x64_en-US.msi       (windows only)
└── appimage/agent-company_<version>_amd64.AppImage (linux only)
```

### Signing

Builds are **unsigned** by default. First launch shows an "unverified
developer" warning. Workarounds for end-users:

- **macOS**: right-click the `.app` → Open → Open Anyway. Or run
  `xattr -cr "/Applications/Holons.app"`.
- **Windows**: Click "More info" → "Run anyway" on SmartScreen.
- **Linux**: AppImages don't trigger warnings.

To sign, set up Apple Dev ID ($99/yr) / SSL.com / Sectigo certs and configure
`tauri.conf.json`'s `macOS.signingIdentity` / `windows.certificateThumbprint`.
See [Tauri signing docs](https://tauri.app/distribute/sign/).

### macOS universal binary

`npx tauri build --target universal-apple-darwin` produces a single `.app`
containing both arm64 and x86_64 slices. Requires both Rust targets installed:

```bash
rustup target add aarch64-apple-darwin x86_64-apple-darwin
```

## Sidecar only

If you just want the standalone Python backend as a single executable (for a
server deploy without Docker):

```bash
bash build/build_sidecar.sh
# → desktop/src-tauri/sidecar/agent-company-backend
# Run it: ./agent-company-backend --port 8087
```

## Web Docker image (server mode)

> Docker support for the app container is not enabled out of the box — the
> `docker-compose.yml` only runs Postgres + pgAdmin for dev. If you want to
> containerize the app, use this as a starting point and PR improvements back.

```dockerfile
# Dockerfile (example)
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY backend ./backend
COPY static ./static
COPY frontend/dist ./frontend/dist
ENV DB_BACKEND=postgres
ENV PORT=8087
CMD ["python", "-m", "backend.app"]
```

Build the frontend first (`cd frontend && npm run build`), then
`docker build -t agent-company .`, mount `.env`, and connect to Postgres.

## Release automation (GitHub Actions)

`.github/workflows/release.yml` builds the desktop binary for macOS
(arm64 + x86_64), Windows, and Linux on every git tag. Artifacts are attached
to the GitHub Release. See the workflow file for the exact matrix.

## CI (every push)

`.github/workflows/ci.yml`:

- Python: `ruff check`, `pytest backend/tests`
- Frontend: `npm run build` (type-checks + builds)
- Rust: `cargo fmt --check` + `cargo clippy -- -D warnings`

All three must be green before merge.

## Uninstall / full reset (macOS)

Dragging `Holons.app` to the Trash only removes the app bundle. User state
lives in two places outside the bundle:

| What | Where |
| --- | --- |
| Tauri store (app mode, server URL, desktop token, language) | `~/Library/Application Support/com.holons.desktop/` |
| Personal-mode SQLite DB + sidecar log | `~/.agent_company/` |

To completely reset so the next launch shows the first-run setup screen:

```bash
pkill -f Holons
pkill -f agent-company-backend
rm -rf ~/Library/Application\ Support/com.holons.desktop
rm -rf ~/.agent_company
```

Then reinstall the DMG. If you only want to forget the login/connection but
keep your agents and chat history, delete just the first path.

## Troubleshooting

- **App skips the Enterprise/Personal setup and goes straight to the cast
  bar** — stale Tauri store. See [Uninstall / full reset](#uninstall--full-reset-macos)
  to clear `~/Library/Application Support/com.holons.desktop/session.json`.
- **Login silently fails on first open but works after a restart** — the
  sidecar respawn effect races the initial `/api/me` query. Fixed as of
  v0.1.0; if you see it, verify `DesktopApp.tsx` gates `useQuery` on
  `sidecarReady`.
- **`bundle_dmg.sh` fails with hdiutil errors** — stale mounts from a prior
  run. `hdiutil detach /dev/diskN -force` for each listed in `hdiutil info`,
  then retry.
- **Sidecar not found at runtime** — confirm the resource is bundled. Check
  `tauri.conf.json` has `"bundle.resources": {"sidecar/agent-company-backend":
  "agent-company-backend"}`. On macOS it ends up at
  `Contents/Resources/agent-company-backend`.
- **PyInstaller misses a hidden import** — add it to `--hidden-import` in
  `build/build_sidecar.sh`. Common culprits: new services under
  `backend/services/` or `backend/llm_clients/`.
