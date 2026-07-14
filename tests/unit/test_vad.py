"""Energy VAD unit tests."""

from __future__ import annotations

import numpy as np

from aegis.audio.vad import EnergyVad, EnergyVadConfig


def test_silence_not_uplinked() -> None:
    vad = EnergyVad(EnergyVadConfig(sample_rate_hz=24000, energy_threshold=500, hangover_ms=0))
    silence = np.zeros(480, dtype=np.int16)  # 20ms @ 24k
    assert vad.should_uplink(silence) is False


def test_loud_frame_uplinked() -> None:
    vad = EnergyVad(
        EnergyVadConfig(
            sample_rate_hz=24000,
            energy_threshold=500,
            hangover_ms=0,
            min_speech_ms=0,
        )
    )
    loud = np.full(480, 3000, dtype=np.int16)
    assert vad.should_uplink(loud) is True


def test_hangover_keeps_uplink() -> None:
    vad = EnergyVad(
        EnergyVadConfig(
            sample_rate_hz=1000,
            energy_threshold=500,
            hangover_ms=50,  # 50 samples
            min_speech_ms=0,
        )
    )
    loud = np.full(10, 3000, dtype=np.int16)
    silence = np.zeros(10, dtype=np.int16)
    assert vad.should_uplink(loud) is True
    # Within hangover window, silence still uplinks
    assert vad.should_uplink(silence) is True
