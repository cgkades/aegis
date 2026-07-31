"""Scenario tests for session guarantees the suite previously only asserted at flag level."""

from __future__ import annotations

import asyncio

import numpy as np
import pytest

from aegis.config import build_config
from aegis.session.events import SessionState, Trigger
from aegis.session.machine import SessionMachine
from aegis.session.runner import _uplink_loop
from aegis.util.metrics import SessionMetrics
from aegis.voice.protocol import UsageSnapshot


class _FakeCapture:
    def __init__(self) -> None:
        self.frame = np.zeros(160, dtype=np.int16)

    def read(self, timeout: float = 0.2):
        return self.frame


class _FakeGraph:
    def __init__(self) -> None:
        self.capture = _FakeCapture()

    def uplink_frame(self, frame):
        return frame


class _RecordingSession:
    def __init__(self) -> None:
        self.sent = 0

    async def send_audio(self, data: bytes) -> None:
        self.sent += 1


def _active_machine() -> SessionMachine:
    machine = SessionMachine()
    machine.trigger(Trigger.CLI_START)
    machine.trigger(Trigger.CAPTURE_READY)
    machine.trigger(Trigger.SESSION_READY)
    assert machine.state is SessionState.ACTIVE
    return machine


@pytest.mark.asyncio
async def test_uplink_stops_sending_mic_audio_while_approval_pending() -> None:
    """Privacy guarantee: no ambient audio uploaded while the user deliberates.

    Previously only the mute_uplink flag was tested; nothing covered the
    two-line enforcement in _uplink_loop itself.
    """
    machine = _active_machine()
    graph = _FakeGraph()
    session = _RecordingSession()
    stop = asyncio.Event()

    task = asyncio.create_task(_uplink_loop(session, graph, machine, stop))
    await asyncio.sleep(0.05)
    assert session.sent > 0, "expected mic frames to flow while ACTIVE"

    machine.trigger(
        Trigger.TOOL_NEEDS_APPROVAL, tool="write_file", call_id="c1", mute_uplink=True
    )
    assert machine.state is SessionState.APPROVAL_PENDING
    assert machine.context.mute_uplink is True

    await asyncio.sleep(0.02)
    baseline = session.sent
    await asyncio.sleep(0.1)
    assert session.sent == baseline, "mic audio was uploaded during approval"

    # Resolving the approval must let audio flow again.
    machine.trigger(Trigger.APPROVAL_ALLOW, tool="write_file")
    await asyncio.sleep(0.05)
    assert session.sent > baseline

    stop.set()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_uplink_loop_exits_when_session_ends() -> None:
    machine = _active_machine()
    stop = asyncio.Event()
    task = asyncio.create_task(_uplink_loop(_RecordingSession(), _FakeGraph(), machine, stop))
    await asyncio.sleep(0.02)
    machine.trigger(Trigger.HOTKEY_END)
    await asyncio.wait_for(task, timeout=2)


def test_cost_cap_trips_on_realistic_cached_usage() -> None:
    """The cap must fire on usage that the old discount would have zeroed out."""
    cfg = build_config({"session": {"max_session_cost_usd": 0.5}})
    metrics = SessionMetrics(model="gpt-realtime-2.1")
    assert metrics.exceeds_cost_cap(cfg.session.max_session_cost_usd) is False

    for _ in range(20):
        metrics.add_usage(
            UsageSnapshot(
                input_audio_tokens=2_000,
                input_text_tokens=50_000,
                output_audio_tokens=2_000,
                cached_input_tokens=45_000,
                cached_input_text_tokens=45_000,
            )
        )

    assert metrics.estimated_cost_usd > 0.5
    assert metrics.exceeds_cost_cap(cfg.session.max_session_cost_usd) is True


def test_cost_cap_not_tripped_by_trivial_usage() -> None:
    cfg = build_config({"session": {"max_session_cost_usd": 2.0}})
    metrics = SessionMetrics(model="gpt-realtime-2.1-mini")
    metrics.add_usage(UsageSnapshot(input_text_tokens=100, output_text_tokens=50))
    assert metrics.exceeds_cost_cap(cfg.session.max_session_cost_usd) is False
