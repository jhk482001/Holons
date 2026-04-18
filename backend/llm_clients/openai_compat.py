"""OpenAI Chat Completions-compatible client.

Covers three kinds:
- openai         (api.openai.com/v1)
- azure_openai   (https://{resource}.openai.azure.com + deployment path)
- local          (any OpenAI-compatible endpoint — Ollama, LM Studio, vLLM)

All three share the same request/response wire format. Only the URL
construction and auth header differ, so we keep them in one module.
"""
from __future__ import annotations

import json
import time
import urllib.request
import urllib.error
from typing import Optional

from .base import LLMClient


def _openai_tool_call_to_bedrock(tc: dict) -> dict:
    fn = tc.get("function") or {}
    args = fn.get("arguments") or "{}"
    try:
        parsed = json.loads(args) if isinstance(args, str) else args
    except Exception:
        parsed = {"_raw": args}
    return {
        "toolUseId": tc.get("id") or "",
        "name": fn.get("name") or "",
        "input": parsed,
    }


def _bedrock_messages_to_openai(
    messages: list[dict], system_prompt: str
) -> list[dict]:
    """Convert Bedrock Converse messages -> OpenAI chat messages.

    OpenAI's format for tool calls is:
      - assistant turn with `tool_calls: [...]`
      - followed by one `tool` role message per result
    """
    out: list[dict] = []
    if system_prompt:
        out.append({"role": "system", "content": system_prompt})

    for m in messages:
        role = m.get("role", "user")
        text_parts: list[str] = []
        tool_calls: list[dict] = []
        tool_result_msgs: list[dict] = []
        for b in m.get("content", []):
            if "text" in b:
                text_parts.append(b["text"])
            elif "toolUse" in b and role == "assistant":
                tu = b["toolUse"]
                tool_calls.append({
                    "id": tu.get("toolUseId") or "",
                    "type": "function",
                    "function": {
                        "name": tu["name"],
                        "arguments": json.dumps(tu.get("input") or {}),
                    },
                })
            elif "toolResult" in b and role == "user":
                tr = b["toolResult"]
                content_text = "\n".join(
                    c.get("text", "") for c in (tr.get("content") or []) if "text" in c
                )
                tool_result_msgs.append({
                    "role": "tool",
                    "tool_call_id": tr.get("toolUseId") or "",
                    "content": content_text,
                })
        if role == "user" and tool_result_msgs and not text_parts:
            out.extend(tool_result_msgs)
            continue
        msg: dict = {"role": role, "content": "\n".join(text_parts)}
        if tool_calls:
            msg["tool_calls"] = tool_calls
            if not text_parts:
                msg["content"] = None
        out.append(msg)
        if tool_result_msgs:
            out.extend(tool_result_msgs)
    return out


def _openai_response_to_bedrock(
    data: dict, model_id: str, provider: str, duration_ms: int, config: dict
) -> dict:
    choices = data.get("choices") or []
    if not choices:
        return LLMClient._empty_result(model_id, provider, "empty choices")
    msg = choices[0].get("message") or {}
    text = msg.get("content") or ""
    tool_uses: list[dict] = []
    bedrock_content: list[dict] = []
    if text:
        bedrock_content.append({"text": text})
    for tc in msg.get("tool_calls") or []:
        b_tc = _openai_tool_call_to_bedrock(tc)
        tool_uses.append(b_tc)
        bedrock_content.append({"toolUse": b_tc})

    usage = data.get("usage") or {}
    in_tok = int(usage.get("prompt_tokens") or 0)
    out_tok = int(usage.get("completion_tokens") or 0)

    price_in = price_out = 0.0
    for m in config.get("models") or []:
        if m.get("id") == model_id:
            price_in = float(m.get("price_in") or 0)
            price_out = float(m.get("price_out") or 0)
            break
    cost = (in_tok / 1000.0) * price_in + (out_tok / 1000.0) * price_out

    finish_reason = choices[0].get("finish_reason") or "stop"
    stop_map = {
        "stop": "end_turn",
        "length": "max_tokens",
        "tool_calls": "tool_use",
        "function_call": "tool_use",
    }
    stop_reason = stop_map.get(finish_reason, "end_turn")

    return {
        "text": text or "",
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


def _http_post_json(url: str, body: dict, headers: dict) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"), headers=headers
    )
    with urllib.request.urlopen(req, timeout=600) as resp:
        return json.loads(resp.read().decode("utf-8"))


