"""run_code — execute a short script in the agent's workspace.

The runner (local subprocess, docker, or disabled) is selected at
backend startup via CODE_EXECUTION_BACKEND env. Personal-mode users
additionally need `enable_code_execution=TRUE` on their as_users row —
this per-user toggle is flipped from Settings → Personal with a warning
modal.

The script runs with cwd = the workspace root, so relative file paths
(`open('app.py')`) resolve against files the agent already wrote via
file_write. stdout / stderr / exit_code are returned; the LLM can then
iterate based on test output.
"""
from __future__ import annotations

from pathlib import Path

from . import register
from ..services import workspaces as ws
from ..services.code_runners import get_runner, _resolve_timeout, LANGS
from .. import db


SPEC = {
    "name": "run_code",
    "description": (
        "Execute a short script inside the current workspace. Supported "
        "languages: python, node, bash, sh. Network is denied; output is "
        "truncated if enormous. Returns {ok, stdout, stderr, exit_code, "
        "duration_ms}. Use this to run tests, quick scripts, or format "
        "checks — not for long-running servers."
    ),
    "inputSchema": {
        "json": {
            "type": "object",
            "properties": {
                "lang": {
                    "type": "string",
                    "enum": list(LANGS.keys()),
                    "description": "Language / interpreter.",
                },
                "code": {
                    "type": "string",
                    "description": "Source to execute.",
                },
                "timeout_s": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "Seconds before we kill the process (default 30, max 120).",
                },
            },
            "required": ["lang", "code"],
        },
    },
}


def _user_allows_code_exec(user_id: int | None) -> bool:
    if not user_id:
        return False
    row = db.fetch_one(
        "SELECT enable_code_execution FROM as_users WHERE id = %s", (user_id,),
    )
    return bool((row or {}).get("enable_code_execution"))


def handler(args: dict, ctx: dict) -> dict:
    workspace = ws.resolve_for_task(ctx or {})
    if not workspace:
        return {"ok": False, "error": "No workspace bound to this task. "
                "Bind a workspace before using run_code."}

    user_id = (ctx or {}).get("agent_user_id")
    if not _user_allows_code_exec(user_id):
        return {
            "ok": False,
            "error": "Code execution is disabled for this user. "
                     "Enable it in Settings → Personal (requires acknowledging the warning).",
        }

    lang = (args.get("lang") or "").strip().lower()
    code = args.get("code") or ""
    timeout_s = _resolve_timeout(args.get("timeout_s"))

    cwd = Path(workspace["storage_path"])
    runner = get_runner()
    result = runner.run(lang=lang, code=code, cwd=cwd, timeout_s=timeout_s)
    # Any file changes from the run may have bumped workspace size.
    try:
        ws._recompute_size(workspace["id"])
    except Exception:
        pass
    return dict(result)


register("run_code", SPEC, handler)
