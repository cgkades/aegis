"""In-memory mock VoiceSession for tests and offline dogfood."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from aegis.config.schema import SessionConfig
from aegis.voice.gateway import CloudAudioGateway
from aegis.voice.protocol import (
    ToolCallRequest,
    UsageSnapshot,
    VoiceEvent,
    VoiceEventType,
)


class MockVoiceSession:
    """Deterministic session that never touches the network.

    Optionally registers with a gateway using a localhost URL so tests can
    exercise open/close accounting without OpenAI.
    """

    def __init__(
        self,
        *,
        gateway: CloudAudioGateway | None = None,
        register_gateway: bool = False,
        reply_text: str = "Hello from mock Aegis.",
        emit_tool_call: ToolCallRequest | None = None,
        auto_end: bool = True,
    ) -> None:
        self._gateway = gateway
        self._register_gateway = register_gateway
        self._reply_text = reply_text
        self._emit_tool_call = emit_tool_call
        self._auto_end = auto_end
        self._config: SessionConfig | None = None
        self._queue: asyncio.Queue[VoiceEvent | None] = asyncio.Queue()
        self._connected = False
        self._audio_chunks = 0
        self._usage = UsageSnapshot()
        self._gateway_registered = False

    async def connect(self, config: SessionConfig) -> None:
        self._config = config
        if self._register_gateway and self._gateway is not None:
            # Local mock URL allowed by gateway for tests
            self._gateway.register_open("ws://127.0.0.1:9/mock-realtime")
            self._gateway_registered = True
        self._connected = True
        await self._queue.put(VoiceEvent(type=VoiceEventType.READY))
        if self._emit_tool_call is not None:
            await self._queue.put(
                VoiceEvent(
                    type=VoiceEventType.TOOL_CALL,
                    tool_call=self._emit_tool_call,
                )
            )
        # Simulate a short agent transcript + silence audio
        await self._queue.put(
            VoiceEvent(type=VoiceEventType.AGENT_TRANSCRIPT, text=self._reply_text)
        )
        await self._queue.put(
            VoiceEvent(
                type=VoiceEventType.AGENT_AUDIO,
                pcm16=b"\x00\x00" * 240,  # 10ms silence @ 24k mono int16
            )
        )
        await self._queue.put(
            VoiceEvent(
                type=VoiceEventType.USAGE,
                usage=UsageSnapshot(
                    input_audio_tokens=10,
                    output_audio_tokens=20,
                    input_text_tokens=5,
                    output_text_tokens=8,
                ),
            )
        )
        # Finite dogfood session — signal natural completion unless tools need handling.
        if self._auto_end and self._emit_tool_call is None:
            await self._finish()

    async def send_audio(self, pcm16_24k_mono: bytes) -> None:
        if not self._connected:
            raise RuntimeError("mock session not connected")
        self._audio_chunks += 1
        # Rough token accounting for tests
        self._usage.input_audio_tokens += max(1, len(pcm16_24k_mono) // 100)

    async def send_tool_result(
        self,
        call_id: str,
        output: str,
        *,
        is_error: bool = False,
    ) -> None:
        if not self._connected:
            raise RuntimeError("mock session not connected")
        await self._queue.put(
            VoiceEvent(
                type=VoiceEventType.AGENT_TRANSCRIPT,
                text=f"tool {call_id} {'error' if is_error else 'ok'}: {output[:200]}",
            )
        )

    async def interrupt_agent(self) -> None:
        return None

    async def end(self) -> None:
        if not self._connected:
            return
        await self._queue.put(
            VoiceEvent(type=VoiceEventType.USAGE, usage=self._usage)
        )
        await self._finish()

    async def _finish(self) -> None:
        if not self._connected and not self._gateway_registered:
            return
        self._connected = False
        await self._queue.put(VoiceEvent(type=VoiceEventType.ENDED))
        await self._queue.put(None)
        if self._gateway_registered and self._gateway is not None:
            self._gateway.register_close()
            self._gateway_registered = False

    async def events(self) -> AsyncIterator[VoiceEvent]:
        while True:
            item = await self._queue.get()
            if item is None:
                break
            yield item
