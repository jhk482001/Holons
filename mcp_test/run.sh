#!/bin/bash
# Start the mcp_test validation server on port 8190.
# Kill any previous instance first so repeated runs don't fight.
set -euo pipefail
cd "$(dirname "$0")/.."
PORT="${MCP_TEST_PORT:-8190}"
pid=$(lsof -ti tcp:"$PORT" || true)
if [ -n "$pid" ]; then
  echo "killing previous mcp_test on port $PORT (pid=$pid)"
  kill "$pid" || true
  sleep 1
fi
exec python3 -m mcp_test.server
