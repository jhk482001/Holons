"""Bedrock runtime wrapper using the unified Converse API.

A model registry lets the UI expose friendly names. Cost is computed per
model based on published on-demand pricing (USD per 1K tokens). Prices
can drift, so treat the numbers as a close estimate, not an invoice.
"""
import time
from typing import List, Dict, Any, Optional

import boto3
from botocore.config import Config as BotoConfig

from .config import CFG


# Friendly name -> (bedrock model/profile id, provider, price_in_per_1k, price_out_per_1k)
MODEL_REGISTRY: Dict[str, Dict[str, Any]] = {
    "claude-opus-4.6": {
        "model_id": "global.anthropic.claude-opus-4-6-v1",
        "provider": "anthropic",
        "price_in": 0.015,
        "price_out": 0.075,
        "supports_image": True,
        "label": "Claude Opus 4.6 (top quality)",
    },
    "claude-sonnet-4.6": {
        "model_id": "jp.anthropic.claude-sonnet-4-6",
        "provider": "anthropic",
        "price_in": 0.003,
        "price_out": 0.015,
        "supports_image": True,
        "label": "Claude Sonnet 4.6 (balanced)",
    },
    "claude-haiku-4.5": {
        "model_id": "jp.anthropic.claude-haiku-4-5-20251001-v1:0",
        "provider": "anthropic",
        "price_in": 0.001,
        "price_out": 0.005,
        "supports_image": True,
        "label": "Claude Haiku 4.5 (fast)",
    },
    "nova-2-lite": {
        "model_id": "jp.amazon.nova-2-lite-v1:0",
        "provider": "amazon",
        "price_in": 0.00006,
        "price_out": 0.00024,
        "supports_image": True,
        "label": "Amazon Nova 2 Lite",
    },
    "nova-pro": {
        "model_id": "apac.amazon.nova-pro-v1:0",
        "provider": "amazon",
        "price_in": 0.0008,
        "price_out": 0.0032,
        "supports_image": True,
        "label": "Amazon Nova Pro",
    },
    "nova-lite": {
        "model_id": "apac.amazon.nova-lite-v1:0",
        "provider": "amazon",
        "price_in": 0.00006,
        "price_out": 0.00024,
        "supports_image": True,
        "label": "Amazon Nova Lite",
    },
    "llama-3.1-70b": {
        "model_id": "meta.llama3-1-70b-instruct-v1:0",
        "provider": "meta",
        "price_in": 0.00072,
        "price_out": 0.00072,
        "supports_image": False,
        "label": "Meta Llama 3.1 70B (may be unavailable in this region)",
    },
}


_runtime = None


def runtime():
    global _runtime
    if _runtime is None:
        _runtime = boto3.client(
            "bedrock-runtime",
            aws_access_key_id=CFG["AWS_ACCESS_KEY"],
            aws_secret_access_key=CFG["AWS_SECRET_KEY"],
            region_name=CFG.get("AWS_REGION", "ap-northeast-1"),
            config=BotoConfig(
                connect_timeout=15,
                read_timeout=600,
                retries={"max_attempts": 2, "mode": "standard"},
            ),
        )
    return _runtime


def list_models() -> List[Dict[str, Any]]:
    out = []
    for key, v in MODEL_REGISTRY.items():
        out.append({
            "key": key,
            "label": v["label"],
            "provider": v["provider"],
            "supports_image": v["supports_image"],
        })
    return out


def resolve(friendly_key: str) -> Dict[str, Any]:
    if friendly_key in MODEL_REGISTRY:
        return MODEL_REGISTRY[friendly_key]
    # Allow raw model IDs to pass through with zero-cost bookkeeping.
    return {
        "model_id": friendly_key,
        "provider": "unknown",
        "price_in": 0.0,
        "price_out": 0.0,
        "supports_image": False,
        "label": friendly_key,
    }


def _build_content(user_text: str, images: Optional[List[Dict[str, Any]]] = None):
    """Build a Converse content list. Each image dict: {mime, bytes}."""
    content = []
    if images:
        for img in images:
            fmt = (img.get("mime") or "image/png").split("/")[-1]
            if fmt == "jpeg":
                fmt = "jpeg"
            content.append({
                "image": {
                    "format": fmt,
                    "source": {"bytes": img["bytes"]},
                }
            })
    content.append({"text": user_text})
    return content


def invoke(
    model_key: str,
    system_prompt: str,
    user_text: str = "",
    history: Optional[List[Dict[str, Any]]] = None,
    images: Optional[List[Dict[str, Any]]] = None,
    max_tokens: int = 4096,
    temperature: float = 0.7,
    messages: Optional[List[Dict[str, Any]]] = None,
    tool_config: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Invoke a model via the Converse API.

    Two calling styles:
      - Simple: pass user_text (+ optional history). We build a single-turn
        message for you. This is the existing 1-shot path used by Lead
        chat, workflow node execution, skill extractor, etc.
      - Advanced: pass `messages` directly (full Converse content blocks,
        including prior assistant toolUse / user toolResult entries) to
        continue a tool-use loop. Used by the tool-aware execute path.

    When `tool_config` is provided, it's passed through as the Converse
    `toolConfig` and the returned dict includes `tool_uses` (list of
    {toolUseId, name, input}) plus `stop_reason` ("end_turn" / "tool_use" /
    "max_tokens"). The caller is responsible for the loop.

    Returns:
      {
        text: str,                  # concatenated text blocks from the reply
        tool_uses: list[dict],      # toolUse blocks from the reply
        stop_reason: str,           # Converse stopReason
        assistant_message: dict,    # raw assistant message (append to history)
        input_tokens, output_tokens, cost_usd, duration_ms, model_id, provider, error
      }
    """
    info = resolve(model_key)
    model_id = info["model_id"]
    rt = runtime()

    if messages is not None:
        msgs = messages
    else:
        msgs = []
        if history:
            for h in history:
                role = h.get("role", "user")
                if role not in ("user", "assistant"):
                    continue
                msgs.append({"role": role, "content": [{"text": h.get("content", "")}]})
        msgs.append({"role": "user", "content": _build_content(user_text, images)})

    kwargs = {
        "modelId": model_id,
        "messages": msgs,
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
    tool_uses: List[Dict[str, Any]] = []
    stop_reason = "end_turn"
    assistant_message: Dict[str, Any] = {"role": "assistant", "content": []}
    in_tok = out_tok = 0
    try:
        resp = rt.converse(**kwargs)
        assistant_message = resp["output"]["message"]
        text_parts = []
        for block in assistant_message.get("content", []):
            if "text" in block:
                text_parts.append(block["text"])
            elif "toolUse" in block:
                tool_uses.append(block["toolUse"])
        text = "".join(text_parts)
        stop_reason = resp.get("stopReason", "end_turn")
        usage = resp.get("usage", {})
        in_tok = usage.get("inputTokens", 0) or 0
        out_tok = usage.get("outputTokens", 0) or 0
    except Exception as e:
        err = str(e)
        text = f"[ERROR invoking {model_id}: {err}]"
    duration_ms = int((time.time() - t0) * 1000)

    cost = (in_tok / 1000.0) * info["price_in"] + (out_tok / 1000.0) * info["price_out"]
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
        "provider": info["provider"],
        "error": err,
    }
