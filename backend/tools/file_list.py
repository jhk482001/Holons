"""file_list — list every file in the workspace with size + mtime."""
from __future__ import annotations

from . import register
from ..services import workspaces as ws


SPEC = {
    "name": "file_list",
    "description": (
        "List all files in the current workspace. Returns "
        "{ok, files: [{path, size, mtime, is_dir}]}. Use this to "
        "discover what the previous agent wrote."
    ),
    "inputSchema": {
        "json": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
}


def handler(args: dict, ctx: dict) -> dict:
    workspace = ws.resolve_for_task(ctx or {})
    if not workspace:
        return {"ok": False, "error": "No workspace bound to this task."}
    try:
        return {"ok": True, "files": ws.list_tree(workspace)}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}


register("file_list", SPEC, handler)
