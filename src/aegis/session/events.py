"""Internal session events (not Realtime wire names)."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class SessionState(StrEnum):
    IDLE = "idle"
    WAKING = "waking"
    CONFIRMING = "confirming"
    CONNECTING = "connecting"
    ACTIVE = "active"
    APPROVAL_PENDING = "approval_pending"
    ENDING = "ending"


class Trigger(StrEnum):
    WAKE_WORD = "wake_word"
    HOTKEY = "hotkey"
    CLI_START = "cli_start"
    SOCKET_START = "socket_start"
    SPEECH_CONFIRMED = "speech_confirmed"
    CONFIRM_TIMEOUT = "confirm_timeout"
    CANCEL = "cancel"
    CAPTURE_READY = "capture_ready"
    SESSION_READY = "session_ready"
    CONNECT_FAIL = "connect_fail"
    CONNECT_TIMEOUT = "connect_timeout"
    TOOL_NEEDS_APPROVAL = "tool_needs_approval"
    APPROVAL_ALLOW = "approval_allow"
    APPROVAL_DENY = "approval_deny"
    APPROVAL_TIMEOUT = "approval_timeout"
    GOODBYE = "goodbye"
    SILENCE_TIMEOUT = "silence_timeout"
    MAX_DURATION = "max_duration"
    MAX_COST = "max_cost"
    HOTKEY_END = "hotkey_end"
    ERROR = "error"
    TEARDOWN_DONE = "teardown_done"
    AUDIO_ERROR = "audio_error"


@dataclass(frozen=True, slots=True)
class SessionEvent:
    trigger: Trigger
    payload: dict[str, Any] = field(default_factory=dict)
