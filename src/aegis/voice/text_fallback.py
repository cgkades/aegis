"""Text fallback session (no full-duplex) — cost-saver stub."""

from __future__ import annotations

from collections.abc import AsyncIterator

from aegis.config.schema import AegisConfig, SessionConfig
from aegis.voice.protocol import VoiceEvent, VoiceEventType


class TextFallbackSession:
    """STT→LLM→TTS pipeline placeholder for cost profile (deferred quality)."""

    def __init__(self, cfg: AegisConfig | None = None) -> None:
        self.cfg = cfg

    async def connect(self, config: SessionConfig) -> None:
        raise NotImplementedError(
            "text_fallback provider is not fully implemented yet. "
            "Use session.provider = 'realtime' for production voice."
        )

    async def send_audio(self, pcm16_24k_mono: bytes) -> None:
        raise NotImplementedError("text_fallback not available")

    async def send_tool_result(
        self,
        call_id: str,
        output: str,
        *,
        is_error: bool = False,
    ) -> None:
        raise NotImplementedError("text_fallback not available")

    async def interrupt_agent(self) -> None:
        return None

    async def end(self) -> None:
        return None

    async def events(self) -> AsyncIterator[VoiceEvent]:
        if False:  # pragma: no cover
            yield VoiceEvent(type=VoiceEventType.ENDED)
        raise NotImplementedError("text_fallback not available")
