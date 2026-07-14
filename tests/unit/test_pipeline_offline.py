"""AudioGraph offline helpers (no sounddevice required)."""

from __future__ import annotations

import numpy as np

from aegis.audio.pipeline import AudioGraph, AudioGraphConfig


def test_resample_branches() -> None:
    graph = AudioGraph(
        AudioGraphConfig(
            capture_rate_hz=48000,
            wake_rate_hz=16000,
            session_rate_hz=24000,
            local_vad_enabled=False,
        )
    )
    # Fake that capture rate is 48k without opening device
    graph.capture._actual_rate_hz = 48000  # noqa: SLF001
    pcm = np.zeros(4800, dtype=np.int16)  # 100ms @ 48k
    wake = graph.to_wake_rate(pcm)
    session = graph.to_session_rate(pcm)
    assert abs(wake.shape[0] - 1600) <= 1
    assert abs(session.shape[0] - 2400) <= 1


def test_uplink_gates_silence() -> None:
    graph = AudioGraph(
        AudioGraphConfig(
            capture_rate_hz=48000,
            session_rate_hz=24000,
            local_vad_enabled=True,
            local_vad_hangover_ms=0,
        )
    )
    graph.capture._actual_rate_hz = 48000  # noqa: SLF001
    silence = np.zeros(4800, dtype=np.int16)
    assert graph.uplink_frame(silence) is None


def test_uplink_passes_when_vad_disabled() -> None:
    graph = AudioGraph(
        AudioGraphConfig(capture_rate_hz=48000, session_rate_hz=24000, local_vad_enabled=False)
    )
    graph.capture._actual_rate_hz = 48000  # noqa: SLF001
    silence = np.zeros(4800, dtype=np.int16)
    out = graph.uplink_frame(silence)
    assert out is not None
