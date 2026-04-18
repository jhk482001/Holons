"""RAG pipeline — local pgvector backend.

Supports one asset kind (`kind='rag'`) with three configurable backends in
`asset_items.config.backend`:

  * `pgvector` (this module): chunks → Bedrock Titan embeddings v2 → local
    pgvector column in `rag_documents`. Retrieval uses cosine similarity
    via the ivfflat index.
  * `bedrock_kb` / `pinecone` (Phase 2.3): delegate to external services.

The `ingest_text` / `search` functions dispatch to the right backend based
on `asset.config.backend`.

Public API::

    rag.ingest_text(asset, source_name, text)  -> int  # chunks ingested
    rag.search(asset, query, top_k=5)          -> list[dict]  # hits

Each chunk is stored with its embedding; retrieval returns
`[{id, source_name, chunk_index, content, score, metadata}]` sorted by
score (cosine similarity, 1 = identical, 0 = orthogonal).

Embedding model: Titan Embeddings v2 — 1024 dims, ~8k token input limit.
We chunk at ~1000 chars (roughly ~250 tokens) with a 100-char overlap so
nothing exceeds the model's limit by a wide margin.
"""
from __future__ import annotations

import json
import logging

from .. import db
from .. import bedrock_client

log = logging.getLogger("agent_company.rag")

TITAN_EMBED_MODEL = "amazon.titan-embed-text-v2:0"
EMBED_DIM = 1024  # must match schema.rag_documents.embedding vector(N)

CHUNK_SIZE = 1000
CHUNK_OVERLAP = 100


# ============================================================================
# Chunking
# ============================================================================

