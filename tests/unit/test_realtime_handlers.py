"""Direct coverage of RealtimeVoiceSession event handlers."""

from __future__ import annotations

import base64

import pytest

from aegis.voice.gateway import CloudAudioGateway
from aegis.voice.protocol import VoiceEventType
from aegis.voice.realtime import RealtimeVoiceSession


@pytest.mark.asyncio
async def test_handle_server_events_matrix() -> None:
    session = RealtimeVoiceSession(api_key="sk", gateway=CloudAudioGateway())
    session._connected = True

    # Feed events and drain via queue
    pcm = base64.b64encode(b"\x00\x00" * 8).decode()
    events_in = [
        {"type": "session.updated"},
        {"type": "response.output_audio.delta", "delta": pcm},
        {"type": "response.output_audio_transcript.delta", "delta": "hi "},
        {
            "type": "conversation.item.input_audio_transcription.completed",
            "transcript": "user said",
        },
        {"type": "response.function_call_arguments.delta", "call_id": "c1", "delta": '{"a"'},
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
                    "input_token_details": {
                        "audio_tokens": 1,
                        "text_tokens": 1,
                        "cached_tokens": 0,
                    },
                    "output_token_details": {"audio_tokens": 2, "text_tokens": 1},
                }
            },
        },
        {"type": "mcp_list_tools.completed"},
        {"type": "error", "error": {"message": "x"}},
        {"type": "unknown_event"},
    ]
    for msg in events_in:
        await session._handle_server_event(msg)

    collected = []
    # Non-blocking drain of queue
    while not session._events.empty():
        collected.append(await session._events.get())

    types = {e.type for e in collected}
    assert VoiceEventType.READY in types
    assert VoiceEventType.AGENT_AUDIO in types
    assert VoiceEventType.AGENT_TRANSCRIPT in types
    assert VoiceEventType.USER_TRANSCRIPT in types
    assert VoiceEventType.TOOL_CALL in types
    assert VoiceEventType.USAGE in types
    assert VoiceEventType.ERROR in types
    assert VoiceEventType.REMOTE_TOOL_ACTIVITY in types


@pytest.mark.asyncio
async def test_send_paths_when_connected() -> None:
    session = RealtimeVoiceSession(api_key="sk", gateway=CloudAudioGateway())
    sent = []

    class WS:
        async def send(self, data: str) -> None:
            sent.append(data)

    session._ws = WS()
    session._connected = True
    await session.send_audio(b"\x00\x00")
    await session.send_audio(b"")  # no-op
    await session.send_tool_result("c1", "out")
    await session.send_tool_result("c2", "bad", is_error=True)
    await session.interrupt_agent()
    assert any("input_audio_buffer.append" in s for s in sent)
    assert any("function_call_output" in s for s in sent)
    assert any("response.cancel" in s for s in sent)


@pytest.mark.asyncio
async def test_send_when_not_connected() -> None:
    session = RealtimeVoiceSession(api_key="sk")
    with pytest.raises(RuntimeError):
        await session.send_audio(b"\x00\x00")
    with pytest.raises(RuntimeError):
        await session.send_tool_result("c", "x")
