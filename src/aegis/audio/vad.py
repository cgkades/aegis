"""Local energy-based VAD for uplink silence gating (Phase 0)."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(slots=True)
class EnergyVadConfig:
    """Simple RMS energy VAD configuration."""

    sample_rate_hz: int = 24000
    # RMS threshold on int16 scale (0–32768). ~500 is conservative for quiet rooms.
    energy_threshold: float = 500.0
    hangover_ms: int = 300
    # Minimum speech run before flipping to speech (debounce)
    min_speech_ms: int = 30


class EnergyVad:
    """Frame-wise energy VAD with hangover for uplink gating.

    Not a neural VAD — good enough to avoid streaming long silence to Realtime.
    """

    def __init__(self, config: EnergyVadConfig | None = None) -> None:
        self.config = config or EnergyVadConfig()
        self._in_speech = False
        self._hangover_samples_left = 0
        self._speech_run_samples = 0
        self._hangover_samples = int(
            self.config.sample_rate_hz * self.config.hangover_ms / 1000
        )
        self._min_speech_samples = int(
            self.config.sample_rate_hz * self.config.min_speech_ms / 1000
        )

    @property
    def in_speech(self) -> bool:
        return self._in_speech

    def reset(self) -> None:
        self._in_speech = False
        self._hangover_samples_left = 0
        self._speech_run_samples = 0

    def process(self, pcm: np.ndarray) -> bool:
        """Process a mono int16 frame; return whether uplink should send this frame.

        During hangover after speech, frames are still considered active so word
        endings are not clipped.
        """
        arr = np.asarray(pcm, dtype=np.float32).reshape(-1)
        if arr.size == 0:
            return self._in_speech or self._hangover_samples_left > 0

        rms = float(np.sqrt(np.mean(np.square(arr))))
        is_loud = rms >= self.config.energy_threshold
        n = arr.size

        if is_loud:
            self._speech_run_samples += n
            if self._speech_run_samples >= self._min_speech_samples:
                self._in_speech = True
            self._hangover_samples_left = self._hangover_samples
        else:
            self._speech_run_samples = 0
            if self._hangover_samples_left > 0:
                self._hangover_samples_left = max(0, self._hangover_samples_left - n)
            if self._hangover_samples_left == 0:
                self._in_speech = False

        return self._in_speech or self._hangover_samples_left > 0

    def should_uplink(self, pcm: np.ndarray) -> bool:
        """Alias for :meth:`process` — True if this frame should leave the host."""
        return self.process(pcm)
