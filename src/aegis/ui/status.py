"""Session disclosure: terminal status + optional chimes."""

from __future__ import annotations

import shutil
import subprocess
import sys
from enum import StrEnum
from pathlib import Path

from aegis.util.logging import get_logger

log = get_logger("ui.status")


class Presence(StrEnum):
    IDLE = "idle"
    CONNECTING = "connecting"
    ACTIVE = "active"
    APPROVAL = "approval"
    ENDING = "ending"


_LABELS = {
    Presence.IDLE: "Idle (local wake only)",
    Presence.CONNECTING: "Connecting to cloud voice…",
    Presence.ACTIVE: "Active — cloud session open",
    Presence.APPROVAL: "Approval required",
    Presence.ENDING: "Ending session…",
}


class StatusPresenter:
    """User-visible presence for trust (mic/cloud disclosure)."""

    def __init__(
        self,
        *,
        chime_on_wake: bool = True,
        chime_on_connecting: bool = True,
        chime_on_end: bool = False,
    ) -> None:
        self.chime_on_wake = chime_on_wake
        self.chime_on_connecting = chime_on_connecting
        self.chime_on_end = chime_on_end
        self._presence = Presence.IDLE

    @property
    def presence(self) -> Presence:
        return self._presence

    def set_presence(self, presence: Presence, *, detail: str = "") -> None:
        self._presence = presence
        label = _LABELS.get(presence, presence.value)
        line = f"[Aegis] {label}"
        if detail:
            line += f" — {detail}"
        print(line, file=sys.stderr, flush=True)

        if presence is Presence.CONNECTING and self.chime_on_connecting:
            play_chime("connecting")
        elif presence is Presence.ACTIVE and self.chime_on_wake:
            play_chime("active")
        elif presence is Presence.ENDING and self.chime_on_end:
            play_chime("end")
        elif presence is Presence.APPROVAL:
            play_chime("approval")


def play_chime(kind: str = "active") -> None:
    """Best-effort system bell / paplay. Never raises."""
    # Terminal bell as universal fallback
    try:
        sys.stderr.write("\a")
        sys.stderr.flush()
    except Exception:
        pass

    # Optional paplay with generated silence-free short blip if available
    if shutil.which("paplay") and Path("/usr/share/sounds").exists():
        candidates = [
            "/usr/share/sounds/freedesktop/stereo/message.oga",
            "/usr/share/sounds/freedesktop/stereo/bell.oga",
            "/usr/share/sounds/freedesktop/stereo/complete.oga",
        ]
        for path in candidates:
            if Path(path).is_file():
                try:
                    # run() reaps the child (no zombies under a long-lived daemon).
                    subprocess.run(  # noqa: S603
                        ["paplay", path],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        timeout=2,
                        check=False,
                    )
                except Exception:
                    pass
                break


def format_session_banner(
    *,
    session_id: str | None,
    model: str,
    backend: str,
    tools: list[str],
) -> str:
    tools_s = ", ".join(tools) if tools else "(none)"
    return (
        f"Aegis session {session_id or '?'} | backend={backend} | model={model}\n"
        f"Tools: {tools_s}\n"
        "Cloud audio is ACTIVE. End with Ctrl+C or goodbye."
    )
