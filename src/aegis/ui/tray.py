"""Optional system tray (best-effort; not required for MVP)."""

from __future__ import annotations

from aegis.util.logging import get_logger

log = get_logger("ui.tray")


class TrayIcon:
    """Placeholder tray — real AppIndicator/StatusNotifier can plug in later."""

    def __init__(self) -> None:
        self._available = False

    def start(self) -> bool:
        # Avoid hard dependency on GUI toolkits in headless/CI
        log.info("tray not implemented; use CLI status / chimes for disclosure")
        self._available = False
        return False

    def set_state(self, state: str) -> None:
        return None

    def stop(self) -> None:
        return None
