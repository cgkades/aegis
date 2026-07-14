"""Activation / hotkey backend tests."""

from __future__ import annotations

from aegis.activation import (
    ActivationBackend,
    HotkeyListener,
    detect_hotkey_backend,
    detect_session_type,
    print_activation_help,
)
from aegis.config.schema import ActivationConfig, HotkeyBackend


def test_detect_session_type(monkeypatch) -> None:
    monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
    assert detect_session_type() == "wayland"
    monkeypatch.delenv("XDG_SESSION_TYPE", raising=False)
    assert detect_session_type() in {"unknown", ""}


def test_wayland_uses_external(monkeypatch) -> None:
    monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
    info = detect_hotkey_backend(ActivationConfig(hotkey_backend=HotkeyBackend.AUTO))
    assert info.backend is ActivationBackend.WAYLAND_EXTERNAL
    assert "aegis session start" in info.notes


def test_none_backend() -> None:
    info = detect_hotkey_backend(ActivationConfig(hotkey_backend=HotkeyBackend.NONE))
    assert info.backend is ActivationBackend.NONE


def test_evdev_backend() -> None:
    info = detect_hotkey_backend(ActivationConfig(hotkey_backend=HotkeyBackend.EVDEV))
    assert info.backend is ActivationBackend.EVDEV


def test_parse_hotkey() -> None:
    assert HotkeyListener._parse_hotkey("Super+Shift+Space") is not None
    assert HotkeyListener._parse_hotkey("Ctrl+Alt+a") is not None
    assert HotkeyListener._parse_hotkey("") is None


def test_hotkey_listener_start_stop_no_pynput(monkeypatch) -> None:
    monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
    calls: list[int] = []
    listener = HotkeyListener(
        ActivationConfig(hotkey_backend=HotkeyBackend.AUTO),
        on_activate=lambda: calls.append(1),
    )
    info = listener.start()
    assert info.backend is ActivationBackend.WAYLAND_EXTERNAL
    listener.stop()


def test_print_activation_help(capsys) -> None:
    print_activation_help(ActivationConfig())
    out = capsys.readouterr().out
    assert "aegis session start" in out


def test_safe_activate_handles_error() -> None:
    def boom() -> None:
        raise RuntimeError("fail")

    listener = HotkeyListener(ActivationConfig(hotkey_backend=HotkeyBackend.NONE), boom)
    listener._safe_activate()  # must not raise
