"""GPT-Live adapter stub — API not production-ready for third parties yet."""

from __future__ import annotations

from collections.abc import AsyncIterator

from aegis.config.schema import SessionConfig
from aegis.voice.protocol import VoiceEvent


class GptLiveVoiceSession:
    """Placeholder until OpenAI ships a developer GPT-Live API."""

    async def connect(self, config: SessionConfig) -> None:
        raise NotImplementedError(
            "GPT-Live developer API is not available yet. "
            "Use session.provider = 'realtime' (default). "
            "Join the OpenAI waitlist; this stub will be filled when docs land."
        )

    async def send_audio(self, pcm16_24k_mono: bytes) -> None:
        raise NotImplementedError("GPT-Live not available")

    async def send_tool_result(
        self,
        call_id: str,
        output: str,
        *,
        is_error: bool = False,
    ) -> None:
        raise NotImplementedError("GPT-Live not available")

    async def interrupt_agent(self) -> None:
        raise NotImplementedError("GPT-Live not available")

    async def end(self) -> None:
        return None

    async def events(self) -> AsyncIterator[VoiceEvent]:
        if False:  # pragma: no cover
            yield VoiceEvent  # type: ignore[misc]
        raise NotImplementedError("GPT-Live not available")
