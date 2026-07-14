"""CloudAudioGateway and mock session tests."""

from __future__ import annotations

import pytest

from aegis.config.schema import SessionConfig
from aegis.voice.gateway import CloudAudioGateway, GatewayError
from aegis.voice.mock import MockVoiceSession
from aegis.voice.protocol import ToolCallRequest, VoiceEventType


def test_gateway_refuses_random_host() -> None:
    gw = CloudAudioGateway()
    with pytest.raises(GatewayError, match="non-OpenAI"):
        gw.authorize_connect("wss://evil.example/v1/realtime")


def test_gateway_allows_openai() -> None:
    gw = CloudAudioGateway()
    gw.authorize_connect("wss://api.openai.com/v1/realtime")


def test_idle_assert() -> None:
    gw = CloudAudioGateway()
    gw.assert_idle_has_no_cloud()
    gw.register_open("wss://api.openai.com/v1/realtime")
    with pytest.raises(GatewayError, match="still open"):
        gw.assert_idle_has_no_cloud()
    gw.register_close()
    gw.assert_idle_has_no_cloud()


@pytest.mark.asyncio
async def test_mock_session_flow() -> None:
    gw = CloudAudioGateway()
    session = MockVoiceSession(gateway=gw, register_gateway=True, auto_end=False)
    await session.connect(SessionConfig())
    assert gw.is_open

    events = []

    async def collect() -> None:
        async for ev in session.events():
            events.append(ev)

    collect_task = __import__("asyncio").create_task(collect())
    await session.send_audio(b"\x00\x00" * 100)
    await session.end()
    await collect_task

    types = [e.type for e in events]
    assert VoiceEventType.READY in types
    assert VoiceEventType.AGENT_TRANSCRIPT in types
    assert VoiceEventType.ENDED in types
    assert not gw.is_open


@pytest.mark.asyncio
async def test_mock_tool_call() -> None:
    session = MockVoiceSession(
        emit_tool_call=ToolCallRequest(
            call_id="c1",
            name="read_file",
            arguments={"path": "/tmp/x"},
        ),
        auto_end=False,
    )
    await session.connect(SessionConfig())
    tool_events = []
    async for ev in session.events():
        if ev.type is VoiceEventType.TOOL_CALL:
            tool_events.append(ev)
            await session.send_tool_result("c1", "ok content")
            await session.end()
        if ev.type is VoiceEventType.ENDED:
            break
    assert len(tool_events) == 1
    assert tool_events[0].tool_call is not None
    assert tool_events[0].tool_call.name == "read_file"
