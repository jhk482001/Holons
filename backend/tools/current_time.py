"""current_time — return the current wall clock in a given timezone.

Safe: pure computation, no I/O. Useful when the agent needs to reason
about "today" or "is it business hours" without relying on model cutoff.
"""
from __future__ import annotations

from datetime import datetime, timezone

from . import register


SPEC = {
    "name": "current_time",
    "description": (
        "Return the current date and time. Optionally specify an IANA "
        "timezone (e.g., 'Asia/Taipei', 'UTC'). Use this when you need "
        "'today' or 'now' and cannot rely on training data."
    ),
    "inputSchema": {
        "json": {
            "type": "object",
            "properties": {
                "tz": {
                    "type": "string",
                    "description": "IANA timezone identifier (default: Asia/Taipei).",
                },
            },
            "required": [],
        },
    },
}


def handler(args: dict, ctx: dict) -> dict:
    tz_name = (args.get("tz") or "Asia/Taipei").strip()
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(tz_name)
        now = datetime.now(tz)
    except Exception:
        # Fall back to UTC if the requested zone is unknown
        tz_name = "UTC"
        now = datetime.now(timezone.utc)
    return {
        "iso": now.isoformat(),
        "date": now.strftime("%Y-%m-%d"),
        "time": now.strftime("%H:%M:%S"),
        "weekday": now.strftime("%A"),
        "timezone": tz_name,
    }


register("current_time", SPEC, handler)
