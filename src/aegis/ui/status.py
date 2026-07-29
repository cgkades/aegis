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
        previous = self._presence
        self._presence = presence
        label = _LABELS.get(presence, presence.value)
        line = f"[Aegis] {label}"
        if detail:
            line += f" — {detail}"
        print(line, file=sys.stderr, flush=True)

        # Chime on transitions only. The session runner re-asserts ACTIVE after
        # every tool call, and re-chiming there is both noisy and a repeated
        # audio-device round trip in the middle of a live conversation.
        if presence is previous:
            return

        if presence is Presence.CONNECTING and self.chime_on_connecting:
            play_chime("connecting")
        elif presence is Presence.ACTIVE and self.chime_on_wake:
            play_chime("active")
        elif presence is Presence.ENDING and self.chime_on_end:
            play_chime("end")
        elif presence is Presence.APPROVAL:
            play_chime("approval")


# Chime players are never awaited: this runs on the asyncio loop that also
# streams mic audio, and blocking it for the length of a sound file drops
# frames. Children are reaped opportunistically on the next call instead.
_MAX_INFLIGHT_CHIMES = 4
_inflight_chimes: list[subprocess.Popen] = []


def _reap_chimes() -> None:
    for proc in list(_inflight_chimes):
        if proc.poll() is not None:
            _inflight_chimes.remove(proc)


def play_chime(kind: str = "active") -> None:
    """Best-effort system bell / paplay. Never raises, never blocks."""
    # Terminal bell as universal fallback
    try:
        sys.stderr.write("\a")
        sys.stderr.flush()
    except Exception:
        pass

    _reap_chimes()
    if len(_inflight_chimes) >= _MAX_INFLIGHT_CHIMES:
        # Audio sink is wedged or backed up; drop the chime rather than pile up.
        return

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
                    proc = subprocess.Popen(  # noqa: S603
                        ["paplay", path],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                except Exception:
                    pass
                else:
                    _inflight_chimes.append(proc)
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