def chunk_text(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Split a long document into overlapping char-based chunks.

    Boundary-aware: tries to break on a newline within the last 100 chars
    so chunks don't cut mid-sentence. Falls back to hard cuts when no
    newline is available.
    """
    if not text:
        return []
    text = text.strip()
    if not text:
        return []
    if len(text) <= size:
        return [text]

    chunks: list[str] = []
    pos = 0
    while pos < len(text):
        end = min(pos + size, len(text))
        if end < len(text):
            # Prefer breaking at a paragraph / sentence boundary in the
            # last 100 chars of the window.
            window = text[end - 100:end]
            for sep in ("\n\n", "\n", "。", ". ", "! ", "? "):
                idx = window.rfind(sep)
                if idx != -1:
                    end = end - 100 + idx + len(sep)
                    break
        chunk = text[pos:end].strip()
        if chunk:
            chunks.append(chunk)
        pos = end - overlap if end < len(text) else end
    return chunks


# ============================================================================
# Bedrock Titan embeddings
# ============================================================================

def embed_one(text: str) -> list[float]:
    """Return a 1024-dim embedding for a single text. Raises on Bedrock
    errors so the caller can surface them — rag ingestion should fail
    loudly if the embedding model is down."""
    body = json.dumps({
        "inputText": text,
        "dimensions": EMBED_DIM,
        "normalize": True,
    })
    resp = bedrock_client.runtime().invoke_model(
        modelId=TITAN_EMBED_MODEL,
        body=body,
        contentType="application/json",
        accept="application/json",
    )
    parsed = json.loads(resp["body"].read())
    vec = parsed.get("embedding") or []
    if len(vec) != EMBED_DIM:
        raise RuntimeError(
            f"Titan embed returned {len(vec)} dims, expected {EMBED_DIM}"
        )
    return vec


def embed_batch(texts: list[str]) -> list[list[float]]:
    """Embed a list of texts sequentially. Titan Embeddings v2 doesn't
    expose a true batch endpoint via invoke_model, so this loops. The
    call is rate-limited by Bedrock so huge batches will naturally slow."""
    return [embed_one(t) for t in texts]


# ============================================================================
# Backend dispatch
# ============================================================================

def _backend_of(asset: dict) -> str:
    cfg = asset.get("config") or {}
    return (cfg.get("backend") or "pgvector").lower()


def ingest_text(
    asset: dict,
    source_name: str,
    text: str,
    *,
    metadata: dict | None = None,
) -> int:
    """Chunk, embed, and persist a document. Returns number of chunks
    ingested. Dispatches to the backend declared in the asset config."""
    backend = _backend_of(asset)
    if backend == "pgvector":
        return _ingest_pgvector(asset, source_name, text, metadata)
    if backend in ("bedrock_kb", "pinecone"):
        from . import rag_external
        return rag_external.ingest_text(asset, source_name, text, metadata)
    raise ValueError(f"unsupported RAG backend {backend!r}")


def search(asset: dict, query: str, top_k: int = 5) -> list[dict]:
    """Retrieve the top-K chunks most similar to the query."""
    backend = _backend_of(asset)
    if backend == "pgvector":
        return _search_pgvector(asset, query, top_k)
    if backend in ("bedrock_kb", "pinecone"):
        from . import rag_external
        return rag_external.search(asset, query, top_k)
    raise ValueError(f"unsupported RAG backend {backend!r}")


# ============================================================================
# Local pgvector backend
# ============================================================================

def _vec_literal(vec: list[float]) -> str:
    """pgvector accepts a string '[x,y,z]' for explicit vector input."""
    return "[" + ",".join(f"{v:.6f}" for v in vec) + "]"


def _ingest_pgvector(
    asset: dict,
    source_name: str,
    text: str,
    metadata: dict | None,
) -> int:
    chunks = chunk_text(text)
    if not chunks:
        return 0
    embeddings = embed_batch(chunks)

    with db.get_conn() as conn:
        with conn.cursor() as cur:
            for idx, (chunk, vec) in enumerate(zip(chunks, embeddings)):
                cur.execute(
                    """
                    INSERT INTO rag_documents
                      (asset_id, source_name, chunk_index, content, embedding, metadata)
                    VALUES (%s, %s, %s, %s, %s::vector, %s::jsonb)
                    """,
                    (
                        asset["id"],
                        source_name,
                        idx,
                        chunk,
                        _vec_literal(vec),
                        json.dumps(metadata or {}),
                    ),
                )

    # Roll up doc_count in the asset config for the UI badge.
    _bump_doc_count(asset["id"], len(chunks))
    return len(chunks)


def _bump_doc_count(asset_id: int, delta: int) -> None:
    """Keep asset.config.doc_count in sync so the UI can show '123 chunks'
    without a second query. The value is best-effort — if two ingestions
    race, they'll converge at a consistent total via UPDATE ... SET = n+delta."""
    db.execute(
        """
        UPDATE asset_items
           SET config = jsonb_set(
                 COALESCE(config, '{}'::jsonb),
                 '{doc_count}',
                 to_jsonb(
                   COALESCE((config ->> 'doc_count')::int, 0) + %s
                 )
               ),
               updated_at = NOW()
         WHERE id = %s
        """,
        (delta, asset_id),
    )


def _search_pgvector(asset: dict, query: str, top_k: int) -> list[dict]:
    vec = embed_one(query)
    rows = db.fetch_all(
        """
        SELECT id, source_name, chunk_index, content, metadata,
               1 - (embedding <=> %s::vector) AS score
        FROM rag_documents
        WHERE asset_id = %s
        ORDER BY embedding <=> %s::vector
        LIMIT %s
        """,
        (_vec_literal(vec), asset["id"], _vec_literal(vec), top_k),
    )
    return rows


def delete_all_chunks(asset_id: int) -> int:
    """Wipe every chunk for this asset. Used when a RAG source is deleted
    or an admin hits 'rebuild index'."""
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM rag_documents WHERE asset_id = %s",
                (asset_id,),
            )
            n = cur.rowcount
    # Reset doc_count in asset config
    db.execute(
        """
        UPDATE asset_items
           SET config = jsonb_set(
                 COALESCE(config, '{}'::jsonb),
                 '{doc_count}',
                 '0'::jsonb
               ),
               updated_at = NOW()
         WHERE id = %s
        """,
        (asset_id,),
    )
    return n


def chunk_count(asset_id: int) -> int:
    row = db.fetch_one(
        "SELECT COUNT(*) AS c FROM rag_documents WHERE asset_id = %s",
        (asset_id,),
    )
    return int(row["c"]) if row else 0
