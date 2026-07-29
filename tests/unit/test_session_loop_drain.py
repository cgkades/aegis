"""The event pump must keep draining while a tool call or approval is in flight."""

from __future__ import annotations

import asyncio
import time

import pytest

from aegis.config import build_config
from aegis.session import runner as runner_mod
from aegis.session.context import ContextManager
from aegis.session.events import SessionState, Trigger
from aegis.session.machine import SessionMachine
from aegis.session.runner import _SessionLoop
from aegis.tools.types import ToolResult
from aegis.ui.status import StatusPresenter
from aegis.util.metrics import SessionMetrics
from aegis.voice.protocol import ToolCallRequest, VoiceEvent, VoiceEventType


class _ScriptedSession:
    """Voice session whose event stream the test drives by hand."""

    def __init__(self) -> None:
        self.queue: asyncio.Queue[VoiceEvent | None] = asyncio.Queue()
        self.tool_results: list[tuple[str, str]] = []
        self.ended = False

    async def events(self):
        while True:
            item = await self.queue.get()
            if item is None:
                break
            yield item

    async def send_tool_result(self, call_id: str, output: str, *, is_error: bool = False) -> None:
        self.tool_results.append((call_id, output))

    async def send_audio(self, pcm: bytes) -> None:
        return None

    async def interrupt_agent(self) -> None:
        return None

    async def end(self) -> None:
        self.ended = True


class _RecordingGraph:
    def __init__(self) -> None:
        self.played: list[float] = []

    def play_session_audio(self, pcm) -> None:
        self.played.append(time.monotonic())


class _StubRegistry:
    def __init__(self) -> None:
        self.turn_resets = 0

    def reset_turn(self) -> None:
        self.turn_resets += 1


def _active_machine() -> SessionMachine:
    machine = SessionMachine()
    machine.trigger(Trigger.CLI_START, skip_confirm=True)
    machine.trigger(Trigger.CAPTURE_READY)
    machine.trigger(Trigger.SESSION_READY)
    assert machine.state is SessionState.ACTIVE
    return machine


def _make_loop(session, graph, machine, cfg, *, deadline_s: float = 30.0) -> _SessionLoop:
    return _SessionLoop(
        session=session,
        graph=graph,
        machine=machine,
        registry=_StubRegistry(),
        cfg=cfg,
        context=ContextManager(cfg.session.context),
        metrics=SessionMetrics(model=cfg.session.model),
        status=StatusPresenter(chime_on_wake=False, chime_on_connecting=False),
        audit=None,
        stop=asyncio.Event(),
        deadline=time.monotonic() + deadline_s,
        uplink_task=None,
        interactive_approval=False,
        approval_handler=None,
        auto_end_mock=False,
    )


def _audio_event() -> VoiceEvent:
    return VoiceEvent(type=VoiceEventType.AGENT_AUDIO, pcm16=b"\x00\x00" * 240)


@pytest.mark.asyncio
async def test_agent_audio_plays_while_a_slow_tool_runs(monkeypatch) -> None:
    """The regression this refactor exists for.

    With tool dispatch inline in the event loop, audio produced during a tool
    call sat in the adapter queue until the tool returned — and once that queue
    filled, the adapter evicted it outright.
    """
    tool_started = asyncio.Event()
    release_tool = asyncio.Event()
    tool_finished_at: list[float] = []

    async def slow_tool(call, **kwargs) -> ToolResult:
        tool_started.set()
        await release_tool.wait()
        tool_finished_at.append(time.monotonic())
        return ToolResult(output="done")

    monkeypatch.setattr(runner_mod, "handle_tool_call", slow_tool)

    cfg = build_config({"session": {"idle_timeout_s": 300, "max_duration_s": 300}})
    session = _ScriptedSession()
    graph = _RecordingGraph()
    machine = _active_machine()
    loop = _make_loop(session, graph, machine, cfg)

    run_task = asyncio.create_task(loop.run())
    session.queue.put_nowait(
        VoiceEvent(
            type=VoiceEventType.TOOL_CALL,
            tool_call=ToolCallRequest(call_id="c1", name="slow", arguments={}),
        )
    )
    await asyncio.wait_for(tool_started.wait(), timeout=2)

    # The model keeps talking while the tool runs.
    for _ in range(5):
        session.queue.put_nowait(_audio_event())
    await asyncio.sleep(0.05)

    assert len(graph.played) == 5, "agent audio was not drained while the tool ran"
    assert not tool_finished_at, "tool should still be in flight"

    release_tool.set()
    await asyncio.sleep(0.05)
    assert tool_finished_at, "tool never completed"
    # Every frame reached playback strictly before the tool returned.
    assert all(t < tool_finished_at[0] for t in graph.played)

    session.queue.put_nowait(VoiceEvent(type=VoiceEventType.ENDED))
    await asyncio.wait_for(run_task, timeout=2)


