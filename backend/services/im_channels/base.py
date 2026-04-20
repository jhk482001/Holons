"""Abstract IM platform adapter.

Each concrete adapter (Telegram, Slack, LINE, …) implements the four
methods below. The manager owns a thread per enabled binding and calls
`poll_once` repeatedly; inbound messages are fed to `router.dispatch`,
which translates them into `lead_agent.chat` calls and sends the reply
back via `send`.

Keeping the surface tight (send / send_typing / send_image / poll_once)
makes it trivial to add a new platform: the hard plumbing (session
continuity, reply formatting, command dispatch) is shared.
"""
from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Iterable


@dataclass
class InboundMessage:
    """Normalised inbound message across every platform."""
    platform: str          # 'telegram', 'slack', ...
    external_id: str       # chat id / user id on the platform
    sender_display: str    # for logs and logging
    text: str              # plain text — rich formatting stripped
    raw: dict              # original platform payload for debugging


@dataclass
class DispatchResult:
    """What the router returns after processing one inbound message.
    Text is the reply prose; artifacts is a parallel list of structured
    artifact payloads (html/slides/markdown/file) that the adapter
    should try to deliver in its native rich form before falling back
    to a text breadcrumb."""
    text: str | None = None
    artifacts: list[dict] = field(default_factory=list)


class BasePlatformAdapter(abc.ABC):
    """Each concrete adapter implements this surface."""

    platform: str = ""  # override in subclass

    def __init__(self, binding: dict):
        """`binding` is a row from the `im_bindings` table, with
        `secret_encrypted` already decrypted into `secret` by the caller."""
        self.binding = binding
        self.user_id: int = binding["user_id"]
        self.external_id: str | None = binding.get("external_id")
        self.secret: str | None = binding.get("secret")

    @abc.abstractmethod
    def poll_once(self) -> Iterable[InboundMessage]:
        """Fetch any new messages. Called repeatedly in a loop by the
        manager. Block for up to a few seconds if long-polling; return
        empty iterable if nothing. Should not raise on transient network
        errors — log + return empty."""

    @abc.abstractmethod
    def send(self, external_id: str, text: str) -> None:
        """Send a text message back to the platform chat."""

    @abc.abstractmethod
    def send_typing(self, external_id: str) -> None:
        """Indicate agent is working. Best-effort — may be a no-op."""

    def send_artifact(self, external_id: str, artifact: dict) -> bool:
        """Best-effort rich delivery of one artifact. Return True if
        the adapter handled the artifact natively (file upload,
        rendered image, etc.), False if the caller should fall back to
        sending a text breadcrumb via `send()`. Default is False so
        adapters that don't implement this just degrade gracefully."""
        return False

    def close(self) -> None:
        """Cleanup — close any persistent connections. Default no-op."""
