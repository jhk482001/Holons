"""Anthropic Claude native API client (non-Bedrock).

Uses raw HTTPS to `https://api.anthropic.com/v1/messages` so we don't need
the `anthropic` SDK installed. The Messages API format is very close to
what we already use in the engine, so translation is minimal.
"""
from __future__ import annotations

import json
import time
import urllib.request
import urllib.error
from typing import Optional

from .base import LLMClient


class ClaudeNativeLLMClient(LLMClient):
    provider = "claude_native"

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
        api_key = self.credential.get("api_key") or ""
        if not api_key:
            return self._empty_result(model_id, self.provider, "missing api_key")

        base_url = (self.config.get("base_url") or "https://api.anthropic.com").rstrip("/")
        url = f"{base_url}/v1/messages"

        anth_messages = _bedrock_to_anthropic(messages)
        body: dict = {
            "model": model_id,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": anth_messages,
        }
        if system_prompt:
            body["system"] = system_prompt
        if tool_config:
            body["tools"] = [
                {
                    "name": t["name"],
                    "description": t.get("description", ""),
                    "input_schema": (t.get("inputSchema") or {}).get("json", t.get("inputSchema", {})),
                }
                for t in tool_config
            ]

        t0 = time.time()
        try:
            req = urllib.request.Request(
                url,
                data=json.dumps(body).encode("utf-8"),
                headers={
                    "content-type": "application/json",
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                },
            )
            with urllib.request.urlopen(req, timeout=600) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", "replace")
            return self._empty_result(model_id, self.provider, f"HTTP {e.code}: {err_body[:300]}")
        except Exception as e:
            return self._empty_result(model_id, self.provider, str(e))

        duration_ms = int((time.time() - t0) * 1000)
        return _anthropic_to_bedrock(data, model_id, self.provider, duration_ms, self.config)


def _bedrock_to_anthropic(messages: list[dict]) -> list[dict]:
    """Convert Bedrock Converse messages into Anthropic Messages API shape.
    Both are 'content blocks' flavored but differ in field naming."""
    out = []
    for m in messages:
        role = m.get("role", "user")
        blocks = []
        for b in m.get("content", []):
            if "text" in b:
                blocks.append({"type": "text", "text": b["text"]})
            elif "toolUse" in b:
                tu = b["toolUse"]
                blocks.append({
                    "type": "tool_use",
                    "id": tu.get("toolUseId") or tu.get("id"),
                    "name": tu["name"],
                    "input": tu.get("input", {}),
                })
            elif "toolResult" in b:
                tr = b["toolResult"]
                content = []
                for c in tr.get("content", []):
                    if "text" in c:
                        content.append({"type": "text", "text": c["text"]})
                blocks.append({
                    "type": "tool_result",
                    "tool_use_id": tr.get("toolUseId") or tr.get("id"),
                    "content": content,
                    "is_error": tr.get("status") == "error",
                })
            elif "image" in b:
                img = b["image"]
                src = img.get("source", {})
                if "bytes" in src:
                    import base64
                    blocks.append({
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": f"image/{img.get('format', 'png')}",
                            "data": base64.b64encode(src["bytes"]).decode("ascii"),
                        },
                    })
        out.append({"role": role, "content": blocks})
    return out


def _anthropic_to_bedrock(
    data: dict,
    model_id: str,
    provider: str,
    duration_ms: int,
    config: dict,
) -> dict:
    """Wrap an Anthropic Messages API response into our Bedrock-shape dict."""
    content_blocks = data.get("content") or []
    text_parts: list[str] = []
    tool_uses: list[dict] = []
    bedrock_content: list[dict] = []
    for b in content_blocks:
        t = b.get("type")
        if t == "text":
            text_parts.append(b.get("text", ""))
            bedrock_content.append({"text": b.get("text", "")})
        elif t == "tool_use":
            tu = {
                "toolUseId": b.get("id"),
                "name": b.get("name"),
                "input": b.get("input") or {},
            }
            tool_uses.append(tu)
            bedrock_content.append({"toolUse": tu})

    usage = data.get("usage") or {}
    in_tok = int(usage.get("input_tokens") or 0)
    out_tok = int(usage.get("output_tokens") or 0)

    # Pricing lookup from config.models
    price_in = price_out = 0.0
    for m in config.get("models") or []:
        if m.get("id") == model_id:
            price_in = float(m.get("price_in") or 0)
            price_out = float(m.get("price_out") or 0)
            break
    cost = (in_tok / 1000.0) * price_in + (out_tok / 1000.0) * price_out

    stop_reason_map = {
        "end_turn": "end_turn",
        "tool_use": "tool_use",
        "max_tokens": "max_tokens",
        "stop_sequence": "end_turn",
    }
    stop_reason = stop_reason_map.get(data.get("stop_reason", "end_turn"), "end_turn")

    return {
        "text": "".join(text_parts),
        "tool_uses": tool_uses,
        "stop_reason": stop_reason,
        "assistant_message": {"role": "assistant", "content": bedrock_content},
        "input_tokens": in_tok,
        "output_tokens": out_tok,
        "cost_usd": round(cost, 6),
        "duration_ms": duration_ms,
        "model_id": model_id,
        "provider": provider,
        "error": None,
    }