@pytest.mark.asyncio
async def test_idle_timeout_pauses_while_a_tool_is_in_flight(monkeypatch) -> None:
    """openspec: the idle timeout MUST pause while a tool runs."""
    tool_started = asyncio.Event()
    release_tool = asyncio.Event()

    async def slow_tool(call, **kwargs) -> ToolResult:
        tool_started.set()
        await release_tool.wait()
        return ToolResult(output="done")

    monkeypatch.setattr(runner_mod, "handle_tool_call", slow_tool)

    cfg = build_config({"session": {"idle_timeout_s": 5, "max_duration_s": 300}})
    session = _ScriptedSession()
    machine = _active_machine()
    loop = _make_loop(session, _RecordingGraph(), machine, cfg)

    run_task = asyncio.create_task(loop.run())
    session.queue.put_nowait(
        VoiceEvent(
            type=VoiceEventType.TOOL_CALL,
            tool_call=ToolCallRequest(call_id="c1", name="slow", arguments={}),
        )
    )
    await asyncio.wait_for(tool_started.wait(), timeout=2)

    # Push the idle clock well past the timeout while the tool is still running.
    loop._last_activity = time.monotonic() - 60
    await asyncio.sleep(0.6)
    assert not run_task.done(), "idle timeout fired while a tool was in flight"

    release_tool.set()
    session.queue.put_nowait(VoiceEvent(type=VoiceEventType.ENDED))
    await asyncio.wait_for(run_task, timeout=2)


@pytest.mark.asyncio
async def test_idle_timeout_still_fires_when_nothing_is_pending() -> None:
    cfg = build_config({"session": {"idle_timeout_s": 5, "max_duration_s": 300}})
    session = _ScriptedSession()
    machine = _active_machine()
    loop = _make_loop(session, _RecordingGraph(), machine, cfg)

    loop._last_activity = time.monotonic() - 60
    await asyncio.wait_for(loop.run(), timeout=3)
    assert machine.state is not SessionState.ACTIVE


@pytest.mark.asyncio
async def test_tool_calls_stay_serial_and_ordered(monkeypatch) -> None:
    """Realtime wants function_call_output in order; only the drain is parallel."""
    concurrent = 0
    peak = 0
    order: list[str] = []

    async def tracking_tool(call, **kwargs) -> ToolResult:
        nonlocal concurrent, peak
        concurrent += 1
        peak = max(peak, concurrent)
        await asyncio.sleep(0.01)
        order.append(call.call_id)
        concurrent -= 1
        return ToolResult(output=call.call_id)

    monkeypatch.setattr(runner_mod, "handle_tool_call", tracking_tool)

    cfg = build_config({"session": {"idle_timeout_s": 300, "max_duration_s": 300}})
    session = _ScriptedSession()
    machine = _active_machine()
    loop = _make_loop(session, _RecordingGraph(), machine, cfg)

    run_task = asyncio.create_task(loop.run())
    for i in range(4):
        session.queue.put_nowait(
            VoiceEvent(
                type=VoiceEventType.TOOL_CALL,
                tool_call=ToolCallRequest(call_id=f"c{i}", name="t", arguments={}),
            )
        )
    await asyncio.sleep(0.2)
    session.queue.put_nowait(VoiceEvent(type=VoiceEventType.ENDED))
    await asyncio.wait_for(run_task, timeout=2)

    assert peak == 1, "tool calls must not overlap"
    assert order == ["c0", "c1", "c2", "c3"]


@pytest.mark.asyncio
async def test_cost_cap_can_trip_while_a_tool_is_running(monkeypatch) -> None:
    """The caps used to go blind for the whole duration of a tool call."""
    release_tool = asyncio.Event()
    tool_started = asyncio.Event()

    async def slow_tool(call, **kwargs) -> ToolResult:
        tool_started.set()
        await release_tool.wait()
        return ToolResult(output="done")

    monkeypatch.setattr(runner_mod, "handle_tool_call", slow_tool)

    cfg = build_config(
        {"session": {"max_session_cost_usd": 0.001, "idle_timeout_s": 300}}
    )
    session = _ScriptedSession()
    machine = _active_machine()
    loop = _make_loop(session, _RecordingGraph(), machine, cfg)

    run_task = asyncio.create_task(loop.run())
    session.queue.put_nowait(
        VoiceEvent(
            type=VoiceEventType.TOOL_CALL,
            tool_call=ToolCallRequest(call_id="c1", name="slow", arguments={}),
        )
    )
    await asyncio.wait_for(tool_started.wait(), timeout=2)

    # Usage arrives mid-tool and blows the cap.
    from aegis.voice.protocol import UsageSnapshot

    session.queue.put_nowait(
        VoiceEvent(
            type=VoiceEventType.USAGE,
            usage=UsageSnapshot(input_audio_tokens=500_000, output_audio_tokens=500_000),
        )
    )

    await asyncio.wait_for(run_task, timeout=3)
    assert machine.state is not SessionState.ACTIVE
    release_tool.set()


