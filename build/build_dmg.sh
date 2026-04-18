#!/bin/bash
# Build the complete Agent Company .dmg for macOS.
#
# This builds:
#   1. The Python backend sidecar (via PyInstaller)
#   2. The Tauri desktop app (Rust + React)
#   3. Bundles everything into a .dmg
#
# Prerequisites:
#   - Rust toolchain (rustup)
#   - Node.js + npm
#   - Python 3 + pip
#   - Xcode Command Line Tools
#
# Usage:
#   cd agent_company
#   bash build/build_dmg.sh

set -euo pipefail
cd "$(dirname "$0")/.."

echo "=== Step 1: Build Python sidecar ==="
bash build/build_sidecar.sh

echo ""
echo "=== Step 2: Build Tauri desktop app ==="
cd desktop
npm install
npm run build  # Vite build of React frontend
npx tauri build

echo ""
echo "=== Done! ==="
echo "DMG location: desktop/src-tauri/target/release/bundle/dmg/"
ls -lh src-tauri/target/release/bundle/dmg/*.dmg 2>/dev/null || echo "(DMG not found — check build output above)"
