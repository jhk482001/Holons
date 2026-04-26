#!/bin/bash
# Build the Python backend into a standalone macOS binary using PyInstaller.
# The output goes to desktop/src-tauri/sidecar/ so Tauri can bundle it.
#
# Prerequisites:
#   pip install pyinstaller
#
# Usage:
#   cd agent_company
#   bash build/build_sidecar.sh

set -euo pipefail
cd "$(dirname "$0")/.."

echo "=== Building backend sidecar ==="

# Ensure PyInstaller is available
python3 -m PyInstaller --version >/dev/null 2>&1 || {
    echo "PyInstaller not found. Installing..."
    pip install pyinstaller
}

# Build the web frontend first so the sidecar can serve SPA routes
# (/settings, /projects, /dashboard, …) to anyone opening the sidecar's
# port in a browser — including the desktop tray's "Open web settings".
#
# Always nuke the previous dist + Vite's persistent cache before building.
# Without this Vite occasionally reuses a stale `frontend/dist/index.html`
# that references hashed JS bundles built from earlier source — exactly
# the failure mode that shipped a non-functional dialog page in 1.0.0
# rebuilds.
echo "--- Building frontend dist for inclusion in sidecar ---"
(cd frontend && rm -rf dist node_modules/.vite && npm install --silent && npm run build)

FRONTEND_DIST_ARG=()
if [ -d "frontend/dist" ]; then
    FRONTEND_DIST_ARG=(--add-data "frontend/dist:frontend_dist")
fi

# Build
python3 -m PyInstaller \
    --name agent-company-backend \
    --onefile \
    --noconfirm \
    --clean \
    --hidden-import backend \
    --hidden-import backend.app \
    --hidden-import backend.standalone \
    --hidden-import backend.services \
    --hidden-import backend.llm_clients \
    --hidden-import backend.tools \
    --collect-all backend \
    --add-data "static:static" \
    "${FRONTEND_DIST_ARG[@]}" \
    backend/standalone.py
# NOTE: we do NOT bundle env.config — it's developer-local and would leak
# AWS/DB credentials into the distributed binary. The personal-mode sidecar
# runs on SQLite defaults without it, and managed deploys read env.config
# from the install dir, not the frozen archive.

# Move to sidecar location
SIDECAR_DIR="desktop/src-tauri/sidecar"
mkdir -p "$SIDECAR_DIR"
cp dist/agent-company-backend "$SIDECAR_DIR/"
echo "=== Sidecar built: $SIDECAR_DIR/agent-company-backend ==="
echo "Size: $(du -h "$SIDECAR_DIR/agent-company-backend" | cut -f1)"
