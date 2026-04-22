"""file_glob — find files in the workspace by glob pattern."""
from __future__ import annotations

from . import register
from ..services import workspaces as ws


SPEC = {
    "name": "file_glob",
    "description": (
        "Find files in the current workspace by glob pattern (e.g. "
        "'src/**/*.ts', 'tests/*.py'). Returns {ok, paths: [str]}. "
        "Files only, not directories."
    ),
    "inputSchema": {
        "json": {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Glob pattern (uses pathlib.Path.glob semantics).",
                },
            },
            "required": ["pattern"],
        },
    },
}


def handler(args: dict, ctx: dict) -> dict:
    workspace = ws.resolve_for_task(ctx or {})
    if not workspace:
        return {"ok": False, "error": "No workspace bound to this task."}
    pattern = (args.get("pattern") or "").strip() or "*"
    try:
        return {"ok": True, "paths": ws.glob(workspace, pattern)}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}


register("file_glob", SPEC, handler)
