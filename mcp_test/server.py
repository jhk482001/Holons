"""Minimal JSON-RPC 2.0 MCP server for manual / e2e validation.

Implements just enough of the Model Context Protocol (Streamable HTTP
flavor) that agent_company's `backend.services.mcp_client` can talk to
it:

    POST /mcp
      { jsonrpc, id, method: "initialize" | "tools/list" | "tools/call", params }

Exposes three fake tools so the full round trip (spec list → call →
result surfacing in the asset usage log) can be exercised without any
real external MCP vendor.

Run with::

    python3 -m mcp_test.server         # from agent_company/
    # or
    ./mcp_test/run.sh

Listens on port 8190 by default. Agent Company's Library page can point
an MCP asset at http://localhost:8190/mcp to drive this.
"""
from __future__ import annotations

import datetime as _dt
import logging
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import sys

logging.basicConfig(
    level=logging.INFO,
    format="[mcp_test] %(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("mcp_test")

PORT = int(os.environ.get("MCP_TEST_PORT", "8190"))

# ============================================================================
# Tool definitions — spec + handler
# ============================================================================

TOOLS: list[dict] = [
    {
        "name": "echo",
        "description": "Echo back whatever text you pass in. Useful for smoke tests.",
        "inputSchema": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    },
    {
        "name": "now",
        "description": "Return the current UTC time as an ISO-8601 string.",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "add",
        "description": "Add two integers.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "a": {"type": "integer"},
                "b": {"type": "integer"},
            },
            "required": ["a", "b"],
        },
    },
]


def handle_tool(name: str, arguments: dict) -> dict:
    """Dispatch a tool invocation and return an MCP `tools/call` result."""
    if name == "echo":
        text = arguments.get("text", "")
        return {
            "content": [{"type": "text", "text": f"echo: {text}"}],
            "isError": False,
        }
    if name == "now":
        return {
            "content": [
                {"type": "text", "text": _dt.datetime.now(tz=_dt.timezone.utc).isoformat()}
            ],
            "isError": False,
        }
    if name == "add":
        try:
            total = int(arguments["a"]) + int(arguments["b"])
        except (KeyError, TypeError, ValueError) as exc:
            return {
                "content": [{"type": "text", "text": f"add error: {exc}"}],
                "isError": True,
            }
        return {
            "content": [{"type": "text", "text": str(total)}],
            "isError": False,
        }
    return {
        "content": [{"type": "text", "text": f"unknown tool {name!r}"}],
        "isError": True,
    }


# ============================================================================
# JSON-RPC 2.0 request handling
# ============================================================================

def dispatch(request: dict) -> dict:
    req_id = request.get("id")
    method = request.get("method")
    params = request.get("params") or {}
    log.info("rpc method=%s", method)

    if method == "initialize":
        # Return a minimal server-info envelope; agent_company's mcp_client
        # doesn't check the capabilities deeply.
        result = {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "mcp_test", "version": "0.1"},
        }
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        name = params.get("name", "")
        arguments = params.get("arguments") or {}
        result = handle_tool(name, arguments)
    elif method == "ping":
        result = {}
    else:
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": -32601, "message": f"method not found: {method}"},
        }

    return {"jsonrpc": "2.0", "id": req_id, "result": result}


# ============================================================================
# HTTP plumbing
# ============================================================================

class Handler(BaseHTTPRequestHandler):
    def _send_json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.end_headers()

    def do_GET(self) -> None:
        if self.path == "/":
            self._send_json({
                "service": "mcp_test",
                "endpoint": "POST /mcp",
                "tools": [t["name"] for t in TOOLS],
            })
            return
        self._send_json({"error": "not found"}, status=404)

    def do_POST(self) -> None:
        if self.path not in ("/mcp", "/"):
            self._send_json({"error": "not found"}, status=404)
            return
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            request = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            self._send_json(
                {"jsonrpc": "2.0", "id": None,
                 "error": {"code": -32700, "message": "parse error"}},
                status=400,
            )
            return
        response = dispatch(request)
        self._send_json(response)

    def log_message(self, format: str, *args) -> None:  # noqa: A002
        # Use our logger instead of BaseHTTPRequestHandler's stderr spam
        log.info("%s - %s", self.client_address[0], format % args)


def main() -> None:
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    log.info("mcp_test server listening on http://localhost:%s/mcp", PORT)
    log.info("available tools: %s", [t["name"] for t in TOOLS])
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("shutting down")
        server.shutdown()
        sys.exit(0)


if __name__ == "__main__":
    main()
