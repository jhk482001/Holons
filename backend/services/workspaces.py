"""Workspace service — scratchpad filesystem for agents.

Each workspace maps 1:1 to a directory on the backend host. Agents
write / read files via the file_* tools; the web UI browses the tree
via the /api/workspaces/... endpoints.

Storage path strategy:
  - Personal mode (SQLite, standalone binary): ~/.agent_company/workspaces/
  - Enterprise mode (Postgres):                 /var/lib/holons/workspaces/
Override with env HOLONS_WORKSPACE_ROOT.

Each workspace gets its own subdir <root>/<user_id>/<workspace_id>/.
All tool calls are clamped to that subdir via _safe_join() — any path
that realpaths outside is rejected.
"""
from __future__ import annotations

import io
import os
import shutil
import zipfile
from pathlib import Path

from .. import db


# ----------------------------------------------------------------------
# Storage root
# ----------------------------------------------------------------------

def _default_root() -> str:
    env = os.environ.get("HOLONS_WORKSPACE_ROOT")
    if env:
        return env
    # Prefer personal-mode path if the sibling data.db directory exists.
    home = Path.home() / ".agent_company" / "workspaces"
    if (Path.home() / ".agent_company").exists():
        return str(home)
    return "/var/lib/holons/workspaces"


def storage_root() -> Path:
    root = Path(_default_root())
    root.mkdir(parents=True, exist_ok=True)
    return root


def _workspace_dir(user_id: int, workspace_id: int) -> Path:
    return storage_root() / str(user_id) / str(workspace_id)


# ----------------------------------------------------------------------
# Path safety — every file tool funnels through _safe_join so a rogue
# prompt that calls file_write("../../etc/passwd", ...) bounces.
# ----------------------------------------------------------------------

class PathEscape(ValueError):
    """Raised when a user-provided relative path escapes the workspace."""


def _safe_join(workspace_root: Path, relpath: str) -> Path:
    """Resolve `relpath` under `workspace_root`, refusing anything that
    leaves the root via ../ or absolute paths."""
    if not isinstance(relpath, str) or not relpath:
        raise PathEscape("path must be a non-empty string")
    p = (workspace_root / relpath).resolve()
    root_resolved = workspace_root.resolve()
    try:
        p.relative_to(root_resolved)
    except ValueError:
        raise PathEscape(f"path {relpath!r} escapes workspace")
    return p


# ----------------------------------------------------------------------
# CRUD
# ----------------------------------------------------------------------

def create(user_id: int, name: str, project_id: int | None = None,
            description: str | None = None) -> dict:
    wid = db.execute_returning(
        """
        INSERT INTO workspaces (user_id, project_id, name, description, storage_path)
        VALUES (%s, %s, %s, %s, '') RETURNING id
        """,
        (user_id, project_id, name, description),
    )
    wdir = _workspace_dir(user_id, wid)
    wdir.mkdir(parents=True, exist_ok=True)
    # Stamp the real path so a future move of HOLONS_WORKSPACE_ROOT
    # doesn't silently orphan the row.
    db.execute(
        "UPDATE workspaces SET storage_path = %s WHERE id = %s",
        (str(wdir), wid),
    )
    return get(wid, user_id)  # type: ignore


def get(workspace_id: int, user_id: int) -> dict | None:
    row = db.fetch_one(
        "SELECT * FROM workspaces WHERE id = %s AND user_id = %s",
        (workspace_id, user_id),
    )
    if not row:
        return None
    return row


def list_for_user(user_id: int, project_id: int | None = None) -> list[dict]:
    if project_id is not None:
        return db.fetch_all(
            "SELECT * FROM workspaces WHERE user_id = %s AND project_id = %s "
            "ORDER BY updated_at DESC",
            (user_id, project_id),
        )
    return db.fetch_all(
        "SELECT * FROM workspaces WHERE user_id = %s ORDER BY updated_at DESC",
        (user_id,),
    )


def delete(workspace_id: int, user_id: int) -> bool:
    row = get(workspace_id, user_id)
    if not row:
        return False
    wdir = Path(row["storage_path"])
    try:
        if wdir.exists():
            shutil.rmtree(wdir)
    except Exception:
        pass
    # FK `agent_tasks.workspace_id ON DELETE SET NULL` can deadlock with
    # concurrent task inserts. Retry a couple of times before giving up;
    # each attempt is a fresh transaction.
    import time as _time
    last = None
    for attempt in range(3):
        try:
            db.execute(
                "DELETE FROM workspaces WHERE id = %s AND user_id = %s",
                (workspace_id, user_id),
            )
            return True
        except Exception as e:  # noqa: BLE001
            last = e
            _time.sleep(0.1 * (attempt + 1))
    if last is not None:
        raise last
    return True


