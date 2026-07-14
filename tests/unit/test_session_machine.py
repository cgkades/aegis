"""Session state machine tests."""

from __future__ import annotations

import pytest

from aegis.session import (
    InvalidTransition,
    SessionMachine,
    SessionState,
    Trigger,
)


def test_wake_confirm_connect_active_end() -> None:
    m = SessionMachine()
    assert m.state is SessionState.IDLE

    m.trigger(Trigger.WAKE_WORD, confirm_enabled=True)
    assert m.state is SessionState.WAKING
    assert m.context.session_id is not None

    m.trigger(Trigger.CAPTURE_READY)
    assert m.state is SessionState.CONFIRMING

    m.trigger(Trigger.SPEECH_CONFIRMED)
    assert m.state is SessionState.CONNECTING

    m.trigger(Trigger.SESSION_READY)
    assert m.state is SessionState.ACTIVE

    m.trigger(Trigger.SILENCE_TIMEOUT)
    assert m.state is SessionState.ENDING

    m.trigger(Trigger.TEARDOWN_DONE)
    assert m.state is SessionState.IDLE


def test_cli_skips_confirm() -> None:
    m = SessionMachine()
    m.trigger(Trigger.CLI_START)
    assert m.state is SessionState.WAKING
    assert m.context.skip_confirm is True
    m.trigger(Trigger.CAPTURE_READY)
    assert m.state is SessionState.CONNECTING


def test_approval_pending_mutes_uplink() -> None:
    m = SessionMachine()
    m.trigger(Trigger.CLI_START)
    m.trigger(Trigger.CAPTURE_READY)
    m.trigger(Trigger.SESSION_READY)
    assert m.state is SessionState.ACTIVE

    m.trigger(Trigger.TOOL_NEEDS_APPROVAL, tool="run_command")
    assert m.state is SessionState.APPROVAL_PENDING
    assert m.context.approval_in_flight is True
    assert m.context.mute_uplink is True

    m.trigger(Trigger.APPROVAL_ALLOW)
    assert m.state is SessionState.ACTIVE
    assert m.context.approval_in_flight is False
    assert m.context.mute_uplink is False


def test_illegal_transition() -> None:
    m = SessionMachine()
    with pytest.raises(InvalidTransition):
        m.trigger(Trigger.SESSION_READY)


def test_connect_fail_returns_idle() -> None:
    m = SessionMachine()
    m.trigger(Trigger.HOTKEY)
    m.trigger(Trigger.CAPTURE_READY)
    m.trigger(Trigger.CONNECT_FAIL)
    assert m.state is SessionState.IDLE


def test_max_cost_ends_session() -> None:
    m = SessionMachine()
    m.trigger(Trigger.CLI_START)
    m.trigger(Trigger.CAPTURE_READY)
    m.trigger(Trigger.SESSION_READY)
    m.trigger(Trigger.MAX_COST)
    assert m.state is SessionState.ENDING


def test_listener_called() -> None:
    m = SessionMachine()
    seen: list[tuple[str, str]] = []

    def on_change(old, new, event) -> None:  # noqa: ANN001
        seen.append((old.value, new.value))

    m.add_listener(on_change)
    m.trigger(Trigger.WAKE_WORD)
    assert seen == [("idle", "waking")]
