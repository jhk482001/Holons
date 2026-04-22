"""file_write — create or overwrite a file in the agent's workspace.

Safe-ish: bounded to the workspace directory via realpath clamping, with
a 10 MiB per-file cap (configurable in workspaces.MAX_FILE_BYTES). The
file is immediately visible to sibling tools (file_read / file_list)
and to the UI workspace browser.

Returns {ok, path, size, error?}.
"""
from __future__ import annotations

from . import register
from ..services import workspaces as ws


SPEC = {
    "name": "file_write",
    "description": (
        "Create or overwrite a file in the current workspace. The path is "
        "relative to the workspace root; '..' is rejected. Use this to "
        "produce code files, configs, specs, or intermediate artifacts for "
        "the next agent to read."
    ),
    "inputSchema": {
        "json": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Relative path inside the workspace (e.g. 'src/app.py').",
                },
                "content": {
                    "type": "string",
                    "description": "File contents (UTF-8 text).",
                },
            },
            "required": ["path", "content"],
        },
    },
}


def handler(args: dict, ctx: dict) -> dict:
    workspace = ws.resolve_for_task(ctx or {})
    if not workspace:
        return {"ok": False, "error": "No workspace bound to this task. "
                "Ask the user to attach a workspace before calling file_write."}
    path = (args.get("path") or "").strip()
    content = args.get("content") or ""
    try:
        result = ws.write_file(workspace, path, content)
        return {"ok": True, **result}
    except ws.PathEscape as e:
        return {"ok": False, "error": f"path rejected: {e}"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}


register("file_write", SPEC, handler)
