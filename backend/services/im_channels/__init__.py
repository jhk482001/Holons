"""IM channel integrations — Telegram first, more adapters to follow.

Each platform (Telegram, Slack, LINE, Discord, …) is a concrete subclass
of BasePlatformAdapter. The manager owns lifecycle: polling threads, start
/ stop, restart on token rotation. The router is the funnel point —
every inbound message, regardless of platform, ends up calling
`lead_agent.chat()` through the same session continuity logic.
"""
from .base import BasePlatformAdapter  # noqa: F401
from .manager import start_all, stop_all, reload_user  # noqa: F401
