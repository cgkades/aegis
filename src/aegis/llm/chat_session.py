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
from aegis.tools.sanitize import truncate_preserving_fence
from aegis.util.instructions import with_security_block
from aegis.voice.protocol import VoiceEvent, VoiceEventType

_DEFAULT_INSTRUCTIONS = with_security_block(
    "You are Aegis, a local-first ops assistant on the user's Linux machine. "
    "Be concise and practical."
)

# Cap on a single tool result carried in chat history. Applied with
# truncate_preserving_fence so the closing delimiter always survives.
_MAX_TOOL_NOTE_CHARS = 2000


class ChatLLMSession:
    """Minimal session: connect → ready; text turns via inject_user_text."""

    def __init__(
        self,
        cfg: AegisConfig,
        *,
        provider: str | None = None,
        instructions: str | None = None,
    ) -> None:
        self.cfg = cfg
        self.provider = provider
        self._instructions = with_security_block(instructions or _DEFAULT_INSTRUCTIONS)
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
                content=self._instructions,
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
        # Intentional no-op, per the VoiceSession contract: cascaded STT is not
        # wired up, so a chat provider has nothing to do with microphone PCM.
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
        if not self._connected or self._client is None:
            # Match Realtime/Mock rather than dropping the result on the floor:
            # a silently discarded tool result leaves the model waiting.
            raise RuntimeError("chat session not connected")
        # `output` arrives already wrapped by the tool loop. Plain slicing cut
        # the closing </untrusted_tool_output> off every result over this cap —
        # i.e. the fencing contract broke for the largest, most suspicious
        # outputs. Truncate inside the fence instead.
        body = truncate_preserving_fence(output, _MAX_TOOL_NOTE_CHARS)
        # There is no tool-role plumbing for these providers (no tool_call_id),
        # so this rides in a user turn. The label sits outside the fence so the
        # model can see the provenance without the content being able to forge
        # it.
        note = f"Tool {call_id} {'error' if is_error else 'result'} follows.\n{body}"
        self._history.append(ChatMessage(role="user", content=note))
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
