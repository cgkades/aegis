"""CloudAudioGateway — sole path for cloud audio sockets.

Idle code must never open network audio. Only VoiceSession.connect goes through here.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

from aegis.util.logging import get_logger

log = get_logger("voice.gateway")


class GatewayError(Exception):
    """Raised when cloud audio connect is refused or fails policy checks."""


@dataclass
class CloudAudioGateway:
    """Gatekeeper for OpenAI Realtime (or future GPT-Live) connections.

    Tracks whether a cloud audio session is open so Idle assertions and
    ``aegis doctor`` can verify no idle sockets.
    """

    allowed_hosts: tuple[str, ...] = ("api.openai.com",)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _active_sessions: int = 0
    _last_url: str | None = None

    @property
    def is_open(self) -> bool:
        with self._lock:
            return self._active_sessions > 0

    @property
    def active_sessions(self) -> int:
        with self._lock:
            return self._active_sessions

    def assert_idle_has_no_cloud(self) -> None:
        """Raise if any cloud audio session is still open (Idle invariant)."""
        if self.is_open:
            raise GatewayError(
                f"CloudAudioGateway still open ({self._active_sessions} session(s)); "
                "Idle path must not hold cloud audio sockets"
            )

    def authorize_connect(self, url: str) -> None:
        """Validate destination before any WebSocket is opened."""
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        if not host:
            raise GatewayError(f"invalid realtime url: {url}")
        # Local mock sessions only (unit tests) — never used for production audio.
        if parsed.scheme == "ws" and host in {"localhost", "127.0.0.1"}:
            return
        if parsed.scheme not in {"wss", "https"}:
            raise GatewayError(f"cloud audio requires wss://, got {parsed.scheme}")
        if host not in self.allowed_hosts and not host.endswith(".openai.com"):
            raise GatewayError(
                f"refusing cloud audio connect to non-OpenAI host: {host}"
            )

    def register_open(self, url: str) -> None:
        self.authorize_connect(url)
        with self._lock:
            self._active_sessions += 1
            self._last_url = url
            log.info("cloud audio session open count=%s", self._active_sessions)

    def register_close(self) -> None:
        with self._lock:
            self._active_sessions = max(0, self._active_sessions - 1)
            log.info("cloud audio session closed count=%s", self._active_sessions)

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "active_sessions": self._active_sessions,
                "last_url": self._last_url,
                "is_open": self._active_sessions > 0,
            }


# Process-wide default gateway (single-user daemon).
default_gateway = CloudAudioGateway()
