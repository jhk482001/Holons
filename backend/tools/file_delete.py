"""file_delete — remove a file or directory from the workspace."""
from __future__ import annotations

from . import register
from ..services import workspaces as ws


SPEC = {
    "name": "file_delete",
    "description": (
        "Delete a file or directory in the current workspace. Destructive; "
        "use sparingly. Returns {ok, existed}."
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
        existed = ws.delete_file(workspace, path)
        return {"ok": True, "existed": existed, "path": path}
    except ws.PathEscape as e:
        return {"ok": False, "error": f"path rejected: {e}"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}


register("file_delete", SPEC, handler)
