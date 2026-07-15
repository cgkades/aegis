"""Session state machine — owns transitions; no cloud I/O here."""

from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from aegis.session.events import SessionEvent, SessionState, Trigger
from aegis.util.logging import get_logger

log = get_logger("session.machine")

Listener = Callable[[SessionState, SessionState, SessionEvent], None]


class InvalidTransition(Exception):
    """Raised when a trigger is not legal in the current state."""


# Legal transitions: (from_state, trigger) -> to_state
_TRANSITIONS: dict[tuple[SessionState, Trigger], SessionState] = {
    # Idle activations
    (SessionState.IDLE, Trigger.WAKE_WORD): SessionState.WAKING,
    (SessionState.IDLE, Trigger.HOTKEY): SessionState.WAKING,
    (SessionState.IDLE, Trigger.CLI_START): SessionState.WAKING,
    (SessionState.IDLE, Trigger.SOCKET_START): SessionState.WAKING,
    # Waking
    (SessionState.WAKING, Trigger.CAPTURE_READY): SessionState.CONFIRMING,
    # When confirm is disabled, Waking can go straight to Connecting
    (SessionState.WAKING, Trigger.SESSION_READY): SessionState.CONNECTING,  # unused path
    (SessionState.WAKING, Trigger.CANCEL): SessionState.IDLE,
    (SessionState.WAKING, Trigger.AUDIO_ERROR): SessionState.IDLE,
    (SessionState.WAKING, Trigger.SPEECH_CONFIRMED): SessionState.CONNECTING,
    # Confirming
    (SessionState.CONFIRMING, Trigger.SPEECH_CONFIRMED): SessionState.CONNECTING,
    (SessionState.CONFIRMING, Trigger.CONFIRM_TIMEOUT): SessionState.IDLE,
    (SessionState.CONFIRMING, Trigger.CANCEL): SessionState.IDLE,
    # Connecting
    (SessionState.CONNECTING, Trigger.SESSION_READY): SessionState.ACTIVE,
    (SessionState.CONNECTING, Trigger.CONNECT_FAIL): SessionState.IDLE,
    (SessionState.CONNECTING, Trigger.CONNECT_TIMEOUT): SessionState.IDLE,
    (SessionState.CONNECTING, Trigger.CANCEL): SessionState.ENDING,
    # Active
    (SessionState.ACTIVE, Trigger.TOOL_NEEDS_APPROVAL): SessionState.APPROVAL_PENDING,
    (SessionState.ACTIVE, Trigger.GOODBYE): SessionState.ENDING,
    (SessionState.ACTIVE, Trigger.SILENCE_TIMEOUT): SessionState.ENDING,
    (SessionState.ACTIVE, Trigger.MAX_DURATION): SessionState.ENDING,
    (SessionState.ACTIVE, Trigger.MAX_COST): SessionState.ENDING,
    (SessionState.ACTIVE, Trigger.HOTKEY_END): SessionState.ENDING,
    (SessionState.ACTIVE, Trigger.ERROR): SessionState.ENDING,
    (SessionState.ACTIVE, Trigger.CANCEL): SessionState.ENDING,
    # Approval pending
    (SessionState.APPROVAL_PENDING, Trigger.APPROVAL_ALLOW): SessionState.ACTIVE,
    (SessionState.APPROVAL_PENDING, Trigger.APPROVAL_DENY): SessionState.ACTIVE,
    (SessionState.APPROVAL_PENDING, Trigger.APPROVAL_TIMEOUT): SessionState.ACTIVE,
    (SessionState.APPROVAL_PENDING, Trigger.GOODBYE): SessionState.ENDING,
    (SessionState.APPROVAL_PENDING, Trigger.HOTKEY_END): SessionState.ENDING,
    (SessionState.APPROVAL_PENDING, Trigger.MAX_DURATION): SessionState.ENDING,
    (SessionState.APPROVAL_PENDING, Trigger.MAX_COST): SessionState.ENDING,
    (SessionState.APPROVAL_PENDING, Trigger.ERROR): SessionState.ENDING,
    # Ending
    (SessionState.ENDING, Trigger.TEARDOWN_DONE): SessionState.IDLE,
}


