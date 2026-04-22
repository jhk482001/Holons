"""Unified LLM client factory.

Usage:

    from backend.services import model_clients
    from backend import llm_clients

    client_row = model_clients.resolve_for_agent(agent_id)
    llm = llm_clients.build(client_row)
    result = llm.invoke(
        model_id=agent["primary_model_id"],
        system_prompt=agent["system_prompt"],
        messages=[...],
        tool_config=[...],
    )

Every provider module returns a Bedrock-Converse-shaped response dict, so
the engine doesn't need to care which provider is behind the call.
"""
from __future__ import annotations

from .base import LLMClient
from .bedrock import BedrockLLMClient
from .claude_native import ClaudeNativeLLMClient
from .gemini import GeminiLLMClient
from .minimax import MinimaxLLMClient
from .openai_compat import (
    AzureOpenAILLMClient,
    LocalOpenAICompatClient,
    OpenAILLMClient,
)


_REGISTRY: dict[str, type[LLMClient]] = {
    "bedrock": BedrockLLMClient,
    "claude_native": ClaudeNativeLLMClient,
    "openai": OpenAILLMClient,
    "azure_openai": AzureOpenAILLMClient,
    "gemini": GeminiLLMClient,
    "minimax": MinimaxLLMClient,
    "local": LocalOpenAICompatClient,
}


def build(client_row: dict) -> LLMClient:
    """Instantiate an LLMClient for the given raw model_clients row.
    `client_row` must include `kind`, `config` dict, and `credential` dict
    (as returned by `model_clients.get_raw`)."""
    kind = client_row.get("kind")
    cls = _REGISTRY.get(kind)
    if not cls:
        raise ValueError(f"unknown model client kind: {kind}")
    return cls(client_row=client_row)


_RETRYABLE_ERRORS = (
    "ThrottlingException",
    "TooManyRequestsException",
    "ServiceUnavailableException",
    "ModelTimeoutException",
    "RequestTimeout",
    "rate limit",
    "Rate limit",
    "429",
    "503",
    "Connection reset",
    "Connection refused",
)

_MAX_RETRIES = 3
_BASE_DELAY = 1.0  # seconds


