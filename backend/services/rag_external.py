"""External RAG connectors — AWS Bedrock Knowledge Base and Pinecone.

These are dispatched from `rag.ingest_text()` / `rag.search()` when an
asset's `config.backend` is `bedrock_kb` or `pinecone`. We keep all the
vendor-specific boto/pinecone code isolated here so `rag.py` stays small.

Configuration shape stored in `asset_items.config`:

    bedrock_kb:
        {
            "backend": "bedrock_kb",
            "knowledge_base_id": "KB123456",
            "data_source_id": "DS123",         // optional, for ingest
            "region": "ap-northeast-1",        // optional, defaults to CFG
        }

    pinecone:
        {
            "backend": "pinecone",
            "index_name": "my-kb",
            "namespace": "default",            // optional
            "environment": "us-east-1-aws",    // host / environment
            "embed_model": "titan",            // "titan" or "openai" (future)
        }

Credentials live in `credential_encrypted` (Fernet-decrypted on use):
  - bedrock_kb: unused (uses ambient AWS creds from env.config)
  - pinecone: the Pinecone API key, as a plain string

Both connectors expose the same `ingest_text(asset, source_name, text, metadata)`
and `search(asset, query, top_k)` interface so `rag.py` can dispatch blindly.
"""
from __future__ import annotations

import json
import logging
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from ..config import CFG
from . import asset_crypto

log = logging.getLogger("agent_company.rag_external")


def _backend(asset: dict) -> str:
    return (asset.get("config") or {}).get("backend", "").lower()


def ingest_text(
    asset: dict,
    source_name: str,
    text: str,
    metadata: dict | None,
) -> int:
    backend = _backend(asset)
    if backend == "bedrock_kb":
        return _bedrock_kb_ingest(asset, source_name, text, metadata or {})
    if backend == "pinecone":
        return _pinecone_ingest(asset, source_name, text, metadata or {})
    raise ValueError(f"unknown external backend {backend!r}")


def search(asset: dict, query: str, top_k: int) -> list[dict]:
    backend = _backend(asset)
    if backend == "bedrock_kb":
        return _bedrock_kb_search(asset, query, top_k)
    if backend == "pinecone":
        return _pinecone_search(asset, query, top_k)
    raise ValueError(f"unknown external backend {backend!r}")


# ============================================================================
# AWS Bedrock Knowledge Base
# ============================================================================
#
# Bedrock KB has a split API:
#   - bedrock-agent-runtime.retrieve() — query, no LLM wrapper
#   - bedrock-agent.start_ingestion_job() — trigger a re-sync on a data source
#
# We don't push raw text to bedrock_kb directly — the KB is expected to be
# configured with its own data sources (S3, SharePoint, etc.) out of band.
# `ingest_text` here is best-effort: it uploads the text to the data source's
# S3 bucket if we know it, then triggers a sync; otherwise it raises.
#
# For most users this connector is search-only — create the KB in AWS,
# point the config at it, and query from agents.

def _bedrock_agent_runtime(region: str | None = None):
    return boto3.client(
        "bedrock-agent-runtime",
        aws_access_key_id=CFG.get("AWS_ACCESS_KEY") or None,
        aws_secret_access_key=CFG.get("AWS_SECRET_KEY") or None,
        region_name=region or CFG.get("AWS_REGION", "ap-northeast-1"),
    )


def _bedrock_kb_ingest(asset, source_name, text, metadata) -> int:
    """Not implemented — Bedrock KB ingest is driven by its own data source
    plumbing (S3 sync, crawler, etc.) and we don't want to own an S3 bucket
    here. Raise with a clear message so the UI can surface it as 'search-only
    connector'."""
    raise NotImplementedError(
        "Bedrock KB is a search-only connector in this app. Ingest your "
        "documents via the AWS console or SDK into the KB's native data "
        "source, then call /api/rag/search."
    )


