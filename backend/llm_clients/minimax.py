"""Minimax chatcompletion v2 client.

https://api.minimax.chat/v1/text/chatcompletion_v2

Minimax's wire format is OpenAI-compatible for the basic case (chat messages
+ usage + choices), so we reuse the OpenAI translators but with a different
auth header shape.
"""
from __future__ import annotations

import time
import urllib.request
import urllib.error
import json
from typing import Optional

from .base import LLMClient
from .openai_compat import (
    _bedrock_messages_to_openai,
    _openai_response_to_bedrock,
)


class MinimaxLLMClient(LLMClient):
    provider = "minimax"

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
        group_id = self.config.get("group_id") or ""

        url = "https://api.minimax.chat/v1/text/chatcompletion_v2"
        if group_id:
            url += f"?GroupId={group_id}"

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
            req = urllib.request.Request(
                url, data=json.dumps(body).encode("utf-8"), headers=headers
            )
            with urllib.request.urlopen(req, timeout=600) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", "replace")
            return self._empty_result(model_id, self.provider, f"HTTP {e.code}: {err_body[:300]}")
        except Exception as e:
            return self._empty_result(model_id, self.provider, str(e))
        duration_ms = int((time.time() - t0) * 1000)
        return _openai_response_to_bedrock(
            data, model_id, self.provider, duration_ms, self.config
        )
