"""_EventQueue: bounded FIFO with audio-only eviction (replaces asyncio.Queue internals)."""

from __future__ import annotations

import asyncio

import pytest

from aegis.voice.protocol import ToolCallRequest, VoiceEvent, VoiceEventType
from aegis.voice.realtime import _EventQueue


def _audio() -> VoiceEvent:
    return VoiceEvent(type=VoiceEventType.AGENT_AUDIO, pcm16=b"\x00\x00")


def _tool(call_id: str = "c1") -> VoiceEvent:
    return VoiceEvent(
        type=VoiceEventType.TOOL_CALL,
        tool_call=ToolCallRequest(call_id=call_id, name="t", arguments={}),
    )


@pytest.mark.asyncio
async def test_fifo_order_is_preserved_across_event_types() -> None:
    """Audio and control events must stay interleaved as produced."""
    q = _EventQueue(10)
    order = [_audio(), _tool("a"), _audio(), VoiceEvent(type=VoiceEventType.ERROR)]
    for event in order:
        q.put_nowait(event)
    got = [await q.get() for _ in range(len(order))]
    assert [e.type for e in got] == [e.type for e in order]


def test_put_nowait_raises_when_full() -> None:
    q = _EventQueue(2)
    q.put_nowait(_tool("a"))
    q.put_nowait(_tool("b"))
    assert q.full()
    with pytest.raises(asyncio.QueueFull):
        q.put_nowait(_tool("c"))


def test_get_nowait_raises_when_empty() -> None:
    q = _EventQueue(2)
    assert q.empty()
    with pytest.raises(asyncio.QueueEmpty):
        q.get_nowait()


def test_drop_oldest_audio_evicts_the_first_audio_only() -> None:
    q = _EventQueue(10)
    q.put_nowait(_tool("a"))
    first = _audio()
    second = _audio()
    q.put_nowait(first)
    q.put_nowait(_tool("b"))
    q.put_nowait(second)

    assert q.drop_oldest_audio() is True
    remaining = [q.get_nowait() for _ in range(q.qsize())]
    assert [e.type for e in remaining] == [
        VoiceEventType.TOOL_CALL,
        VoiceEventType.TOOL_CALL,
        VoiceEventType.AGENT_AUDIO,
    ]
    # The surviving audio event is the newer one.
    assert remaining[-1] is second


def test_drop_oldest_audio_reports_failure_when_only_control_events_queued() -> None:
    q = _EventQueue(3)
    for i in range(3):
        q.put_nowait(_tool(f"c{i}"))
    assert q.drop_oldest_audio() is False


def test_sentinel_none_is_a_valid_item() -> None:
    q = _EventQueue(2)
    q.put_nowait(None)
    assert q.get_nowait() is None


@pytest.mark.asyncio
async def test_get_waits_for_a_producer() -> None:
    q = _EventQueue(4)
    getter = asyncio.create_task(q.get())
    await asyncio.sleep(0.01)
    assert not getter.done()
    q.put_nowait(_tool("late"))
    event = await asyncio.wait_for(getter, timeout=1)
    assert event.type is VoiceEventType.TOOL_CALL


@pytest.mark.asyncio
async def test_put_backpressures_until_space_frees() -> None:
    """Control events block the producer rather than being dropped."""
    q = _EventQueue(1)
    q.put_nowait(_tool("first"))

    putter = asyncio.create_task(q.put(_tool("second")))
    await asyncio.sleep(0.01)
    assert not putter.done(), "put must block while the queue is full"

    assert q.get_nowait().tool_call.call_id == "first"
    await asyncio.wait_for(putter, timeout=1)
    assert q.get_nowait().tool_call.call_id == "second"


@pytest.mark.asyncio
async def test_repeated_fill_and_drain_keeps_readiness_flags_consistent() -> None:
    """Regression guard: the internal ready/space events must not desync."""
    q = _EventQueue(2)
    for _ in range(5):
        q.put_nowait(_audio())
        q.put_nowait(_audio())
        assert q.full()
        assert await q.get() is not None
        assert await q.get() is not None
        assert q.empty()
    getter = asyncio.create_task(q.get())
    await asyncio.sleep(0.01)
    assert not getter.done(), "queue reported ready while empty"
    q.put_nowait(_audio())
    await asyncio.wait_for(getter, timeout=1)
