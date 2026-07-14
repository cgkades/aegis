"""High-level audio graph: one capture rate, branched consumer rates + VAD."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from aegis.audio.capture import AudioCapture, CaptureConfig
from aegis.audio.playback import AudioPlayback, PlaybackConfig
from aegis.audio.resampler import resample_int16
from aegis.audio.vad import EnergyVad, EnergyVadConfig
from aegis.config.schema import AudioConfig


@dataclass(slots=True)
class AudioGraphConfig:
    capture_rate_hz: int = 48000
    wake_rate_hz: int = 16000
    session_rate_hz: int = 24000
    channels: int = 1
    input_device: str | int | None = "default"
    output_device: str | int | None = "default"
    local_vad_enabled: bool = True
    local_vad_hangover_ms: int = 300

    @classmethod
    def from_audio_config(cls, cfg: AudioConfig) -> AudioGraphConfig:
        return cls(
            capture_rate_hz=cfg.capture_rate_hz,
            wake_rate_hz=cfg.wake_sample_rate_hz,
            session_rate_hz=cfg.session_sample_rate_hz,
            channels=cfg.channels,
            input_device=None if cfg.input_device == "default" else cfg.input_device,
            output_device=None if cfg.output_device == "default" else cfg.output_device,
            local_vad_enabled=cfg.local_vad_enabled,
            local_vad_hangover_ms=cfg.local_vad_hangover_ms,
        )


class AudioGraph:
    """Owns capture + playback and provides resampled views + uplink VAD."""

    def __init__(self, config: AudioGraphConfig | None = None) -> None:
        self.config = config or AudioGraphConfig()
        self.capture = AudioCapture(
            CaptureConfig(
                device=self.config.input_device,
                device_rate_hz=self.config.capture_rate_hz,
                channels=self.config.channels,
            )
        )
        self.playback = AudioPlayback(
            PlaybackConfig(
                device=self.config.output_device,
                device_rate_hz=self.config.capture_rate_hz,
                channels=self.config.channels,
            )
        )
        self.vad = EnergyVad(
            EnergyVadConfig(
                sample_rate_hz=self.config.session_rate_hz,
                hangover_ms=self.config.local_vad_hangover_ms,
            )
        )

    def start(self, *, capture_only: bool = False) -> None:
        """Start capture, and (unless capture_only) playback.

        The always-on daemon runs its wake loop with ``capture_only=True`` so it
        does not hold an unused output stream open 24/7 (which keeps the audio
        device from suspending). Rolls back the capture stream if playback fails,
        so a partial failure never leaves the microphone open.
        """
        self.capture.start()
        if capture_only:
            return
        try:
            self.playback.start()
        except Exception:
            self.capture.stop()
            raise

    def stop(self) -> None:
        self.capture.stop()
        self.playback.stop()
        self.vad.reset()

    def to_wake_rate(self, pcm: np.ndarray) -> np.ndarray:
        return resample_int16(pcm, self.capture.sample_rate_hz, self.config.wake_rate_hz)

    def to_session_rate(self, pcm: np.ndarray) -> np.ndarray:
        return resample_int16(
            pcm, self.capture.sample_rate_hz, self.config.session_rate_hz
        )

    def uplink_frame(self, capture_pcm: np.ndarray) -> np.ndarray | None:
        """Return session-rate PCM if VAD says to send; else None when gating.

        When VAD is disabled, always returns the resampled frame.
        """
        session_pcm = self.to_session_rate(capture_pcm)
        if not self.config.local_vad_enabled:
            return session_pcm
        if self.vad.should_uplink(session_pcm):
            return session_pcm
        return None

    def play_session_audio(self, pcm: np.ndarray) -> None:
        """Play PCM that is already at session rate (e.g. 24 kHz from Realtime)."""
        self.playback.write(pcm, source_hz=self.config.session_rate_hz)

    def __enter__(self) -> AudioGraph:
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.stop()
