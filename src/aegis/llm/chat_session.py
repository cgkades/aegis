"""Text chat session for non-Realtime providers (Ollama, LiteLLM, OAuth, API).

Implements the VoiceSession protocol with text in/out so tools and the settings
page can exercise providers without duplex audio. Audio PCM is accepted but
ignored until a cascaded STT/TTS path is wired.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from aegis.config.schema import AegisConfig, SessionConfig
from aegis.llm.client import ChatMessage, LLMClient, create_llm_client
from aegis.voice.protocol import VoiceEvent, VoiceEventType


class ChatLLMSession:
    """Minimal session: connect → ready; text turns via inject_user_text."""

    def __init__(self, cfg: AegisConfig, *, provider: str | None = None) -> None:
        self.cfg = cfg
        self.provider = provider
        self._queue: asyncio.Queue[VoiceEvent | None] = asyncio.Queue()
        self._connected = False
        self._client: LLMClient | None = None
        self._history: list[ChatMessage] = []
        # Cap retained turns so a long session doesn't resend unbounded history
        # every turn (O(n²) tokens) or grow memory without limit. The system
        # message (index 0) is always kept.
        self._max_history = max(2, 2 * cfg.session.context.max_transcript_turns)

    def _prune_history(self) -> None:
        if len(self._history) <= self._max_history:
            return
        system = self._history[:1]
        tail = self._history[-(self._max_history - 1) :]
        self._history = [*system, *tail]

    async def connect(self, config: SessionConfig) -> None:
        self._client = create_llm_client(self.cfg, provider=self.provider)
        self._connected = True
        self._history = [
            ChatMessage(
                role="system",
                content=(
                    "You are Aegis, a local-first ops assistant on the user's Linux machine. "
                    "Be concise and practical."
                ),
            )
        ]
        await self._queue.put(
            VoiceEvent(
                type=VoiceEventType.READY,
                message=f"chat provider={self._client.provider} model={self._client.model}",
            )
        )
        await self._queue.put(
            VoiceEvent(
                type=VoiceEventType.AGENT_TRANSCRIPT,
                text=(
                    f"Aegis chat ready via {self._client.provider} "
                    f"({self._client.model}). Type or use tools."
                ),
            )
        )

    async def send_audio(self, pcm16_24k_mono: bytes) -> None:
        # Cascaded STT not wired yet — ignore PCM for chat providers
        return None

    async def inject_user_text(self, text: str) -> None:
        if not self._connected or self._client is None:
            raise RuntimeError("not connected")
        text = text.strip()
        if not text:
            return
        await self._queue.put(VoiceEvent(type=VoiceEventType.USER_TRANSCRIPT, text=text))
        self._history.append(ChatMessage(role="user", content=text))
        self._prune_history()
        resp = await self._client.chat(self._history)
        self._history.append(ChatMessage(role="assistant", content=resp.text))
        await self._queue.put(
            VoiceEvent(type=VoiceEventType.AGENT_TRANSCRIPT, text=resp.text)
        )

    async def send_tool_result(
        self,
        call_id: str,
        output: str,
        *,
        is_error: bool = False,
    ) -> None:
        note = f"Tool {call_id} {'error' if is_error else 'result'}: {output[:2000]}"
        self._history.append(ChatMessage(role="user", content=note))
        if self._client is None:
            return
        self._prune_history()
        resp = await self._client.chat(self._history)
        self._history.append(ChatMessage(role="assistant", content=resp.text))
        await self._queue.put(
            VoiceEvent(type=VoiceEventType.AGENT_TRANSCRIPT, text=resp.text)
        )

    async def interrupt_agent(self) -> None:
        return None

    async def end(self) -> None:
        if not self._connected:
            return
        self._connected = False
        await self._queue.put(VoiceEvent(type=VoiceEventType.ENDED))
        await self._queue.put(None)

    async def events(self) -> AsyncIterator[VoiceEvent]:
        while True:
            item = await self._queue.get()
            if item is None:
                break
            yield item