# ----------------------------------------------------------------------
# File operations — called both by the built-in tools (agent-side) and
# by the /api/workspaces/:id/files/... HTTP endpoints (UI-side).
# ----------------------------------------------------------------------

MAX_FILE_BYTES = 10 * 1024 * 1024  # 10 MiB


def _root_for(workspace: dict) -> Path:
    p = Path(workspace["storage_path"])
    p.mkdir(parents=True, exist_ok=True)
    return p


def write_file(workspace: dict, relpath: str, content: str | bytes,
               encoding: str = "utf-8") -> dict:
    root = _root_for(workspace)
    target = _safe_join(root, relpath)
    target.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, str):
        data = content.encode(encoding)
    else:
        data = content
    if len(data) > MAX_FILE_BYTES:
        raise ValueError(f"file too large ({len(data)} bytes; cap {MAX_FILE_BYTES})")
    target.write_bytes(data)
    _recompute_size(workspace["id"])
    return {"path": relpath, "size": len(data)}


def read_file(workspace: dict, relpath: str,
              as_bytes: bool = False) -> str | bytes:
    root = _root_for(workspace)
    target = _safe_join(root, relpath)
    if not target.exists() or not target.is_file():
        raise FileNotFoundError(relpath)
    data = target.read_bytes()
    if as_bytes:
        return data
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("utf-8", errors="replace")


def delete_file(workspace: dict, relpath: str) -> bool:
    root = _root_for(workspace)
    target = _safe_join(root, relpath)
    if not target.exists():
        return False
    if target.is_dir():
        shutil.rmtree(target)
    else:
        target.unlink()
    _recompute_size(workspace["id"])
    return True


def list_tree(workspace: dict) -> list[dict]:
    """Flat listing of every file under the workspace root. Each entry:
    {path, size, mtime, is_dir}."""
    root = _root_for(workspace)
    out: list[dict] = []
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root)
        try:
            stat = p.stat()
        except OSError:
            continue
        out.append({
            "path": str(rel),
            "size": stat.st_size if p.is_file() else 0,
            "mtime": stat.st_mtime,
            "is_dir": p.is_dir(),
        })
    return out


def glob(workspace: dict, pattern: str) -> list[str]:
    root = _root_for(workspace)
    return sorted(
        str(p.relative_to(root))
        for p in root.glob(pattern)
        if p.is_file()
    )


def _recompute_size(workspace_id: int) -> None:
    row = db.fetch_one(
        "SELECT storage_path FROM workspaces WHERE id = %s", (workspace_id,),
    )
    if not row:
        return
    total = 0
    try:
        for p in Path(row["storage_path"]).rglob("*"):
            if p.is_file():
                total += p.stat().st_size
    except OSError:
        pass
    db.execute(
        "UPDATE workspaces SET size_bytes = %s, updated_at = NOW() WHERE id = %s",
        (total, workspace_id),
    )


# ----------------------------------------------------------------------
# Zip export
# ----------------------------------------------------------------------

def zip_bytes(workspace: dict) -> bytes:
    root = _root_for(workspace)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in root.rglob("*"):
            if p.is_file():
                zf.write(p, arcname=str(p.relative_to(root)))
    return buf.getvalue()


# ----------------------------------------------------------------------
# Resolve a workspace-id referenced from a task payload. The task
# payload carries workspace_id; if the owning agent's user matches the
# workspace owner, the tool can use it. Otherwise the tool returns an
# error (dead-letters back through the engine).
# ----------------------------------------------------------------------

def resolve_for_task(task: dict) -> dict | None:
    """Look up the workspace bound to this task. Returns the workspace
    row, or None if unbound / mismatched."""
    wid = task.get("workspace_id")
    if not wid:
        payload = task.get("payload") or {}
        if isinstance(payload, str):
            import json as _json
            try:
                payload = _json.loads(payload)
            except Exception:
                payload = {}
        wid = payload.get("workspace_id")
    if not wid:
        # Fall back to the run's workspace binding.
        run_id = task.get("run_id")
        if run_id:
            row = db.fetch_one(
                "SELECT workspace_id FROM runs WHERE id = %s", (run_id,),
            )
            wid = (row or {}).get("workspace_id")
    if not wid:
        return None
    agent_id = task.get("agent_id")
    if not agent_id:
        return None
    owner = db.fetch_one(
        "SELECT user_id FROM agents WHERE id = %s", (agent_id,),
    )
    if not owner:
        return None
    ws = db.fetch_one(
        "SELECT * FROM workspaces WHERE id = %s AND user_id = %s",
        (int(wid), owner["user_id"]),
    )
    return ws
