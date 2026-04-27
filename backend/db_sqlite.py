"""SQLite database layer for personal/standalone mode.

Drop-in replacement for db_postgres.py — exposes the same public API
(`init`, `close`, `fetch_one`, `fetch_all`, `execute`, `execute_returning`,
`get_conn`, `txn_cursor`). Translates common Postgres-isms on the fly:

  - `%s` → `?` placeholders
  - `NOW()` → `datetime('now')`
  - `::jsonb`, `::text` casts → stripped
  - `RETURNING id` → use cursor.lastrowid
  - `ON CONFLICT ... DO UPDATE` → basic support
  - `FOR UPDATE SKIP LOCKED` → stripped (single-user, no contention)
  - JSONB `->>` operator → json_extract()
  - SERIAL/BIGSERIAL → INTEGER (SQLite auto-increment)

Limitations vs. Postgres:
  - No pgvector (RAG pgvector backend disabled)
  - No pg_trgm (ILIKE still works via SQLite LIKE)
  - No INTERVAL (replaced with manual date arithmetic)
  - JSONB stored as TEXT (json.dumps/loads at app layer)
"""
from __future__ import annotations

import json
import re
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

_db_path: str | None = None
_local = threading.local()


def init(database_url: str | None = None, **_kwargs) -> None:
    """Initialize SQLite. `database_url` should be a file path or
    `sqlite:///path/to/db.sqlite3`. If None, uses `~/.agent_company/data.db`."""
    global _db_path
    if database_url:
        _db_path = database_url.replace("sqlite:///", "").replace("sqlite://", "")
    else:
        data_dir = Path.home() / ".agent_company"
        data_dir.mkdir(parents=True, exist_ok=True)
        _db_path = str(data_dir / "data.db")
    # Probe
    conn = _get_conn()
    conn.execute("SELECT 1")
    # Apply schema
    from .schema_sqlite import create_all_sqlite
    create_all_sqlite(conn)
    conn.commit()


def close() -> None:
    conn = getattr(_local, "conn", None)
    if conn:
        conn.close()
        _local.conn = None


def _get_conn() -> sqlite3.Connection:
    """Get a thread-local SQLite connection."""
    conn = getattr(_local, "conn", None)
    if conn is None:
        if _db_path is None:
            init()
        try:
            conn = sqlite3.connect(_db_path, check_same_thread=False)
            conn.row_factory = _dict_factory
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
        except sqlite3.OperationalError:
            # DB corrupted (disk I/O error, stale WAL) — remove and recreate
            import logging
            logging.getLogger("agent_company.db_sqlite").warning(
                "SQLite DB corrupted, removing and recreating: %s", _db_path
            )
            for suffix in ("", "-wal", "-shm"):
                p = Path(str(_db_path) + suffix)
                if p.exists():
                    p.unlink()
            conn = sqlite3.connect(_db_path, check_same_thread=False)
            conn.row_factory = _dict_factory
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
        _local.conn = conn
    return conn


def _dict_factory(cursor, row):
    """Row factory that returns dict-like rows (matching psycopg dict_row)."""
    fields = [col[0] for col in cursor.description]
    return dict(zip(fields, row))


# ============================================================================
# SQL translation: Postgres → SQLite
# ============================================================================