def invoke_via_client(
    client_row: dict,
    *,
    model_id: str,
    system_prompt: str,
    messages: list[dict],
    tool_config: list[dict] | None = None,
    max_tokens: int = 4096,
    temperature: float = 0.7,
) -> dict:
    """Build + invoke with automatic retry on transient failures.

    Retries up to 3 times with exponential backoff (1s, 2s, 4s) for
    throttle / timeout / 5xx errors. Non-retryable errors (auth, bad
    request) fail immediately.
    """
    import time
    import logging
    log = logging.getLogger("agent_company.llm_retry")

    llm = build(client_row)
    last_result: dict = {}

    for attempt in range(_MAX_RETRIES + 1):
        result = llm.invoke(
            model_id=model_id,
            system_prompt=system_prompt,
            messages=messages,
            tool_config=tool_config,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        last_result = result

        # Success — no error
        if not result.get("error"):
            return result

        # Check if error is retryable
        err_msg = result.get("error", "")
        is_retryable = any(pat in err_msg for pat in _RETRYABLE_ERRORS)

        if not is_retryable or attempt >= _MAX_RETRIES:
            return result

        delay = _BASE_DELAY * (2 ** attempt)
        log.warning(
            "LLM call failed (attempt %d/%d), retrying in %.1fs: %s",
            attempt + 1, _MAX_RETRIES + 1, delay, err_msg[:200],
        )
        time.sleep(delay)

    return last_result


def invoke_for_agent(
    *,
    agent_id: int | None,
    model_key: str | None = None,
    system_prompt: str = "",
    user_text: str = "",
    history: list[dict] | None = None,
    messages: list[dict] | None = None,
    tool_config: list[dict] | None = None,
    max_tokens: int = 4096,
    temperature: float = 0.7,
) -> dict:
    """Legacy-compatible entry point for the engine and services.

    Drop-in replacement for `backend.bedrock_client.invoke()` that also
    respects the per-agent model client binding. If the agent has no
    model_client_id (shouldn't happen post-backfill), falls back to the
    first default client. If no default client exists (fresh install
    before schema ran), falls back to the legacy bedrock_client path.

    - `model_key` — if given, overrides the agent's primary_model_id.
      Stays as a kwarg for back-compat with the old signature.
    - `user_text` + `history` — legacy simple path; we build Bedrock-shape
      messages internally.
    - `messages` — tool-loop path, passed through as-is.
    """
    from ..services import model_clients

    client_row = model_clients.resolve_for_agent(agent_id)
    if not client_row:
        # Fallback: no client configured at all (should only happen in
        # tests that skip schema.create_all). Delegate to the legacy
        # bedrock_client path so existing tests keep passing.
        from ..bedrock_client import invoke as legacy_invoke
        return legacy_invoke(
            model_key=model_key or "claude-sonnet-4.6",
            system_prompt=system_prompt,
            user_text=user_text,
            history=history,
            messages=messages,
            tool_config=tool_config,
            max_tokens=max_tokens,
            temperature=temperature,
        )

    # Resolve primary + fallback model ids. Caller-supplied model_key
    # wins for primary; if not supplied, we read both columns off the
    # agent row in one go so the fallback path below doesn't re-query.
    fallback_model: str | None = None
    resolved_model = model_key
    if not resolved_model:
        from .. import db as _db
        agent_row = _db.fetch_one(
            "SELECT primary_model_id, fallback_model_id FROM agents WHERE id = %s",
            (agent_id,),
        ) or {}
        resolved_model = (agent_row.get("primary_model_id") or "")
        fallback_model = (agent_row.get("fallback_model_id") or None) or None
    else:
        # Caller pinned a specific model — still look up fallback.
        from .. import db as _db
        agent_row = _db.fetch_one(
            "SELECT fallback_model_id FROM agents WHERE id = %s", (agent_id,),
        ) or {}
        fallback_model = (agent_row.get("fallback_model_id") or None) or None
    if not resolved_model:
        cfg_models = (client_row.get("config") or {}).get("models") or []
        if cfg_models:
            resolved_model = cfg_models[0].get("id") or ""
    if not resolved_model:
        return LLMClient._empty_result(  # type: ignore[attr-defined]
            "", client_row.get("kind") or "unknown",
            "no model_id resolved for agent",
        )

    # Build Bedrock-shape messages if the caller used the legacy path
    msgs = messages
    if msgs is None:
        msgs = []
        if history:
            for h in history:
                role = h.get("role", "user")
                if role not in ("user", "assistant"):
                    continue
                msgs.append({"role": role, "content": [{"text": h.get("content", "")}]})
        if user_text:
            msgs.append({"role": "user", "content": [{"text": user_text}]})

    def _call(mid: str) -> dict:
        return invoke_via_client(
            client_row,
            model_id=mid,
            system_prompt=system_prompt,
            messages=msgs,
            tool_config=tool_config,
            max_tokens=max_tokens,
            temperature=temperature,
        )

    result = _call(resolved_model)

    # Fallback routing — if the primary call returned an error AND the
    # agent has a fallback model configured, retry once with the
    # fallback. We only switch on errors that are LIKELY transient or
    # model-specific (throttling, model not available, bad request
    # about a model feature). Agent quota / auth errors propagate.
    if result.get("error") and fallback_model and fallback_model != resolved_model:
        err_text = (result.get("error") or "").lower()
        transient_markers = (
            "throttl", "rate limit", "429", "500", "502", "503", "504",
            "timeout", "timed out", "model not found", "unavailable",
            "inference profile", "provisionedmodel",
            "invalidrequest", "unsupported",
        )
        if any(m in err_text for m in transient_markers):
            import logging as _log
            _log.getLogger("agent_company.llm").warning(
                "primary %s failed (%s) — falling back to %s",
                resolved_model, err_text[:120], fallback_model,
            )
            fb_result = _call(fallback_model)
            # Mark in the result so the UI can show "served by fallback"
            fb_result.setdefault("fallback_used", True)
            fb_result.setdefault("fallback_from", resolved_model)
            fb_result.setdefault("fallback_to", fallback_model)
            if not fb_result.get("error"):
                return fb_result
            # Both failed — surface the fallback's error (usually more
            # informative than the original) but annotate.
            fb_result["error"] = (
                f"primary ({resolved_model}) error: {err_text[:200]} | "
                f"fallback ({fallback_model}) error: "
                f"{(fb_result.get('error') or '')[:200]}"
            )
            return fb_result

    return result