@dataclass
class SessionContext:
    """Mutable runtime flags for the current conversation attempt."""

    session_id: str | None = None
    approval_in_flight: bool = False
    mute_uplink: bool = False
    started_at: float | None = None
    confirm_enabled: bool = True
    skip_confirm: bool = False  # hotkey/CLI may skip
    estimated_cost_usd: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


class SessionMachine:
    """Single-owner state machine for wake → session lifecycle.

    Thread-safe for trigger() from audio / IPC threads. Listeners run under the lock
    — keep them short or dispatch work elsewhere.
    """

    def __init__(self) -> None:
        self._state = SessionState.IDLE
        self._ctx = SessionContext()
        self._lock = threading.RLock()
        self._listeners: list[Listener] = []

    @property
    def state(self) -> SessionState:
        return self._state

    @property
    def context(self) -> SessionContext:
        return self._ctx

    @property
    def approval_in_flight(self) -> bool:
        return self._ctx.approval_in_flight

    def add_listener(self, listener: Listener) -> None:
        self._listeners.append(listener)

    def trigger(self, event: SessionEvent | Trigger, **payload: Any) -> SessionState:
        """Apply a trigger and return the new state."""
        if isinstance(event, Trigger):
            event = SessionEvent(trigger=event, payload=payload)
        elif payload:
            # Merge extra kwargs into event payload
            event = SessionEvent(
                trigger=event.trigger,
                payload={**event.payload, **payload},
            )

        with self._lock:
            return self._apply(event)

    def _apply(self, event: SessionEvent) -> SessionState:
        key = (self._state, event.trigger)
        # Special-case: Waking + CAPTURE_READY with skip_confirm → Connecting
        if (
            self._state is SessionState.WAKING
            and event.trigger is Trigger.CAPTURE_READY
            and (self._ctx.skip_confirm or not self._ctx.confirm_enabled)
        ):
            new_state = SessionState.CONNECTING
        else:
            if key not in _TRANSITIONS:
                raise InvalidTransition(
                    f"illegal transition {self._state.value} + {event.trigger.value}"
                )
            new_state = _TRANSITIONS[key]

        old = self._state
        self._on_enter(old, new_state, event)
        self._state = new_state
        log.debug("session %s -> %s (%s)", old.value, new_state.value, event.trigger.value)
        for listener in self._listeners:
            listener(old, new_state, event)
        return new_state

    def _on_enter(
        self,
        old: SessionState,
        new: SessionState,
        event: SessionEvent,
    ) -> None:
        if new is SessionState.WAKING and old is SessionState.IDLE:
            self._ctx = SessionContext(
                session_id=str(uuid.uuid4()),
                started_at=time.monotonic(),
                confirm_enabled=bool(event.payload.get("confirm_enabled", True)),
                skip_confirm=bool(event.payload.get("skip_confirm", False)),
                metadata=dict(event.payload.get("metadata") or {}),
            )
            # CLI/hotkey typically skip confirm
            if event.trigger in {
                Trigger.HOTKEY,
                Trigger.CLI_START,
                Trigger.SOCKET_START,
            }:
                self._ctx.skip_confirm = bool(
                    event.payload.get("skip_confirm", True)
                )

        if new is SessionState.APPROVAL_PENDING:
            self._ctx.approval_in_flight = True
            # Honor tools.approval.mute_uplink_during_approval when provided via payload.
            mute = event.payload.get("mute_uplink")
            self._ctx.mute_uplink = True if mute is None else bool(mute)

        if old is SessionState.APPROVAL_PENDING and new is SessionState.ACTIVE:
            self._ctx.approval_in_flight = False
            self._ctx.mute_uplink = False

        if new is SessionState.ENDING:
            self._ctx.approval_in_flight = False
            self._ctx.mute_uplink = True

        if new is SessionState.IDLE and old is not SessionState.IDLE:
            # Preserve last session_id in metadata for audit; reset flags
            last_id = self._ctx.session_id
            self._ctx = SessionContext(metadata={"last_session_id": last_id})
