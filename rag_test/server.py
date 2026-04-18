"""Minimal RAG validation service — POST /ingest + POST /search.

Isolated from agent_company's main DB by using its own dedicated
schema `rag_test` inside the same Postgres instance. That means no new
container to stand up, while still keeping test documents out of the
production `rag_documents` table.

Embeddings are a deterministic keyword-bag — **not** real Bedrock Titan
calls — so this rig runs without AWS credentials. This makes it a
pure-local plumbing smoke rig: you can verify ingest → search → result
surface without burning tokens.

Protocol:

    POST /ingest
      { "source_name": str, "text": str }
      → { "chunks_ingested": int }

    POST /search
      { "query": str, "top_k": int }
      → { "hits": [{"content": str, "score": float, "source_name": str}] }

    DELETE /docs
      → { "deleted": int }

Run with::

    python3 -m rag_test.server    # from agent_company/

Port defaults to 8191. Schema lazy-init happens on first request.
"""
from __future__ import annotations

import json
import logging
import math
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# Reuse agent_company's psycopg connection config, but point at a
# separate schema so tests don't collide with production rag_documents.
from backend import db as _db  # type: ignore

logging.basicConfig(
    level=logging.INFO,
    format="[rag_test] %(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("rag_test")

PORT = int(os.environ.get("RAG_TEST_PORT", "8191"))

EMBED_DIM = 32  # toy fixed-dim vectors, enough for bag-of-keywords matching

_SCHEMA_READY = False


def _ensure_schema() -> None:
    """Create the rag_test schema + docs table on first use. Idempotent."""
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    _db.init()
    with _db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
            cur.execute("CREATE SCHEMA IF NOT EXISTS rag_test")
            cur.execute(
                f"""
                CREATE TABLE IF NOT EXISTS rag_test.docs (
                    id          BIGSERIAL PRIMARY KEY,
                    source_name TEXT NOT NULL,
                    chunk_index INT NOT NULL,
                    content     TEXT NOT NULL,
                    embedding   vector({EMBED_DIM}),
                    created_at  TIMESTAMPTZ DEFAULT NOW()
                )
                """
            )
    _SCHEMA_READY = True
    log.info("rag_test schema ready (dim=%s)", EMBED_DIM)


# ============================================================================
# Toy embedding — deterministic and offline-safe
# ============================================================================

_KEYWORDS = [
    "foo", "bar", "baz", "alpha", "beta", "gamma",
    "delta", "epsilon", "zeta", "eta",
    "hello", "world", "test", "data", "vector",
    "search", "index", "query", "document", "chunk",
    "score", "retrieve", "embed", "rag", "ai",
    "model", "token", "agent", "task", "result",
    "bedrock", "pinecone",
]
assert len(_KEYWORDS) == EMBED_DIM


def embed(text: str) -> list[float]:
    """Bag-of-keywords embedding. Normalized to unit length so cosine
    similarity == dot product."""
    lower = (text or "").lower()
    vec = [float(lower.count(kw)) for kw in _KEYWORDS]
    mag = math.sqrt(sum(v * v for v in vec))
    if mag > 0:
        vec = [v / mag for v in vec]
    # Tiny stable non-zero so pure-zero docs don't break cosine
    vec[-1] = vec[-1] or 0.001
    return vec


def _vec_literal(vec: list[float]) -> str:
    return "[" + ",".join(f"{v:.6f}" for v in vec) + "]"


def chunk_text(text: str, size: int = 400) -> list[str]:
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= size:
        return [text]
    out: list[str] = []
    pos = 0
    while pos < len(text):
        out.append(text[pos:pos + size])
        pos += size
    return out


# ============================================================================
# Ingest + search
# ============================================================================

def ingest(source_name: str, text: str) -> int:
    _ensure_schema()
    chunks = chunk_text(text)
    if not chunks:
        return 0
    with _db.get_conn() as conn:
        with conn.cursor() as cur:
            for i, chunk in enumerate(chunks):
                cur.execute(
                    """
                    INSERT INTO rag_test.docs
                        (source_name, chunk_index, content, embedding)
                    VALUES (%s, %s, %s, %s::vector)
                    """,
                    (source_name, i, chunk, _vec_literal(embed(chunk))),
                )
    return len(chunks)


def search(query: str, top_k: int = 5) -> list[dict]:
    _ensure_schema()
    vec = embed(query)
    rows = _db.fetch_all(
        """
        SELECT id, source_name, chunk_index, content,
               1 - (embedding <=> %s::vector) AS score
        FROM rag_test.docs
        ORDER BY embedding <=> %s::vector
        LIMIT %s
        """,
        (_vec_literal(vec), _vec_literal(vec), top_k),
    )
    return rows


def delete_all() -> int:
    _ensure_schema()
    with _db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM rag_test.docs")
            return cur.rowcount


# ============================================================================
# HTTP plumbing
# ============================================================================

class Handler(BaseHTTPRequestHandler):
    def _send(self, payload, status=200):
        body = json.dumps(payload, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, DELETE, GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def _read_json(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            return json.loads(raw or b"{}")
        except json.JSONDecodeError:
            return None

    def do_GET(self):
        if self.path == "/":
            _ensure_schema()
            n = _db.fetch_one("SELECT COUNT(*) AS c FROM rag_test.docs")["c"]
            self._send({
                "service": "rag_test",
                "docs": int(n),
                "embed_dim": EMBED_DIM,
            })
            return
        self._send({"error": "not found"}, status=404)

    def do_POST(self):
        data = self._read_json()
        if data is None:
            self._send({"error": "bad json"}, status=400)
            return
        if self.path == "/ingest":
            src = data.get("source_name") or "untitled"
            txt = data.get("text") or ""
            try:
                n = ingest(src, txt)
                self._send({"chunks_ingested": n})
            except Exception as e:  # noqa: BLE001
                log.exception("ingest failed")
                self._send({"error": str(e)}, status=500)
            return
        if self.path == "/search":
            q = data.get("query") or ""
            top_k = int(data.get("top_k") or 5)
            try:
                hits = search(q, top_k)
                self._send({"hits": hits})
            except Exception as e:  # noqa: BLE001
                log.exception("search failed")
                self._send({"error": str(e)}, status=500)
            return
        self._send({"error": "not found"}, status=404)

    def do_DELETE(self):
        if self.path == "/docs":
            try:
                n = delete_all()
                self._send({"deleted": n})
            except Exception as e:  # noqa: BLE001
                self._send({"error": str(e)}, status=500)
            return
        self._send({"error": "not found"}, status=404)

    def log_message(self, format: str, *args) -> None:  # noqa: A002
        log.info("%s - %s", self.client_address[0], format % args)


def main():
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    log.info("rag_test server listening on http://localhost:%s", PORT)
    log.info("using schema rag_test.docs inside the agent_company Postgres")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()
        sys.exit(0)


if __name__ == "__main__":
    main()