@pytest.mark.asyncio
async def test_pump_failure_propagates_out_of_run(monkeypatch) -> None:
    """A tool-loop error still ends the session rather than being swallowed."""

    async def exploding_tool(call, **kwargs) -> ToolResult:
        raise RuntimeError("realtime session not connected")

    monkeypatch.setattr(runner_mod, "handle_tool_call", exploding_tool)

    cfg = build_config({"session": {"idle_timeout_s": 300, "max_duration_s": 300}})
    session = _ScriptedSession()
    machine = _active_machine()
    loop = _make_loop(session, _RecordingGraph(), machine, cfg)

    run_task = asyncio.create_task(loop.run())
    session.queue.put_nowait(
        VoiceEvent(
            type=VoiceEventType.TOOL_CALL,
            tool_call=ToolCallRequest(call_id="c1", name="boom", arguments={}),
        )
    )
    with pytest.raises(RuntimeError, match="not connected"):
        await asyncio.wait_for(run_task, timeout=2)


# --------------------------------------------------------------------------- #
# R1: every exit path unwinds through one teardown
# --------------------------------------------------------------------------- #


def _audit_events(directory) -> list[dict]:
    import json

    events: list[dict] = []
    for path in sorted(directory.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                events.append(json.loads(line))
    return events


class _FailingConnectSession:
    def __init__(self, exc: BaseException) -> None:
        self._exc = exc
        self.ended = False

    async def connect(self, config) -> None:
        raise self._exc

    async def events(self):
        if False:
            yield None

    async def send_audio(self, pcm: bytes) -> None:
        return None

    async def send_tool_result(self, call_id, output, *, is_error=False) -> None:
        return None

    async def interrupt_agent(self) -> None:
        return None

    async def end(self) -> None:
        self.ended = True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "exc",
    [RuntimeError("connect refused"), TimeoutError()],
    ids=["connect_failed", "connect_timeout"],
)
async def test_connect_failure_runs_the_same_teardown(tmp_path, exc) -> None:
    """Connect-phase failures used to skip the audit record and the cloud assert."""
    from unittest.mock import patch

    from aegis.config.paths import AegisPaths

    paths = AegisPaths(
        config_dir=tmp_path / "cfg",
        state_dir=tmp_path / "state",
        data_dir=tmp_path / "data",
        cache_dir=tmp_path / "cache",
    )
    paths.ensure_dirs()
    cfg = build_config({"activation": {"chime_on_wake": False, "chime_on_connecting": False}})
    session = _FailingConnectSession(exc)

    with (
        patch.object(runner_mod, "sounddevice_available", return_value=False),
        patch.object(runner_mod, "create_voice_session", return_value=session),
    ):
        code = await runner_mod.run_session_once(
            cfg, backend="custom", paths=paths, max_seconds=5
        )

    assert code == 1
    assert session.ended, "session.end must run so gateway accounting balances"
    ends = [e for e in _audit_events(paths.audit_dir) if e["event_type"] == "session_end"]
    assert len(ends) == 1, "a failed connect must still record session_end"


@pytest.mark.asyncio
async def test_cancellation_during_connect_still_tears_down(tmp_path) -> None:
    from unittest.mock import patch

    from aegis.config.paths import AegisPaths

    paths = AegisPaths(
        config_dir=tmp_path / "cfg",
        state_dir=tmp_path / "state",
        data_dir=tmp_path / "data",
        cache_dir=tmp_path / "cache",
    )
    paths.ensure_dirs()
    cfg = build_config({"activation": {"chime_on_wake": False, "chime_on_connecting": False}})
    session = _FailingConnectSession(asyncio.CancelledError())

    with (
        patch.object(runner_mod, "sounddevice_available", return_value=False),
        patch.object(runner_mod, "create_voice_session", return_value=session),
    ):
        with pytest.raises(asyncio.CancelledError):
            await runner_mod.run_session_once(
                cfg, backend="custom", paths=paths, max_seconds=5
            )

    assert session.ended, "cancellation must not skip gateway accounting"
