"""Bedrock wrapper around the existing `backend.bedrock_client.invoke()`.

This is the one provider that already has a mature, battle-tested call path
in the codebase. We wrap it here so the engine can dispatch through the
generic LLMClient interface instead of importing bedrock_client directly.

Credential handling:
- If `credential` dict has access_key + secret_key, we build a fresh boto3
  client with those.
- Otherwise fall back to the existing `bedrock_client.runtime()` which reads
  from process env / env.config.
"""
from __future__ import annotations

import time
from typing import Optional

import boto3
from botocore.config import Config as BotoConfig

from .base import LLMClient


class BedrockLLMClient(LLMClient):
    provider = "bedrock"

    def __init__(self, *, client_row: dict):
        super().__init__(client_row=client_row)
        self._runtime = None

    # Look up pricing (per 1k in/out tokens) from the client's config.models
    # list so admins can override defaults without a code change.
    def _pricing_for(self, model_id: str) -> tuple[float, float]:
        for m in self.config.get("models") or []:
            if m.get("id") == model_id:
                return float(m.get("price_in") or 0), float(m.get("price_out") or 0)
        # Fall back to the legacy registry
        from ..bedrock_client import resolve
        info = resolve(model_id)
        return float(info.get("price_in") or 0), float(info.get("price_out") or 0)

    def _get_runtime(self):
        if self._runtime is not None:
            return self._runtime
        region = self.config.get("region") or "ap-northeast-1"
        access_key = self.credential.get("access_key")
        secret_key = self.credential.get("secret_key")
        if access_key and secret_key:
            self._runtime = boto3.client(
                "bedrock-runtime",
                aws_access_key_id=access_key,
                aws_secret_access_key=secret_key,
                region_name=region,
                config=BotoConfig(
                    connect_timeout=15,
                    read_timeout=600,
                    retries={"max_attempts": 2, "mode": "standard"},
                ),
            )
        else:
            # Legacy path — use env.config credentials via bedrock_client
            from ..bedrock_client import runtime
            self._runtime = runtime()
        return self._runtime

    def _resolve_model_id(self, model_id: str) -> str:
        """If model_id is an old-style friendly key (e.g. 'claude-sonnet-4.6'),
        resolve it to the actual Bedrock model ID via the legacy registry.
        If it's already a Bedrock-format ID (contains '.' as provider sep
        like 'jp.anthropic.claude-sonnet-4-6'), pass through as-is."""
        if "." in model_id and "/" not in model_id:
            parts = model_id.split(".")
            if len(parts) >= 3:
                return model_id
        from ..bedrock_client import resolve
        info = resolve(model_id)
        return info.get("model_id") or model_id

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
        model_id = self._resolve_model_id(model_id)
        rt = self._get_runtime()

        kwargs = {
            "modelId": model_id,
            "messages": messages,
            "inferenceConfig": {"maxTokens": max_tokens, "temperature": temperature},
        }
        if system_prompt:
            kwargs["system"] = [{"text": system_prompt}]
        if tool_config:
            kwargs["toolConfig"] = {
                "tools": [{"toolSpec": spec} for spec in tool_config],
            }

        t0 = time.time()
        err = None
        text = ""
        tool_uses: list[dict] = []
        stop_reason = "end_turn"
        assistant_message: dict = {"role": "assistant", "content": []}
        in_tok = out_tok = 0
        try:
            resp = rt.converse(**kwargs)
            assistant_message = resp["output"]["message"]
            parts: list[str] = []
            for block in assistant_message.get("content", []):
                if "text" in block:
                    parts.append(block["text"])
                elif "toolUse" in block:
                    tool_uses.append(block["toolUse"])
            text = "".join(parts)
            stop_reason = resp.get("stopReason", "end_turn")
            usage = resp.get("usage", {})
            in_tok = usage.get("inputTokens", 0) or 0
            out_tok = usage.get("outputTokens", 0) or 0
        except Exception as e:
            err = str(e)
            text = f"[ERROR invoking {model_id}: {err}]"
            stop_reason = "error"

        duration_ms = int((time.time() - t0) * 1000)
        price_in, price_out = self._pricing_for(model_id)
        cost = (in_tok / 1000.0) * price_in + (out_tok / 1000.0) * price_out

        return {
            "text": text,
            "tool_uses": tool_uses,
            "stop_reason": stop_reason,
            "assistant_message": assistant_message,
            "input_tokens": in_tok,
            "output_tokens": out_tok,
            "cost_usd": round(cost, 6),
            "duration_ms": duration_ms,
            "model_id": model_id,
            "provider": self.provider,
            "error": err,
        }

    # ------------------------------------------------------------------
    # Streaming path — wraps Bedrock's ConverseStream API. Yields text
    # deltas as they arrive, then a final "complete" dict identical in
    # shape to invoke()'s return so callers can swap implementations.
    # ------------------------------------------------------------------
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
        model_id = self._resolve_model_id(model_id)
        rt = self._get_runtime()

        kwargs = {
            "modelId": model_id,
            "messages": messages,
            "inferenceConfig": {"maxTokens": max_tokens, "temperature": temperature},
        }
        if system_prompt:
            kwargs["system"] = [{"text": system_prompt}]
        if tool_config:
            kwargs["toolConfig"] = {
                "tools": [{"toolSpec": spec} for spec in tool_config],
            }

        t0 = time.time()
        err = None
        text_parts: list[str] = []
        tool_uses: list[dict] = []
        tool_state: dict = {}  # toolUseId → partial input json chunks
        stop_reason = "end_turn"
        in_tok = out_tok = 0
        try:
            resp = rt.converse_stream(**kwargs)
            for ev in resp.get("stream") or []:
                # Text deltas come in "contentBlockDelta" with "text" key;
                # tool-use JSON comes with "toolUse" key. We fold tool-use
                # chunks into tool_state rather than streaming them to the
                # UI (partial JSON is useless to the user).
                if "contentBlockDelta" in ev:
                    delta = ev["contentBlockDelta"].get("delta", {})
                    if "text" in delta:
                        chunk = delta["text"]
                        text_parts.append(chunk)
                        yield ("chunk", chunk)
                    elif "toolUse" in delta:
                        idx = ev["contentBlockDelta"].get("contentBlockIndex", 0)
                        tool_state.setdefault(idx, {"input_json": ""})
                        tool_state[idx]["input_json"] += delta["toolUse"].get("input", "")
                elif "contentBlockStart" in ev:
                    start = ev["contentBlockStart"].get("start", {})
                    if "toolUse" in start:
                        idx = ev["contentBlockStart"].get("contentBlockIndex", 0)
                        tu = start["toolUse"]
                        tool_state[idx] = {
                            "toolUseId": tu.get("toolUseId"),
                            "name": tu.get("name"),
                            "input_json": "",
                        }
                elif "messageStop" in ev:
                    stop_reason = ev["messageStop"].get("stopReason", "end_turn")
                elif "metadata" in ev:
                    usage = ev["metadata"].get("usage", {})
                    in_tok = usage.get("inputTokens", 0) or 0
                    out_tok = usage.get("outputTokens", 0) or 0
        except Exception as e:  # noqa: BLE001
            err = str(e)
            text_parts.append(f"[ERROR streaming {model_id}: {err}]")
            yield ("chunk", f"[ERROR streaming {model_id}: {err}]")
            stop_reason = "error"

        # Finalise tool_uses: parse any accumulated JSON.
        import json as _json
        for slot in tool_state.values():
            if slot.get("toolUseId"):
                try:
                    parsed = _json.loads(slot.get("input_json") or "{}")
                except Exception:
                    parsed = {"_raw": slot.get("input_json", "")}
                tool_uses.append({
                    "toolUseId": slot["toolUseId"],
                    "name": slot.get("name"),
                    "input": parsed,
                })

        text = "".join(text_parts)
        duration_ms = int((time.time() - t0) * 1000)
        price_in, price_out = self._pricing_for(model_id)
        cost = (in_tok / 1000.0) * price_in + (out_tok / 1000.0) * price_out

        # Rebuild assistant_message blocks so the engine's tool-loop can
        # consume it exactly like the batch path. Text blocks first, then
        # any tool_use blocks — matches Bedrock Converse output order.
        blocks: list[dict] = []
        if text:
            blocks.append({"text": text})
        for tu in tool_uses:
            blocks.append({"toolUse": tu})
        assistant_message = {"role": "assistant", "content": blocks}

        yield ("complete", {
            "text": text,
            "tool_uses": tool_uses,
            "stop_reason": stop_reason,
            "assistant_message": assistant_message,
            "input_tokens": in_tok,
            "output_tokens": out_tok,
            "cost_usd": round(cost, 6),
            "duration_ms": duration_ms,
            "model_id": model_id,
            "provider": self.provider,
            "error": err,
        })
