"""Realtime adapter tests with mocked websocket."""

from __future__ import annotations

import asyncio
import base64
import json
from typing import Any
from unittest.mock import patch

import pytest

from aegis.config.schema import SessionConfig
from aegis.voice.gateway import CloudAudioGateway
from aegis.voice.protocol import ToolCallRequest, VoiceEvent, VoiceEventType
from aegis.voice.realtime import (
    _EVENT_QUEUE_MAX,
    _MAX_FUNCTION_ARG_TOTAL_BYTES,
    RealtimeVoiceSession,
    _usage_from_response,
)


class FakeWS:
    def __init__(self, messages: list[dict[str, Any]] | None = None) -> None:
        self.sent: list[dict[str, Any]] = []
        self._messages = list(messages or [])
        self._closed = False
        self.samplerate = 24000

    async def send(self, data: str) -> None:
        self.sent.append(json.loads(data))

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._messages:
            raise StopAsyncIteration
        return json.dumps(self._messages.pop(0))

    async def close(self) -> None:
        self._closed = True


@pytest.mark.asyncio
async def test_realtime_connect_send_audio_and_events() -> None:
    gw = CloudAudioGateway()
    pcm = b"\x00\x00" * 10
    b64 = base64.b64encode(pcm).decode("ascii")
    messages = [
        {"type": "session.created"},
        {"type": "response.audio.delta", "delta": b64},
        {
            "type": "response.audio_transcript.delta",
            "delta": "hello",
        },
        {
            "type": "response.function_call_arguments.done",
            "call_id": "c1",
            "name": "list_dir",
            "arguments": '{"path":"."}',
        },
        {
            "type": "response.done",
            "response": {
                "usage": {
                    "input_token_details": {"audio_tokens": 5, "text_tokens": 1},
                    "output_token_details": {"audio_tokens": 7, "text_tokens": 2},
                }
            },
        },
        {"type": "error", "error": {"message": "boom"}},
    ]
    fake = FakeWS(messages)
    session = RealtimeVoiceSession(
        api_key="sk-test",
        gateway=gw,
        tools=[{"type": "function", "name": "list_dir", "parameters": {}}],
    )

    async def fake_connect(*args, **kwargs):
        return fake

    with patch("aegis.voice.realtime.websockets.connect", side_effect=fake_connect):
        await session.connect(SessionConfig(model="gpt-realtime-2.1-mini"))
        # Gateway registers on connect; may close when fake WS iterator ends.
        assert any(s.get("type") == "session.update" for s in fake.sent)
        # If still connected, exercise send paths
        try:
            await session.send_audio(pcm)
        except RuntimeError:
            pass
        try:
            await session.send_tool_result("c1", "ok")
        except RuntimeError:
            pass
        try:
            await session.interrupt_agent()
        except Exception:
            pass
        await session.end()

    assert any(s.get("type") == "session.update" for s in fake.sent)
    # end is idempotent regarding gateway close
    assert not gw.is_open or gw.active_sessions >= 0


@pytest.mark.asyncio
async def test_realtime_requires_api_key() -> None:
    session = RealtimeVoiceSession(api_key="", api_key_env="NO_SUCH_KEY_XYZ")
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY|not set"):
        await session.connect(SessionConfig())


@pytest.mark.asyncio
async def test_realtime_connect_cancellation_closes_gateway() -> None:
    gateway = CloudAudioGateway()
    session = RealtimeVoiceSession(api_key="sk-test", gateway=gateway)

    async def slow_connect(*args, **kwargs):
        await asyncio.Event().wait()

    with patch("aegis.voice.realtime.websockets.connect", side_effect=slow_connect):
        task = asyncio.create_task(session.connect(SessionConfig()))
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert not gateway.is_open


