"""file_read — read a file from the agent's workspace as UTF-8 text."""
from __future__ import annotations

from . import register
from ..services import workspaces as ws


SPEC = {
    "name": "file_read",
    "description": (
        "Read a UTF-8 text file from the current workspace. Returns "
        "{ok, path, content} on success. Use this to inspect files the "
        "previous agent wrote, or to re-read a spec you saved earlier."
    ),
    "inputSchema": {
        "json": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Relative path inside the workspace.",
                },
            },
            "required": ["path"],
        },
    },
}


def handler(args: dict, ctx: dict) -> dict:
    workspace = ws.resolve_for_task(ctx or {})
    if not workspace:
        return {"ok": False, "error": "No workspace bound to this task."}
    path = (args.get("path") or "").strip()
    try:
        content = ws.read_file(workspace, path)
        return {"ok": True, "path": path, "content": content}
    except ws.PathEscape as e:
        return {"ok": False, "error": f"path rejected: {e}"}
    except FileNotFoundError:
        return {"ok": False, "error": f"not found: {path}"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}


register("file_read", SPEC, handler)
