"""Context manager, UI, tray, logging helpers."""

from __future__ import annotations

from aegis.config.schema import SessionContextConfig
from aegis.session.context import ContextManager
from aegis.ui.status import Presence, StatusPresenter, format_session_banner, play_chime
from aegis.ui.tray import TrayIcon
from aegis.util.logging import get_logger, reset_logging_for_tests, setup_logging


def test_context_snapshot_and_summary() -> None:
    cm = ContextManager(
        SessionContextConfig(max_transcript_turns=40, summarize_when_turns_exceed=3)
    )
    cm.add_transcript("user", "hello")
    cm.add_transcript("assistant", "hi")
    cm.add_transcript("user", "more")
    assert cm.needs_summary
    cm.add_tool_result("read_file", "x" * 20_000)
    snap = cm.snapshot_for_prompt()
    assert "transcript" in snap.lower() or "user" in snap
    assert cm.pressure_report()["turns"] == 3


def test_status_presenter(capsys) -> None:
    sp = StatusPresenter(chime_on_wake=False, chime_on_connecting=False, chime_on_end=False)
    sp.set_presence(Presence.CONNECTING)
    sp.set_presence(Presence.ACTIVE, detail="id=1")
    sp.set_presence(Presence.APPROVAL)
    sp.set_presence(Presence.ENDING)
    sp.set_presence(Presence.IDLE)
    assert sp.presence is Presence.IDLE
    err = capsys.readouterr().err
    assert "Aegis" in err


def test_format_banner() -> None:
    text = format_session_banner(
        session_id="abc",
        model="mini",
        backend="mock",
        tools=["list_dir"],
    )
    assert "abc" in text
    assert "list_dir" in text


def test_play_chime_no_raise() -> None:
    play_chime("active")


def test_tray() -> None:
    t = TrayIcon()
    assert t.start() is False
    t.set_state("active")
    t.stop()


def test_logging_setup() -> None:
    reset_logging_for_tests()
    log = setup_logging("debug")
    log.info("test")
    log2 = get_logger("x")
    assert log2.name.startswith("aegis")
    setup_logging("info")  # reconfigure
    reset_logging_for_tests()
