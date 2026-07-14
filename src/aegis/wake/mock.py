"""Deterministic mock wake engine for tests and dogfood without models."""

from __future__ import annotations

import numpy as np

from aegis.wake.base import WakeEvent


class MockWakeEngine:
    """Triggers after N frames or when RMS exceeds a threshold."""

    name = "mock"

    def __init__(
        self,
        *,
        phrase: str = "hey_aegis",
        trigger_after_frames: int | None = None,
        energy_threshold: float | None = 5000.0,
    ) -> None:
        self.phrase = phrase
        self.trigger_after_frames = trigger_after_frames
        self.energy_threshold = energy_threshold
        self._frames = 0
        self._started = False

    def start(self) -> None:
        self._started = True
        self._frames = 0

    def stop(self) -> None:
        self._started = False

    def reset(self) -> None:
        self._frames = 0

    def process(self, pcm_16k: np.ndarray) -> WakeEvent | None:
        if not self._started:
            raise RuntimeError("engine not started")
        self._frames += 1
        if self.trigger_after_frames is not None and self._frames >= self.trigger_after_frames:
            self._frames = 0
            return WakeEvent(phrase=self.phrase, score=1.0, engine=self.name)
        if self.energy_threshold is not None:
            arr = np.asarray(pcm_16k, dtype=np.float32)
            if arr.size and float(np.sqrt(np.mean(np.square(arr)))) >= self.energy_threshold:
                return WakeEvent(phrase=self.phrase, score=0.99, engine=self.name)
        return None
