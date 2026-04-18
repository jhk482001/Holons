"""http_get — fetch the text content of a whitelisted URL.

Safe-ish: no write side effects. Constrained by:
  - only http(s) scheme
  - host must match an allowlisted pattern (configurable below)
  - 5 MB response cap, 10 second timeout
  - strips HTML tags to plain text before returning (the LLM doesn't need
    raw markup and we avoid shipping 1 MB of <script>)
"""
from __future__ import annotations

import re
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from . import register


# Allowlist of host suffixes. An incoming URL host must end with one of these.
# Expand cautiously — each addition is a new external attack surface.
ALLOWED_HOST_SUFFIXES = (
    "wikipedia.org",
    "wikimedia.org",
    "anthropic.com",
    "aws.amazon.com",
    "github.com",
    "raw.githubusercontent.com",
    "docs.python.org",
    "pypi.org",
    "httpbin.org",    # useful for tests
    "example.com",
    "example.org",
    "example.net",
)

MAX_BYTES = 5 * 1024 * 1024
TIMEOUT = 10.0


SPEC = {
    "name": "http_get",
    "description": (
        "Fetch the content of an HTTPS URL and return it as plain text. "
        "Only a short allowlist of trusted domains is reachable. Use this "
        "for citing documentation or a public reference page. NOT for "
        "arbitrary scraping. Returns {url, status, text}."
    ),
    "inputSchema": {
        "json": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "Absolute http(s) URL within the allowlisted hosts.",
                },
            },
            "required": ["url"],
        },
    },
}


def _strip_html(s: str) -> str:
    # Remove script/style blocks first then all tags.
    s = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", s, flags=re.DOTALL | re.IGNORECASE)
    s = re.sub(r"<[^>]+>", " ", s)
    # Collapse whitespace
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _host_allowed(host: str) -> bool:
    host = host.lower()
    return any(host == s or host.endswith("." + s) for s in ALLOWED_HOST_SUFFIXES)


def handler(args: dict, ctx: dict) -> dict:
    url = (args.get("url") or "").strip()
    if not url:
        return {"error": "url required"}
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return {"error": f"scheme {parsed.scheme!r} not allowed"}
    if not parsed.netloc:
        return {"error": "missing host"}
    host = parsed.netloc.split(":")[0]
    if not _host_allowed(host):
        return {
            "error": f"host {host!r} not in allowlist",
            "allowed_hosts": list(ALLOWED_HOST_SUFFIXES),
        }
    try:
        req = Request(url, headers={"User-Agent": "agent_company/1.0"})
        with urlopen(req, timeout=TIMEOUT) as resp:
            raw = resp.read(MAX_BYTES + 1)
            truncated = len(raw) > MAX_BYTES
            body = raw[:MAX_BYTES].decode("utf-8", errors="replace")
            status = resp.getcode() or 0
            ctype = resp.headers.get("content-type", "")
    except Exception as e:  # noqa: BLE001
        return {"error": f"fetch failed: {e}"}

    text = _strip_html(body) if "html" in ctype.lower() else body
    # Cap the returned text length so a single tool call doesn't blow out
    # the LLM context.
    if len(text) > 8000:
        text = text[:8000] + "\n...(truncated)"
    return {
        "url": url,
        "status": status,
        "content_type": ctype,
        "text": text,
        "truncated": truncated,
    }


register("http_get", SPEC, handler)
