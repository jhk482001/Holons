"""Abstract LLM client — the interface every provider must implement.

All providers conform to a Bedrock-Converse-shaped request/response because
that's what the existing engine.py already expects. Non-Bedrock providers
translate to and from Bedrock format inside their own module; the engine
doesn't need to know which provider it's talking to.

Message shape (input `messages`):

    [
      {"role": "user"|"assistant", "content": [
         {"text": "..."}                                 # plain text block
         | {"toolUse": {"toolUseId", "name", "input"}}   # assistant side
         | {"toolResult": {"toolUseId", "content": [{"text":...}], "status": "success"|"error"}}
         | {"image": {"format": "png"|..., "source": {"bytes": b"..."}}}
      ]}
    ]

Tool config shape (input `tool_config`):

    [
      {"name": "foo", "description": "...", "inputSchema": {"json": {...}}}
    ]

Response shape (returned dict):

    {
      "text": str,                        # concatenated text blocks
      "tool_uses": [{"toolUseId","name","input"}, ...],
      "stop_reason": "end_turn"|"tool_use"|"max_tokens"|"error",
      "assistant_message": {"role": "assistant", "content": [...]},  # for history
      "input_tokens": int,
      "output_tokens": int,
      "cost_usd": float,
      "duration_ms": int,
      "model_id": str,
      "provider": str,
      "error": str | None,
    }

Every implementation is free to return `cost_usd = 0.0` if the provider
doesn't expose usage / pricing info.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Optional


class LLMClient(ABC):
    """Base class. Concrete implementations live in sibling modules."""

    provider: str = "unknown"

    def __init__(self, *, client_row: dict):
        """client_row: the output of model_clients.get_raw() — includes
        config dict and decrypted credential dict."""
        self.client_row = client_row
        self.config: dict[str, Any] = client_row.get("config") or {}
        self.credential: dict[str, Any] = client_row.get("credential") or {}

    @abstractmethod
    def invoke(
        self,
        *,
        model_id: str,
        system_prompt: str,
        messages: list[dict],
        tool_config: Optional[list[dict]] = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ) -> dict:
        """Call the underlying provider and return a Bedrock-shaped result."""

    # Optional streaming path — providers that can stream override this.
    # The default implementation is a degradation: call invoke() to get
    # the full result, yield the entire text as one chunk, then yield
    # the full result dict. That lets the call-site (invoke_streaming_for_agent)
    # treat streaming as a strict superset of batch without having to
    # special-case providers that lack native streaming support.
    #
    # Yields tuples of the form:
    #   ("chunk", str)              — incremental text delta
    #   ("complete", dict)          — final result dict (same shape as invoke())
    def stream(
        self,
        *,
        model_id: str,
        system_prompt: str,
        messages: list[dict],
        tool_config: Optional[list[dict]] = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ):
        result = self.invoke(
            model_id=model_id,
            system_prompt=system_prompt,
            messages=messages,
            tool_config=tool_config,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        text = result.get("text") or ""
        if text:
            yield ("chunk", text)
        yield ("complete", result)

    # ------------------------------------------------------------------
    # Convenience helpers shared by non-Bedrock providers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_last_user_text(messages: list[dict]) -> str:
        """For providers that can't handle multi-turn toolResult blocks
        natively, pull the last plain-text user message as a fallback."""
        for m in reversed(messages):
            if m.get("role") == "user":
                for block in m.get("content", []):
                    if "text" in block:
                        return block["text"]
        return ""

    @staticmethod
    def _messages_to_plain_history(messages: list[dict]) -> list[dict]:
        """Flatten Bedrock-shape messages into simple {role, content} dicts
        for providers that take OpenAI-style history."""
        out = []
        for m in messages:
            role = m.get("role", "user")
            parts = []
            for block in m.get("content", []):
                if "text" in block:
                    parts.append(block["text"])
                elif "toolResult" in block:
                    tr = block["toolResult"]
                    text_bits = [
                        c.get("text", "") for c in tr.get("content", []) if "text" in c
                    ]
                    parts.append(" ".join(text_bits))
            if parts:
                out.append({"role": role, "content": "\n".join(parts)})
        return out

    @staticmethod
    def _empty_result(model_id: str, provider: str, error: str) -> dict:
        return {
            "text": f"[ERROR {provider}/{model_id}: {error}]",
            "tool_uses": [],
            "stop_reason": "error",
            "assistant_message": {
                "role": "assistant",
                "content": [{"text": f"[ERROR {provider}: {error}]"}],
            },
            "input_tokens": 0,
            "output_tokens": 0,
            "cost_usd": 0.0,
            "duration_ms": 0,
            "model_id": model_id,
            "provider": provider,
            "error": error,
        }
