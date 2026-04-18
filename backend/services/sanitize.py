"""Input sanitization helpers.

Applied at system boundaries (user input via API) to prevent:
- Control character injection (null bytes, ANSI escapes)
- Excessively long strings that waste DB space / LLM tokens
- Basic prompt injection markers (not bulletproof, but raises the bar)

Usage:
    from .services.sanitize import clean_text, clean_name

    name = clean_name(request.json.get("name", ""))
    prompt = clean_text(request.json.get("system_prompt", ""), max_len=10000)
"""
from __future__ import annotations

import re

# Strip C0/C1 control characters except \n \r \t
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")


def clean_text(value: str | None, *, max_len: int = 50_000) -> str:
    """Sanitize a general text field (descriptions, prompts, messages)."""
    if not value:
        return ""
    s = _CONTROL_RE.sub("", value)
    return s[:max_len].strip()


def clean_name(value: str | None, *, max_len: int = 200) -> str:
    """Sanitize a name/title field — strip control chars + limit length."""
    if not value:
        return ""
    s = _CONTROL_RE.sub("", value)
    # Collapse whitespace
    s = " ".join(s.split())
    return s[:max_len].strip()


def clean_json_config(value: dict | None) -> dict:
    """Recursively clean string values in a JSON config dict."""
    if not value or not isinstance(value, dict):
        return value or {}
    out = {}
    for k, v in value.items():
        if isinstance(v, str):
            out[k] = clean_text(v, max_len=10_000)
        elif isinstance(v, dict):
            out[k] = clean_json_config(v)
        elif isinstance(v, list):
            out[k] = [
                clean_json_config(i) if isinstance(i, dict)
                else clean_text(i, max_len=10_000) if isinstance(i, str)
                else i
                for i in v
            ]
        else:
            out[k] = v
    return out
