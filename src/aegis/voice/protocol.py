"""Minimal provider-agnostic voice session protocol.

Realtime-specific event names stay out of session.machine and this surface.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from aegis.config.schema import SessionConfig


@dataclass(slots=True)
class ToolCallRequest:
    call_id: str
    name: str
    arguments: dict[str, Any]


@dataclass(slots=True)
class UsageSnapshot:
    input_audio_tokens: int = 0
    output_audio_tokens: int = 0
    input_text_tokens: int = 0
    output_text_tokens: int = 0
    cached_input_tokens: int = 0
    # Modality split of cached_input_tokens when the server reports it. Cost
    # estimation needs it: cached text and cached audio are priced from very
    # different base rates.
    cached_input_audio_tokens: int = 0
    cached_input_text_tokens: int = 0
    raw: dict[str, Any] | None = None

    def merge(self, other: UsageSnapshot) -> UsageSnapshot:
        return UsageSnapshot(
            input_audio_tokens=self.input_audio_tokens + other.input_audio_tokens,
            output_audio_tokens=self.output_audio_tokens + other.output_audio_tokens,
            input_text_tokens=self.input_text_tokens + other.input_text_tokens,
            output_text_tokens=self.output_text_tokens + other.output_text_tokens,
            cached_input_tokens=self.cached_input_tokens + other.cached_input_tokens,
            cached_input_audio_tokens=(
                self.cached_input_audio_tokens + other.cached_input_audio_tokens
            ),
            cached_input_text_tokens=(
                self.cached_input_text_tokens + other.cached_input_text_tokens
            ),
            raw=other.raw or self.raw,
        )


class VoiceEventType(StrEnum):
    READY = "ready"
    AGENT_AUDIO = "agent_audio"
    # The user started speaking — a turn boundary that, unlike USER_TRANSCRIPT,
    # does not depend on the separate transcription pass succeeding.
    USER_TURN_STARTED = "user_turn_started"
    USER_TRANSCRIPT = "user_transcript"
    AGENT_TRANSCRIPT = "agent_transcript"
    TOOL_CALL = "tool_call"
    REMOTE_TOOL_ACTIVITY = "remote_tool_activity"
    USAGE = "usage"
    ERROR = "error"
    ENDED = "ended"


@dataclass(slots=True)
class VoiceEvent:
    type: VoiceEventType
    # Optional payloads by type
    pcm16: bytes | None = None
    text: str | None = None
    tool_call: ToolCallRequest | None = None
    usage: UsageSnapshot | None = None
    message: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class VoiceSession(Protocol):
    """Minimal session: adapters map provider events → VoiceEvent.

    Error contract, which implementations must follow so callers can rely on it:

    * ``send_tool_result`` and ``inject_user_text`` raise ``RuntimeError`` when
      the session is not connected. Dropping a tool result silently leaves the
      model waiting forever with no way to notice.
    * ``send_audio`` is best-effort and never raises for a backend that has no
      use for microphone input (a text-only chat provider ignores it); it does
      raise when a voice backend is not connected.
    * ``USAGE`` events are **deltas**, not running totals. Consumers sum them,
      so an implementation that also emits a cumulative snapshot at the end
      makes every consumer double-count.
    """

    async def connect(self, config: SessionConfig) -> None: ...

    async def send_audio(self, pcm16_24k_mono: bytes) -> None: ...

    async def send_tool_result(
        self,
        call_id: str,
        output: str,
        *,
        is_error: bool = False,
    ) -> None: ...

    async def interrupt_agent(self) -> None: ...

    async def end(self) -> None: ...

    def events(self) -> AsyncIterator[VoiceEvent]: ...