# ============================================================================
# OpenAI (api.openai.com)
# ============================================================================

class OpenAILLMClient(LLMClient):
    provider = "openai"

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

        base_url = (self.config.get("base_url") or "https://api.openai.com/v1").rstrip("/")
        url = f"{base_url}/chat/completions"

        body: dict = {
            "model": model_id,
            "messages": _bedrock_messages_to_openai(messages, system_prompt),
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if tool_config:
            body["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t["name"],
                        "description": t.get("description", ""),
                        "parameters": (t.get("inputSchema") or {}).get("json", t.get("inputSchema", {})),
                    },
                }
                for t in tool_config
            ]

        headers = {
            "content-type": "application/json",
            "authorization": f"Bearer {api_key}",
        }
        org = self.config.get("organization")
        if org:
            headers["openai-organization"] = org

        t0 = time.time()
        try:
            data = _http_post_json(url, body, headers)
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", "replace")
            return self._empty_result(model_id, self.provider, f"HTTP {e.code}: {err_body[:300]}")
        except Exception as e:
            return self._empty_result(model_id, self.provider, str(e))
        duration_ms = int((time.time() - t0) * 1000)
        return _openai_response_to_bedrock(
            data, model_id, self.provider, duration_ms, self.config
        )


# ============================================================================
# Azure OpenAI
# ============================================================================

class AzureOpenAILLMClient(LLMClient):
    provider = "azure_openai"

    def invoke(
        self,
        *,
        model_id: str,  # for Azure this is the deployment name
        system_prompt: str,
        messages: list[dict],
        tool_config: Optional[list[dict]] = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ) -> dict:
        api_key = self.credential.get("api_key") or ""
        if not api_key:
            return self._empty_result(model_id, self.provider, "missing api_key")

        endpoint = (self.config.get("endpoint") or "").rstrip("/")
        if not endpoint:
            return self._empty_result(model_id, self.provider, "missing endpoint")
        api_version = self.config.get("api_version") or "2024-10-01-preview"
        url = f"{endpoint}/openai/deployments/{model_id}/chat/completions?api-version={api_version}"

        body: dict = {
            "messages": _bedrock_messages_to_openai(messages, system_prompt),
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if tool_config:
            body["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t["name"],
                        "description": t.get("description", ""),
                        "parameters": (t.get("inputSchema") or {}).get("json", t.get("inputSchema", {})),
                    },
                }
                for t in tool_config
            ]

        headers = {
            "content-type": "application/json",
            "api-key": api_key,
        }

        t0 = time.time()
        try:
            data = _http_post_json(url, body, headers)
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", "replace")
            return self._empty_result(model_id, self.provider, f"HTTP {e.code}: {err_body[:300]}")
        except Exception as e:
            return self._empty_result(model_id, self.provider, str(e))
        duration_ms = int((time.time() - t0) * 1000)
        return _openai_response_to_bedrock(
            data, model_id, self.provider, duration_ms, self.config
        )


# ============================================================================
# Local (Ollama / LM Studio / vLLM — anything speaking OpenAI wire format)
# ============================================================================

class LocalOpenAICompatClient(LLMClient):
    provider = "local"

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
        base_url = (self.config.get("base_url") or "http://localhost:11434/v1").rstrip("/")
        url = f"{base_url}/chat/completions"
        api_key = self.credential.get("api_key") or "ollama"  # many local servers ignore this

        body: dict = {
            "model": model_id,
            "messages": _bedrock_messages_to_openai(messages, system_prompt),
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if tool_config:
            body["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t["name"],
                        "description": t.get("description", ""),
                        "parameters": (t.get("inputSchema") or {}).get("json", t.get("inputSchema", {})),
                    },
                }
                for t in tool_config
            ]

        headers = {
            "content-type": "application/json",
            "authorization": f"Bearer {api_key}",
        }

        t0 = time.time()
        try:
            data = _http_post_json(url, body, headers)
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", "replace")
            return self._empty_result(model_id, self.provider, f"HTTP {e.code}: {err_body[:300]}")
        except Exception as e:
            return self._empty_result(model_id, self.provider, str(e))
        duration_ms = int((time.time() - t0) * 1000)
        return _openai_response_to_bedrock(
            data, model_id, self.provider, duration_ms, self.config
        )