def _translate_sql(sql: str, params: tuple | list | dict = ()) -> tuple[str, tuple]:
    """Translate Postgres SQL → SQLite, including parameter conversion.

    Returns (translated_sql, positional_params_tuple).
    Handles both positional (%s) and named (%(key)s) placeholders.
    """
    s = sql
    # Postgres `= ANY(%s::TYPE[])` → SQLite `IN (?, ?, …)` with the
    # corresponding positional list param flattened. Must run *before* the
    # cast-strip rule below, otherwise `::bigint` gets removed first and
    # leaves `ANY(%s[])` which the regex won't recognise.
    if isinstance(params, (list, tuple)):
        s, params = _expand_any_arrays(s, params)
    # Cast operators (::jsonb, ::text, etc.)
    s = re.sub(r"::\w+", "", s)
    # NOW() → datetime('now')
    s = s.replace("NOW()", "datetime('now')")
    s = s.replace("now()", "datetime('now')")
    # INTERVAL patterns
    s = re.sub(
        r"datetime\('now'\)\s*-\s*INTERVAL\s*'(\d+)\s*minutes?'",
        lambda m: f"datetime('now', '-{m.group(1)} minutes')",
        s, flags=re.IGNORECASE,
    )
    s = re.sub(
        r"datetime\('now'\)\s*\+\s*INTERVAL\s*'(\d+)\s*days?'",
        lambda m: f"datetime('now', '+{m.group(1)} days')",
        s, flags=re.IGNORECASE,
    )
    s = re.sub(
        r"datetime\('now'\)\s*-\s*INTERVAL\s*'(\d+)\s*days?'",
        lambda m: f"datetime('now', '-{m.group(1)} days')",
        s, flags=re.IGNORECASE,
    )
    # INTERVAL patterns for singular/plural hours
    s = re.sub(
        r"datetime\('now'\)\s*-\s*INTERVAL\s*'(\d+)\s*hours?'",
        lambda m: f"datetime('now', '-{m.group(1)} hours')",
        s, flags=re.IGNORECASE,
    )
    s = re.sub(
        r"datetime\('now'\)\s*-\s*INTERVAL\s*'(\d+)\s*months?'",
        lambda m: f"datetime('now', '-{m.group(1)} months')",
        s, flags=re.IGNORECASE,
    )
    # date_trunc('unit', expr) → SQLite date(expr, 'start of unit'). Only
    # day/month/year/hour line up cleanly. Week needs more care (SQLite
    # starts week on Sunday); left un-translated so it errors loudly if
    # anyone leans on it.
    s = re.sub(
        r"date_trunc\(\s*'(day|month|year|hour)'\s*,\s*([^)]+?)\s*\)",
        r"datetime(\2, 'start of \1')",
        s, flags=re.IGNORECASE,
    )
    # JSONB ->> 'key' → json_extract(col, '$.key'). Column reference may
    # include a table-qualifier prefix (e.g. `t.payload->>'kind'`), so allow
    # optional dot-segments. Without this, the previous `(\w+)` only ate
    # `payload`, leaving the `t.` outside as a stray identifier prefix and
    # producing the syntactically invalid `t.json_extract(payload, '$.kind')`.
    s = re.sub(r"(\w+(?:\.\w+)?)\s*->>\s*'(\w+)'", r"json_extract(\1, '$.\2')", s)
    # JSONB @> containment → instr() approximation
    # Handle dotted names like a.visible_user_ids @> %(uid_arr)s
    s = re.sub(r"([\w.]+)\s*@>\s*(\S+)", r"instr(\1, \2) > 0", s)
    # FOR UPDATE SKIP LOCKED → strip
    s = re.sub(r"\bFOR\s+UPDATE\s*(SKIP\s+LOCKED)?", "", s, flags=re.IGNORECASE)
    # ILIKE → LIKE
    s = s.replace(" ILIKE ", " LIKE ")
    # TRUNCATE → DELETE FROM
    s = re.sub(r"\bTRUNCATE\b", "DELETE FROM", s, flags=re.IGNORECASE)
    s = re.sub(r"\bRESTART\s+IDENTITY\s+CASCADE\b", "", s, flags=re.IGNORECASE)
    # Boolean: TRUE/FALSE → 1/0
    s = re.sub(r"\bTRUE\b", "1", s)
    s = re.sub(r"\bFALSE\b", "0", s)
    # GREATEST(a, b, ...) → MAX(a, b, ...). SQLite's MAX is overloaded —
    # scalar form when given multiple args, aggregate form when given a
    # column. Postgres has separate names; just rewrite the keyword.
    s = re.sub(r"\bGREATEST\b", "MAX", s, flags=re.IGNORECASE)
    s = re.sub(r"\bLEAST\b", "MIN", s, flags=re.IGNORECASE)

    # Handle named parameters: %(key)s → ? and collect values in order
    if isinstance(params, dict):
        ordered_values = []
        def _replace_named(m):
            key = m.group(1)
            val = params.get(key)
            if isinstance(val, (dict, list)):
                ordered_values.append(json.dumps(val))
            else:
                ordered_values.append(val)
            return "?"
        s = re.sub(r"%\((\w+)\)s", _replace_named, s)
        return s, tuple(ordered_values)

    # Handle positional %s → ?
    s = s.replace("%s", "?")
    positional = tuple(
        json.dumps(v) if isinstance(v, (dict, list)) else v
        for v in (params if isinstance(params, (list, tuple)) else ())
    )
    return s, positional


_ANY_RE = re.compile(r"=\s*ANY\s*\(\s*%s(?:::\w+\[\])?\s*\)", re.IGNORECASE)
_PH_RE = re.compile(r"%s")