def _bedrock_kb_search(asset, query: str, top_k: int) -> list[dict]:
    cfg = asset.get("config") or {}
    kb_id = cfg.get("knowledge_base_id")
    if not kb_id:
        raise ValueError("bedrock_kb asset missing config.knowledge_base_id")
    client = _bedrock_agent_runtime(cfg.get("region"))
    try:
        resp = client.retrieve(
            knowledgeBaseId=kb_id,
            retrievalQuery={"text": query},
            retrievalConfiguration={
                "vectorSearchConfiguration": {"numberOfResults": top_k},
            },
        )
    except (BotoCoreError, ClientError) as exc:
        log.exception("bedrock_kb retrieve failed")
        raise RuntimeError(f"Bedrock KB retrieve failed: {exc}") from exc
    out: list[dict] = []
    for idx, hit in enumerate(resp.get("retrievalResults") or []):
        content = hit.get("content") or {}
        loc = hit.get("location") or {}
        score = hit.get("score", 0.0)
        out.append({
            "id": idx,
            "source_name": _bedrock_kb_source_name(loc),
            "chunk_index": idx,
            "content": content.get("text") or "",
            "score": float(score),
            "metadata": {"location": loc},
        })
    return out


def _bedrock_kb_source_name(location: dict) -> str:
    """Flatten a Bedrock KB retrievalResult.location into a human string."""
    t = location.get("type") or "unknown"
    if t == "S3":
        return (location.get("s3Location") or {}).get("uri") or "s3://?"
    if t == "WEB":
        return (location.get("webLocation") or {}).get("url") or "http://?"
    return t


# ============================================================================
# Pinecone
# ============================================================================
#
# Pinecone's Python SDK ships as `pinecone-client` — we don't add it as a
# hard dependency because not everyone uses Pinecone. Import lazily inside
# the functions so `rag_external.py` stays importable even without it.

def _pinecone_client(asset: dict):
    try:
        from pinecone import Pinecone
    except ImportError as exc:
        raise RuntimeError(
            "pinecone-client is not installed. Run: pip install pinecone-client"
        ) from exc

    credential = asset.get("credential_encrypted")
    # credential may be absent when the asset was fetched via _LIST_COLS;
    # fetch it raw if so
    if not credential:
        from .. import db
        row = db.fetch_one(
            "SELECT credential_encrypted FROM asset_items WHERE id = %s",
            (asset["id"],),
        )
        credential = row.get("credential_encrypted") if row else None
    api_key = asset_crypto.decrypt(credential)
    if not api_key:
        raise ValueError("pinecone asset missing API key credential")
    return Pinecone(api_key=api_key)


def _pinecone_ingest(asset, source_name: str, text: str, metadata: dict) -> int:
    """Chunk the text (reuse rag.chunk_text) → embed via Titan → upsert
    into the Pinecone index namespace."""
    from . import rag  # lazy to avoid cycles
    cfg = asset.get("config") or {}
    index_name = cfg.get("index_name")
    namespace = cfg.get("namespace") or "default"
    if not index_name:
        raise ValueError("pinecone asset missing config.index_name")

    chunks = rag.chunk_text(text)
    if not chunks:
        return 0
    embeddings = rag.embed_batch(chunks)

    pc = _pinecone_client(asset)
    index = pc.Index(index_name)
    vectors = []
    for idx, (chunk, vec) in enumerate(zip(chunks, embeddings)):
        vid = f"asset{asset['id']}_{source_name}_{idx}"
        vectors.append({
            "id": vid,
            "values": vec,
            "metadata": {
                "asset_id": asset["id"],
                "source_name": source_name,
                "chunk_index": idx,
                "content": chunk[:2000],  # Pinecone metadata size limit
                **metadata,
            },
        })
    index.upsert(vectors=vectors, namespace=namespace)
    return len(vectors)


def _pinecone_search(asset, query: str, top_k: int) -> list[dict]:
    from . import rag  # lazy
    cfg = asset.get("config") or {}
    index_name = cfg.get("index_name")
    namespace = cfg.get("namespace") or "default"
    if not index_name:
        raise ValueError("pinecone asset missing config.index_name")

    vec = rag.embed_one(query)
    pc = _pinecone_client(asset)
    index = pc.Index(index_name)
    resp = index.query(
        vector=vec,
        top_k=top_k,
        namespace=namespace,
        include_metadata=True,
    )
    out: list[dict] = []
    for hit in resp.get("matches") or []:
        meta = hit.get("metadata") or {}
        out.append({
            "id": hit.get("id"),
            "source_name": meta.get("source_name") or "",
            "chunk_index": int(meta.get("chunk_index", 0)),
            "content": meta.get("content") or "",
            "score": float(hit.get("score", 0)),
            "metadata": {k: v for k, v in meta.items() if k != "content"},
        })
    return out
