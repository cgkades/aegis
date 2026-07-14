"""Wake detection helpers: confirm-speech window after KWS hit."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import numpy as np

from aegis.audio.vad import EnergyVad, EnergyVadConfig
from aegis.wake.base import WakeEvent


@dataclass(slots=True)
class ConfirmSpeechGate:
    """After a wake hit, require speech energy within a timeout before connect.

    Reduces cost from false accepts (DESIGN Issue 24).
    """

    timeout_s: float = 1.5
    sample_rate_hz: int = 16000
    _deadline: float | None = field(default=None, init=False, repr=False)
    _pending: WakeEvent | None = field(default=None, init=False, repr=False)
    _vad: EnergyVad = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._vad = EnergyVad(
            EnergyVadConfig(
                sample_rate_hz=self.sample_rate_hz,
                energy_threshold=400.0,
                hangover_ms=0,
                min_speech_ms=40,
            )
        )

    @property
    def waiting(self) -> bool:
        return self._pending is not None

    def on_wake(self, event: WakeEvent) -> WakeEvent | None:
        """Register a wake hit. If timeout is 0, accept immediately."""
        if self.timeout_s <= 0:
            return event
        self._pending = event
        self._deadline = time.monotonic() + self.timeout_s
        self._vad.reset()
        return None

    def process_audio(self, pcm_16k: np.ndarray) -> WakeEvent | None:
        """Feed post-wake audio; return confirmed WakeEvent or None."""
        if self._pending is None or self._deadline is None:
            return None
        if time.monotonic() > self._deadline:
            self.clear()
            return None
        if self._vad.should_uplink(pcm_16k):
            event = self._pending
            self.clear()
            return event
        return None

    def clear(self) -> None:
        self._pending = None
        self._deadline = None
        self._vad.reset()
