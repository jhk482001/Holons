#!/bin/bash
# Start the rag_test validation server on port 8191.
set -euo pipefail
cd "$(dirname "$0")/.."
PORT="${RAG_TEST_PORT:-8191}"
pid=$(lsof -ti tcp:"$PORT" || true)
if [ -n "$pid" ]; then
  echo "killing previous rag_test on port $PORT (pid=$pid)"
  kill "$pid" || true
  sleep 1
fi
exec python3 -m rag_test.server