def _expand_any_arrays(sql: str, params: tuple | list) -> tuple[str, tuple]:
    """Walk every `%s` in `sql`, paired with its positional param.
    Replace each `= ANY(%s)` (with or without an array cast) with an
    in-line `IN (?, ?, …)` of the right cardinality, flattening the
    corresponding list param. Plain `%s` tokens pass through unchanged.

    Empty arrays become `IN (NULL)` — never matches, which is the same
    result Postgres gives for `= ANY(ARRAY[]::bigint[])`.
    """
    out_chunks: list[str] = []
    out_params: list = []
    pos = 0
    pi = 0
    while pos < len(sql):
        m_any = _ANY_RE.search(sql, pos)
        m_ph = _PH_RE.search(sql, pos)
        if not m_ph:
            out_chunks.append(sql[pos:])
            break
        if m_any and m_any.start() <= m_ph.start() < m_any.end():
            out_chunks.append(sql[pos:m_any.start()])
            arr = params[pi] if pi < len(params) else None
            if isinstance(arr, (list, tuple)) and len(arr) > 0:
                out_chunks.append(" IN (" + ",".join(["%s"] * len(arr)) + ")")
                out_params.extend(arr)
            else:
                out_chunks.append(" IN (NULL)")
            pos = m_any.end()
            pi += 1
        else:
            out_chunks.append(sql[pos:m_ph.end()])
            if pi < len(params):
                out_params.append(params[pi])
            pos = m_ph.end()
            pi += 1
    # Tail: copy any remaining positional params (none if walking was clean)
    while pi < len(params):
        out_params.append(params[pi])
        pi += 1
    return "".join(out_chunks), tuple(out_params)


# ============================================================================
# Public API (matches db_postgres.py)
# ============================================================================

def fetch_one(sql: str, params: tuple | list | dict = ()) -> dict | None:
    conn = _get_conn()
    translated, positional = _translate_sql(sql, params)
    cur = conn.execute(translated, positional)
    row = cur.fetchone()
    return _postprocess_row(row) if row else None


def fetch_all(sql: str, params: tuple | list | dict = ()) -> list[dict]:
    conn = _get_conn()
    translated, positional = _translate_sql(sql, params)
    cur = conn.execute(translated, positional)
    return [_postprocess_row(r) for r in cur.fetchall()]


def execute(sql: str, params: tuple | list | dict = ()) -> int:
    conn = _get_conn()
    translated, positional = _translate_sql(sql, params)
    cur = conn.execute(translated, positional)
    conn.commit()
    return cur.rowcount


def execute_returning(sql: str, params: tuple | list | dict = (), col: str = "id") -> Any:
    """For INSERT ... RETURNING: SQLite doesn't support RETURNING, so we
    strip it and use cursor.lastrowid."""
    translated, positional = _translate_sql(sql, params)
    # Strip RETURNING clause
    returning_match = re.search(r"\bRETURNING\s+\w+", translated, re.IGNORECASE)
    if returning_match:
        translated = translated[:returning_match.start()].strip()
    conn = _get_conn()
    cur = conn.execute(translated, positional)
    conn.commit()
    return cur.lastrowid


class _TranslatingConnection:
    """Wrapper around sqlite3.Connection that auto-translates SQL.
    Used by get_conn() so code that does `conn.cursor(); cur.execute()`
    gets translation for free."""
    def __init__(self, conn):
        self._conn = conn

    def cursor(self):
        return _TranslatingCursor(self._conn)

    def execute(self, sql, params=()):
        translated, positional = _translate_sql(sql, params)
        return self._conn.execute(translated, positional)

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()


@contextmanager
def get_conn():
    """Context manager yielding a translation-wrapping connection."""
    conn = _get_conn()
    try:
        yield _TranslatingConnection(conn)
        conn.commit()
    except Exception:
        conn.rollback()
        raise


class _TranslatingCursor:
    """Wrapper around sqlite3.Cursor that auto-translates Postgres SQL."""
    def __init__(self, conn):
        self._conn = conn
        self._cur = conn.cursor()
        self.description = None

    # psycopg cursors are usable as context managers
    # (`with conn.cursor() as cur:`). Engine code relies on that idiom, so
    # mirror it here — without these, SQLite backend explodes the moment
    # any module tries `with cur:`.
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            self._cur.close()
        except Exception:
            pass
        return False

    def execute(self, sql, params=()):
        translated, positional = _translate_sql(sql, params)
        self._cur.execute(translated, positional)
        self.description = self._cur.description
        return self._cur

    def fetchone(self):
        row = self._cur.fetchone()
        return _postprocess_row(row) if row else None

    def fetchall(self):
        return [_postprocess_row(r) for r in self._cur.fetchall()]

    @property
    def rowcount(self):
        return self._cur.rowcount

    @property
    def lastrowid(self):
        return self._cur.lastrowid


@contextmanager
def txn_cursor():
    """Yield (conn, cur) for compatibility with queue.py locking queries.
    The cursor auto-translates Postgres SQL to SQLite."""
    conn = _get_conn()
    try:
        cur = _TranslatingCursor(conn)
        yield conn, cur
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def pool():
    """Compatibility stub — SQLite has no pool."""
    return None


def _postprocess_row(row: dict) -> dict:
    """Parse JSON string columns back to dicts/lists where applicable."""
    if not row:
        return row
    out = dict(row)
    for k, v in out.items():
        if isinstance(v, str) and v.startswith(("{", "[")):
            try:
                out[k] = json.loads(v)
            except (json.JSONDecodeError, ValueError):
                pass
    return out
