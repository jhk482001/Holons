"""Google Gemini client — uses the REST generateContent endpoint.

https://generativelanguage.googleapis.com/v1beta/models/<model>:generateContent?key=<api_key>

Gemini's request/response shape is pretty different from Bedrock's, so we
translate through a shared helper. Tool calling uses the `functionDeclarations`
+ `functionCall` / `functionResponse` parts format.
"""
from __future__ import annotations

import json
import time
import urllib.request
import urllib.error
from typing import Optional

from .base import LLMClient


class GeminiLLMClient(LLMClient):
    provider = "gemini"

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

        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{model_id}:generateContent?key={api_key}"
        )

        body: dict = {
            "contents": _bedrock_to_gemini_contents(messages),
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": max_tokens,
            },
        }
        if system_prompt:
            body["systemInstruction"] = {"parts": [{"text": system_prompt}]}
        if tool_config:
            body["tools"] = [
                {
                    "functionDeclarations": [
                        {
                            "name": t["name"],
                            "description": t.get("description", ""),
                            "parameters": (t.get("inputSchema") or {}).get("json", t.get("inputSchema", {})),
                        }
                        for t in tool_config
                    ]
                }
            ]

        t0 = time.time()
        try:
            req = urllib.request.Request(
                url,
                data=json.dumps(body).encode("utf-8"),
                headers={"content-type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=600) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", "replace")
            return self._empty_result(model_id, self.provider, f"HTTP {e.code}: {err_body[:300]}")
        except Exception as e:
            return self._empty_result(model_id, self.provider, str(e))

        duration_ms = int((time.time() - t0) * 1000)
        return _gemini_to_bedrock(data, model_id, self.provider, duration_ms, self.config)


def _bedrock_to_gemini_contents(messages: list[dict]) -> list[dict]:
    out = []
    for m in messages:
        role = m.get("role", "user")
        # Gemini uses "user" and "model" instead of "assistant"
        g_role = "model" if role == "assistant" else "user"
        parts = []
        for b in m.get("content", []):
            if "text" in b:
                parts.append({"text": b["text"]})
            elif "toolUse" in b:
                tu = b["toolUse"]
                parts.append({
                    "functionCall": {
                        "name": tu["name"],
                        "args": tu.get("input") or {},
                    }
                })
            elif "toolResult" in b:
                tr = b["toolResult"]
                result_text = "\n".join(
                    c.get("text", "") for c in tr.get("content", []) if "text" in c
                )
                parts.append({
                    "functionResponse": {
                        "name": tr.get("toolUseId") or "",
                        "response": {"result": result_text},
                    }
                })
        if parts:
            out.append({"role": g_role, "parts": parts})
    return out


def _gemini_to_bedrock(
    data: dict, model_id: str, provider: str, duration_ms: int, config: dict
) -> dict:
    candidates = data.get("candidates") or []
    if not candidates:
        return LLMClient._empty_result(model_id, provider, "no candidates")

    content = candidates[0].get("content") or {}
    parts = content.get("parts") or []

    text_parts: list[str] = []
    tool_uses: list[dict] = []
    bedrock_content: list[dict] = []

    for p in parts:
        if "text" in p:
            text_parts.append(p["text"])
            bedrock_content.append({"text": p["text"]})
        elif "functionCall" in p:
            fc = p["functionCall"]
            tu = {
                "toolUseId": fc.get("name", "") + "_" + str(len(tool_uses)),
                "name": fc.get("name", ""),
                "input": fc.get("args") or {},
            }
            tool_uses.append(tu)
            bedrock_content.append({"toolUse": tu})

    usage = data.get("usageMetadata") or {}
    in_tok = int(usage.get("promptTokenCount") or 0)
    out_tok = int(usage.get("candidatesTokenCount") or 0)

    price_in = price_out = 0.0
    for m in config.get("models") or []:
        if m.get("id") == model_id:
            price_in = float(m.get("price_in") or 0)
            price_out = float(m.get("price_out") or 0)
            break
    cost = (in_tok / 1000.0) * price_in + (out_tok / 1000.0) * price_out

    finish = candidates[0].get("finishReason") or "STOP"
    stop_map = {
        "STOP": "end_turn",
        "MAX_TOKENS": "max_tokens",
        "SAFETY": "end_turn",
    }
    stop_reason = "tool_use" if tool_uses else stop_map.get(finish, "end_turn")

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