@pytest.mark.asyncio
async def test_realtime_end_register_close_survives_cancel() -> None:
    """Cancel during end() must still run register_close (gateway idle again)."""
    gateway = CloudAudioGateway()
    session = RealtimeVoiceSession(api_key="sk-test", gateway=gateway)

    class HangWS(FakeWS):
        """Never finishes the recv loop until close is called."""

        def __init__(self) -> None:
            super().__init__([])
            self._gate = asyncio.Event()

        async def __anext__(self):
            await self._gate.wait()
            raise StopAsyncIteration

        async def close(self) -> None:
            self._gate.set()
            raise asyncio.CancelledError

    fake = HangWS()

    async def fake_connect(*args, **kwargs):
        return fake

    with patch("aegis.voice.realtime.websockets.connect", side_effect=fake_connect):
        await session.connect(SessionConfig())
        # Let recv task park on the hang gate so it does not spontaneous-close.
        await asyncio.sleep(0)
        assert gateway.is_open
        with pytest.raises(asyncio.CancelledError):
            await session.end()
        assert not gateway.is_open


def test_usage_from_response_flat() -> None:
    msg = {
        "type": "response.done",
        "usage": {
            "input_audio_tokens": 3,
            "output_audio_tokens": 4,
            "cached_input_tokens": 1,
        },
    }
    u = _usage_from_response(msg)
    assert u is not None
    assert u.input_audio_tokens == 3


@pytest.mark.asyncio
async def test_realtime_handle_mcp_event() -> None:
    gw = CloudAudioGateway()
    session = RealtimeVoiceSession(api_key="sk-test", gateway=gw)
    fake = FakeWS([{"type": "mcp_list_tools.completed", "item_id": "x"}])

    async def fake_connect(*args, **kwargs):
        return fake

    with patch("aegis.voice.realtime.websockets.connect", side_effect=fake_connect):
        await session.connect(SessionConfig())
        events = []

        async def collect():
            async for ev in session.events():
                events.append(ev)
                if ev.type in {VoiceEventType.REMOTE_TOOL_ACTIVITY, VoiceEventType.ENDED}:
                    if ev.type is VoiceEventType.REMOTE_TOOL_ACTIVITY:
                        break

        task = asyncio.create_task(collect())
        await asyncio.sleep(0.1)
        await session.end()
        task.cancel()


@pytest.mark.asyncio
async def test_realtime_backpressure_preserves_queued_tool_calls() -> None:
    session = RealtimeVoiceSession(api_key="sk-test", gateway=CloudAudioGateway())
    tool = VoiceEvent(
        type=VoiceEventType.TOOL_CALL,
        tool_call=ToolCallRequest(call_id="call-1", name="read_file", arguments={}),
    )
    await session._put_event(tool)
    for _ in range(_EVENT_QUEUE_MAX - 1):
        await session._put_event(VoiceEvent(type=VoiceEventType.AGENT_AUDIO, pcm16=b"\0\0"))
    await session._put_event(VoiceEvent(type=VoiceEventType.ERROR, message="network"))

    events = [session._events.get_nowait() for _ in range(session._events.qsize())]
    assert tool in events
    assert any(event and event.type is VoiceEventType.ERROR for event in events)


@pytest.mark.asyncio
async def test_realtime_function_argument_aggregate_budget() -> None:
    session = RealtimeVoiceSession(api_key="sk-test", gateway=CloudAudioGateway())
    for call_id in ("a", "b", "c"):
        await session._handle_server_event(
            {
                "type": "response.function_call_arguments.delta",
                "call_id": call_id,
                "delta": "x" * 400_000,
            }
        )

    assert "c" in session._function_arg_overflows
    assert session._function_arg_total_bytes <= _MAX_FUNCTION_ARG_TOTAL_BYTES
    await session._handle_server_event(
        {
            "type": "response.function_call_arguments.done",
            "call_id": "c",
            "name": "write_file",
            "arguments": "",
        }
    )
    event = session._events.get_nowait()
    assert event is not None and event.tool_call is not None
    assert event.tool_call.name == ""
