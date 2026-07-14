"""Session activation: CLI/socket always works; global hotkey best-effort."""

from __future__ import annotations

import os
import threading
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from aegis.config.schema import ActivationConfig, HotkeyBackend
from aegis.ipc import send_request
from aegis.util.logging import get_logger

log = get_logger("activation")


class ActivationBackend(StrEnum):
    NONE = "none"
    X11_PYNPUT = "x11_pynput"
    WAYLAND_EXTERNAL = "wayland_external"
    EVDEV = "evdev"
    SOCKET = "socket"


@dataclass(frozen=True, slots=True)
class ActivationInfo:
    backend: ActivationBackend
    hotkey: str
    notes: str


def detect_session_type() -> str:
    return os.environ.get("XDG_SESSION_TYPE", "").lower() or "unknown"


def detect_hotkey_backend(config: ActivationConfig) -> ActivationInfo:
    """Choose a hotkey strategy for this desktop environment."""
    if config.hotkey_backend is HotkeyBackend.NONE:
        return ActivationInfo(
            ActivationBackend.NONE,
            config.hotkey,
            "Global hotkey disabled; use CLI/socket or DE keybind → aegis session start",
        )

    session = detect_session_type()
    preferred = config.hotkey_backend

    if preferred is HotkeyBackend.WAYLAND_EXTERNAL or (
        preferred is HotkeyBackend.AUTO and session == "wayland"
    ):
        return ActivationInfo(
            ActivationBackend.WAYLAND_EXTERNAL,
            config.hotkey,
            (
                "Wayland: configure a DE custom shortcut to run "
                "`aegis session start` (global grab is best-effort only)."
            ),
        )

    if preferred in {HotkeyBackend.X11_PYNPUT, HotkeyBackend.AUTO}:
        if _pynput_available() and session in {"x11", "unknown", ""}:
            return ActivationInfo(
                ActivationBackend.X11_PYNPUT,
                config.hotkey,
                "X11 pynput global hotkey (best-effort).",
            )

    if preferred is HotkeyBackend.EVDEV:
        return ActivationInfo(
            ActivationBackend.EVDEV,
            config.hotkey,
            "evdev requires device access / udev rules; not auto-enabled.",
        )

    return ActivationInfo(
        ActivationBackend.SOCKET,
        config.hotkey,
        "No global grab; use `aegis session start` or DE keybind → socket.",
    )


def _pynput_available() -> bool:
    try:
        import pynput  # noqa: F401

        return True
    except Exception:
        return False


class HotkeyListener:
    """Optional global hotkey → callback. Never required for core function."""

    def __init__(
        self,
        config: ActivationConfig,
        on_activate: Callable[[], None],
    ) -> None:
        self.config = config
        self.on_activate = on_activate
        self.info = detect_hotkey_backend(config)
        self._listener = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def start(self) -> ActivationInfo:
        if self.info.backend is not ActivationBackend.X11_PYNPUT:
            log.info("hotkey backend=%s — %s", self.info.backend.value, self.info.notes)
            return self.info
        try:
            from pynput import keyboard
        except Exception as exc:
            log.warning("pynput unavailable: %s", exc)
            self.info = ActivationInfo(
                ActivationBackend.SOCKET,
                self.config.hotkey,
                f"pynput failed: {exc}",
            )
            return self.info

        combo = self._parse_hotkey(self.config.hotkey)
        if combo is None:
            log.warning("could not parse hotkey %r", self.config.hotkey)
            return self.info

        def on_press(key) -> None:  # noqa: ANN001
            pass

        # Use GlobalHotKeys for chord
        try:
            mapping = {combo: self._safe_activate}
            self._listener = keyboard.GlobalHotKeys(mapping)
            self._listener.start()
            log.info("global hotkey active: %s", self.config.hotkey)
        except Exception as exc:
            log.warning("failed to bind hotkey: %s", exc)
            self.info = ActivationInfo(
                ActivationBackend.SOCKET,
                self.config.hotkey,
                f"bind failed: {exc}",
            )
        return self.info

    def stop(self) -> None:
        self._stop.set()
        if self._listener is not None:
            try:
                self._listener.stop()
            except Exception:
                pass
            self._listener = None

    def _safe_activate(self) -> None:
        try:
            self.on_activate()
        except Exception as exc:
            log.error("activation callback failed: %s", exc)

    @staticmethod
    def _parse_hotkey(hotkey: str) -> str | None:
        """Convert 'Super+Shift+Space' → pynput format '<cmd>+<shift>+<space>'."""
        if not hotkey:
            return None
        parts = [p.strip().lower() for p in hotkey.replace("-", "+").split("+") if p.strip()]
        mapped = []
        for p in parts:
            if p in {"super", "meta", "win", "cmd"}:
                mapped.append("<cmd>")
            elif p == "ctrl" or p == "control":
                mapped.append("<ctrl>")
            elif p == "alt":
                mapped.append("<alt>")
            elif p == "shift":
                mapped.append("<shift>")
            elif len(p) == 1:
                mapped.append(p)
            else:
                mapped.append(f"<{p}>")
        return "+".join(mapped) if mapped else None


async def activate_via_socket(socket_path, source: str = "hotkey") -> dict:
    """Always-works activation path used by DE keybinds and optional hotkey."""
    resp = await send_request(socket_path, "session.start", {"source": source})
    if not resp.ok:
        raise RuntimeError(resp.error or "activation failed")
    return resp.result or {}


def print_activation_help(config: ActivationConfig) -> None:
    info = detect_hotkey_backend(config)
    print(f"backend: {info.backend.value}")
    print(f"hotkey:  {config.hotkey}")
    print(f"notes:   {info.notes}")
    print()
    print("Always-works activation:")
    print("  aegis session start")
    print("  # or DE shortcut command:")
    print("  aegis session start")
    if info.backend is ActivationBackend.WAYLAND_EXTERNAL:
        print()
        print("GNOME: Settings → Keyboard → Custom Shortcut → command: aegis session start")
        print("KDE:   System Settings → Shortcuts → Custom → aegis session start")
