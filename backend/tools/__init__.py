"""Built-in tool registry for tool-using agents.

Each tool is a small Python module exposing:

    SPEC = {
        "name": "search_skills",
        "description": "...",
        "inputSchema": {  # JSON Schema, Converse API shape
            "json": {
                "type": "object",
                "properties": {...},
                "required": [...],
            }
        },
    }

    def handler(args: dict, ctx: dict) -> dict:
        '''Return a JSON-serialisable dict. ctx carries agent_id / run_id / etc.'''
        ...

`get_specs(allowed_names)` → list of toolSpec dicts for the LLM toolConfig.
`call_tool(name, input, ctx)` → run the handler and return its output.

Stage 1 ships the registry machinery with no real tools. Stage 2 populates
it with `search_skills`, `http_get`, `current_time`.
"""
from __future__ import annotations

import importlib
import pkgutil
from typing import Any, Callable


_REGISTRY: dict[str, tuple[dict, Callable[[dict, dict], Any]]] = {}


def register(name: str, spec: dict, handler: Callable[[dict, dict], Any]) -> None:
    _REGISTRY[name] = (spec, handler)


def _discover() -> None:
    """Import every sibling module so its top-level `register()` call runs."""
    import backend.tools as pkg
    for m in pkgutil.iter_modules(pkg.__path__):
        if m.name.startswith("_"):
            continue
        try:
            importlib.import_module(f"backend.tools.{m.name}")
        except Exception:  # noqa: BLE001
            # A broken tool shouldn't prevent the rest from loading
            import logging
            logging.getLogger("agent_company.tools").exception(
                "failed to import tool module %s", m.name,
            )


_discovered = False


def _ensure_discovered() -> None:
    global _discovered
    if not _discovered:
        _discover()
        _discovered = True


def get_specs(allowed_names: list[str]) -> list[dict]:
    """Return toolSpec dicts for the LLM toolConfig, filtered by allowlist.

    Unknown names are silently dropped. The returned dicts are in the
    Bedrock Converse API shape:
      {name, description, inputSchema: {json: {...}}}
    """
    _ensure_discovered()
    out = []
    for name in allowed_names:
        entry = _REGISTRY.get(name)
        if not entry:
            continue
        spec, _ = entry
        out.append(spec)
    return out


def call_tool(name: str, args: dict, ctx: dict) -> Any:
    _ensure_discovered()
    entry = _REGISTRY.get(name)
    if not entry:
        raise ValueError(f"unknown tool: {name}")
    _, handler = entry
    return handler(args or {}, ctx or {})


def all_tool_names() -> list[str]:
    _ensure_discovered()
    return sorted(_REGISTRY.keys())


def describe_all() -> list[dict]:
    """Return {name, description, input_schema} for every registered tool.
    Used by the settings UI to render the tool picker."""
    _ensure_discovered()
    out = []
    for name in sorted(_REGISTRY.keys()):
        spec, _ = _REGISTRY[name]
        out.append({
            "name": spec.get("name", name),
            "description": spec.get("description", ""),
            "input_schema": spec.get("inputSchema", {}).get("json", {}),
        })
    return out
