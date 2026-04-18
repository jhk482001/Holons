"""Minimal MCP (Model Context Protocol) client.

Supports the **Streamable HTTP** transport: a single HTTP endpoint that
accepts JSON-RPC 2.0 messages via POST and responds with JSON (or SSE,
but we stick to single-response JSON for simplicity).

What we need from an MCP server:
  - `initialize`   — handshake (required by spec; some servers reject
                     tool calls without it)
  - `tools/list`   — fetch the catalog of tools the server exposes
  - `tools/call`   — invoke a tool and return its result

No streaming, no prompts, no resources — just tools. If a server
requires SSE or other transports, we ignore it.

Auth: optional bearer token sent as `Authorization: Bearer <token>`.
Configurable per-agent via the `agent_mcp_servers.auth_header` column
(full header value — e.g. "Bearer sk-123").
"""
from __future__ import annotations

import json
import logging
import uuid
from typing import Any, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

log = logging.getLogger("agent_company.mcp_client")

PROTOCOL_VERSION = "2024-11-05"
DEFAULT_TIMEOUT = 15.0
MAX_RESPONSE_BYTES = 5 * 1024 * 1024


class McpError(RuntimeError):
    """Raised when an MCP server returns a JSON-RPC error or the transport fails."""


def _rpc(url: str, method: str, params: Optional[dict], auth_header: Optional[str],
         timeout: float = DEFAULT_TIMEOUT) -> Any:
    """Single JSON-RPC request/response over HTTP."""
    rpc_id = uuid.uuid4().hex[:8]
    payload = {
        "jsonrpc": "2.0",
        "id": rpc_id,
        "method": method,
    }
    if params is not None:
        payload["params"] = params
    body = json.dumps(payload).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    if auth_header:
        headers["Authorization"] = auth_header
    req = Request(url, data=body, headers=headers, method="POST")
    try:
        with urlopen(req, timeout=timeout) as resp:
            raw = resp.read(MAX_RESPONSE_BYTES + 1)
    except HTTPError as e:
        raise McpError(f"HTTP {e.code} from MCP server: {e.reason}")
    except URLError as e:
        raise McpError(f"MCP transport error: {e.reason}")
    if len(raw) > MAX_RESPONSE_BYTES:
        raise McpError("MCP response exceeded size limit")
    try:
        msg = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as e:
        raise McpError(f"MCP non-JSON response: {e}")
    if "error" in msg and msg["error"]:
        err = msg["error"]
        raise McpError(f"MCP error {err.get('code')}: {err.get('message', 'unknown')}")
    return msg.get("result")


def initialize(url: str, auth_header: Optional[str] = None) -> dict:
    """Send the MCP initialize handshake. Returns the server's capabilities."""
    return _rpc(url, "initialize", {
        "protocolVersion": PROTOCOL_VERSION,
        "capabilities": {},
        "clientInfo": {
            "name": "agent_company",
            "version": "1.0",
        },
    }, auth_header) or {}


def list_tools(url: str, auth_header: Optional[str] = None) -> list[dict]:
    """Return the MCP server's tool catalog.

    Each entry: {name, description, inputSchema}. We silently handle
    servers that skip initialize — if tools/list works, great.
    """
    try:
        initialize(url, auth_header)
    except McpError as e:
        log.warning("MCP initialize failed for %s: %s", url, e)
    result = _rpc(url, "tools/list", None, auth_header) or {}
    return result.get("tools") or []


def call_tool(url: str, name: str, arguments: dict,
              auth_header: Optional[str] = None) -> dict:
    """Invoke an MCP tool and return a flattened result dict.

    MCP tool results come back as {content: [{type, text|data|...}], isError}.
    We flatten the text blocks into one string and expose it as `text`.
    Non-text content is dropped.
    """
    result = _rpc(url, "tools/call", {
        "name": name,
        "arguments": arguments or {},
    }, auth_header) or {}
    content = result.get("content") or []
    texts = []
    for c in content:
        if isinstance(c, dict) and c.get("type") == "text":
            texts.append(c.get("text") or "")
    is_error = bool(result.get("isError"))
    return {
        "text": "\n".join(texts).strip(),
        "is_error": is_error,
    }


# ============================================================================
# Convenience: fetch all tools for an agent across its configured MCP servers
# ============================================================================

def gather_agent_tools(agent_id: int) -> list[dict]:
    """Return a list of {server_id, server_name, url, auth_header, spec}
    entries — one per MCP tool currently visible to the agent. Drops
    servers that are unreachable so the loop still makes progress.
    """
    from .. import db
    servers = db.fetch_all(
        """
        SELECT id, name, url, auth_header
        FROM agent_mcp_servers
        WHERE agent_id = %s AND enabled = TRUE
        ORDER BY id
        """,
        (agent_id,),
    )
    out: list[dict] = []
    for s in servers:
        try:
            specs = list_tools(s["url"], s.get("auth_header"))
        except Exception as e:  # noqa: BLE001
            log.warning("MCP list_tools failed for server %s (%s): %s",
                        s.get("name"), s.get("url"), e)
            continue
        for spec in specs:
            out.append({
                "server_id": s["id"],
                "server_name": s["name"],
                "url": s["url"],
                "auth_header": s.get("auth_header"),
                "name": spec.get("name"),
                "description": spec.get("description") or "",
                "input_schema": spec.get("inputSchema") or {},
            })
    return out
