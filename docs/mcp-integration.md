# MCP integration

Holons speaks the [Model Context Protocol](https://modelcontextprotocol.io/)
over HTTP so an agent can call external tools — your ERP, your mail, a
datacenter monitor, an image generator — the same way it calls a built-in
tool. This document covers what Holons supports, how to configure an MCP
server for an agent, and the couple of non-obvious rules worth knowing.

---

## What we support

| MCP feature  | Supported | Notes                                                            |
|--------------|-----------|------------------------------------------------------------------|
| `initialize` | ✅         | Holons sends the handshake before any other request.            |
| `tools/list` | ✅         | Called at dispatch time to discover tools.                      |
| `tools/call` | ✅         | Results are flattened to text content; non-text blocks dropped. |
| Prompts      | ❌         | Not read.                                                        |
| Resources    | ❌         | Not read.                                                        |
| Sampling     | ❌         | Holons drives sampling; servers cannot ask for it.              |
| SSE / streaming | ❌     | Streamable HTTP single-response JSON only.                      |

Protocol version negotiated: `2024-11-05`.
Transport: HTTP POST of a JSON-RPC 2.0 envelope. Server responds with
single JSON (no chunking).

Source: `backend/services/mcp_client.py`.

---

## Tool naming convention

When Holons assembles the tool list it sends to the LLM, every MCP tool
is prefixed:

```
mcp__<server_name>__<tool_name>
```

Example: an MCP server registered as `mcp-erp` that exposes a
`list_orders` tool shows up to the LLM as `mcp__mcp-erp__list_orders`.

This avoids collisions with built-in tools (`current_time`, `http_get`,
`search_skills`, `search_kb_<N>`) — the LLM never sees two tools with
the same name. When the LLM calls a prefixed tool, the engine strips
the prefix before dispatching to the MCP server.

See `backend/engine.py::_execute_with_tools`.

---

## Two ways to wire an MCP to an agent

There are two tables an MCP can live in. Both paths work; both are read
at dispatch time and **deduplicated** (see below).

### 1. Asset library (recommended for new setups)

`asset_items` holds every external asset the system knows about
(skills, tools, RAG sources, MCPs). `agent_assets` is the bridge that
assigns an asset to an agent. This is the path the UI exposes under
the "Library" page.

```python
from backend.services import assets

# Create the asset once
asset_id = assets.create_asset(
    actor_user_id=user_id,
    kind="mcp",
    name="mcp-erp",
    config={"url": "http://127.0.0.1:8401/"},
    credential_plaintext="Bearer sk-...",   # optional; encrypted at rest
)

# Assign it to an agent
db.execute(
    "INSERT INTO agent_assets (agent_id, asset_id, enabled) VALUES (%s, %s, TRUE)",
    (agent_id, asset_id),
)
```

### 2. Legacy per-agent table

`agent_mcp_servers` is the earlier design: one row per (agent, MCP)
pair. Still fully supported — it's what `mcp_client.gather_agent_tools`
reads. Useful for bulk seeding, scripts, or tests:

```sql
INSERT INTO agent_mcp_servers (agent_id, name, url, auth_header, enabled)
VALUES (42, 'mcp-erp', 'http://127.0.0.1:8401/', NULL, TRUE);
```

### Auto-migration from legacy → asset library

On every backend startup, `schema._migrate_agent_mcp_to_asset_items()`
copies rows from `agent_mcp_servers` into `asset_items` + `agent_assets`
(marked with a `metadata.migrated_from` key so repeat startups don't
duplicate). This means:

- Writing to the legacy table alone is enough — the asset row appears
  automatically on the next restart.
- The engine then sees the same MCP via **both** paths at dispatch
  time. That's fine because of the dedup below, but it means you
  shouldn't write to both tables yourself — let migration handle it.

---

## Dispatch-time deduplication

The engine (`backend/engine.py`) assembles the MCP tool list from both
paths and dedups on `(server_name, tool_name)`:

```python
mcp_tools = mcp_client.gather_agent_tools(agent_id)       # legacy
asset_ctx = _gather_agent_assets(agent_id)                # asset library
seen = {(mt["server_name"], mt["name"]) for mt in mcp_tools}
for mt in asset_ctx["mcp"]:
    key = (mt["server_name"], mt["name"])
    if key in seen:
        continue
    mcp_tools.append(mt)
    seen.add(key)
```

**Why this matters:** without the dedup, Bedrock Converse rejects the
whole request with `"tool mcp__<X>__<Y> is already defined at
toolConfig.tools.N"` — the agent never runs. The dedup keeps the first
occurrence, which in practice is the legacy-path entry, and ignores the
auto-migrated duplicate.

---

## Auth

The `auth_header` column (legacy) / `credential_encrypted` field (asset
library) holds a full header value. Common forms:

- `Bearer sk-abc123...`
- `Authorization: token xyz`  (Holons strips the `Authorization:` prefix)

Values in `asset_items.credential_encrypted` are encrypted at rest with
Fernet; Holons decrypts at dispatch and passes the plaintext as the
`Authorization` header. Rotate by issuing a new `update_asset` call
with a fresh `credential_plaintext`.

For unauthenticated MCPs (local mocks, dev), leave `NULL` / omit.

---

## Minimum viable MCP server

Holons talks to anything that responds to HTTP POST with a JSON-RPC 2.0
envelope. Minimum stdlib-only Python example:

```python
from http.server import BaseHTTPRequestHandler, HTTPServer
import json

TOOLS = [{
    "name": "get_inventory",
    "description": "Current stock on hand.",
    "inputSchema": {"type": "object", "properties": {}},
}]

def handle(method, params):
    if method == "initialize":
        return {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}},
                "serverInfo": {"name": "mcp-erp", "version": "1.0"}}
    if method == "tools/list":
        return {"tools": TOOLS}
    if method == "tools/call":
        if params["name"] == "get_inventory":
            return {"content": [{"type": "text", "text": '{"sku": "X", "qty": 42}'}],
                    "isError": False}
    raise ValueError("unknown method")

class H(BaseHTTPRequestHandler):
    def do_POST(self):
        msg = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        reply = {"jsonrpc": "2.0", "id": msg["id"]}
        try:
            reply["result"] = handle(msg["method"], msg.get("params"))
        except Exception as e:
            reply["error"] = {"code": -32603, "message": str(e)}
        body = json.dumps(reply).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body))); self.end_headers()
        self.wfile.write(body)

HTTPServer(("127.0.0.1", 8401), H).serve_forever()
```

Wire it to an agent via one of the two paths above, restart the
backend, and the agent gains `mcp__mcp-erp__get_inventory`.

A fully worked example with seven mock servers (ERP, mail, CRM,
market data, datacenter, accounting, Bedrock image generation) lives
in a sibling `Holons-demo` repo (not yet public) — the `base_mcp.py`
there handles the JSON-RPC envelope and lets each concrete mock register
tools with a one-liner:

```python
s = McpServer("mcp-erp")
s.register_tool("get_inventory",
                "Stock on hand and reorder points.",
                {"type": "object", "properties": {"sku": {"type": "string"}}},
                lambda args: {"sku": args.get("sku"), "qty": 42})
s.serve(port=8401)
```

---

## Operational tips

- **Tool results are flattened to text.** The LLM sees one string per
  tool call — the concatenation of every `type: "text"` block in the
  server's response. Non-text content (images, resources, binary) is
  silently dropped. Return JSON-as-text and let the model parse it.
- **Return size is capped at 5 MB** per response
  (`MAX_RESPONSE_BYTES` in `mcp_client.py`). Paginate large
  result sets.
- **Default timeout is 15 s.** Long-running operations should return
  a job id and offer a separate `poll_job` tool.
- **Unreachable servers degrade, not fail.** If `tools/list` throws,
  that server is dropped from the agent's catalog for this run and a
  warning is logged — the agent still runs with its remaining tools.
- **Tool choice stays with the LLM.** Holons passes every tool in
  the config to Bedrock; the model decides which to call and in what
  order. Don't rely on a particular dispatch sequence; instead,
  design tool descriptions that guide usage.

---

## Wiring checklist

When a tool shows up on a new MCP server and you want an agent to use
it:

1. Start the MCP server; verify `curl -X POST` of a `tools/list` RPC
   returns the expected catalog.
2. Pick a path (asset library or legacy) and insert the row.
3. Restart the backend (or, if you inserted via the API, the create
   endpoint already registers a worker).
4. Fire a test chat or schedule a trivial workflow step that says
   "call `mcp__<server>__<tool>` with arguments X and summarise".
5. Watch `/dashboard/agent_load` — the agent should pick up the task
   and complete it within seconds.
