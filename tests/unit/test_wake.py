"""Wake engine and confirm-speech tests."""

from __future__ import annotations

import numpy as np

from aegis.wake.base import WakeEvent
from aegis.wake.mock import MockWakeEngine
from aegis.wake.scores import ConfirmSpeechGate


def test_mock_triggers_after_frames() -> None:
    engine = MockWakeEngine(trigger_after_frames=3, energy_threshold=None)
    engine.start()
    silence = np.zeros(1600, dtype=np.int16)
    assert engine.process(silence) is None
    assert engine.process(silence) is None
    event = engine.process(silence)
    assert event is not None
    assert event.phrase == "hey_aegis"
    assert event.engine == "mock"
    engine.stop()


def test_mock_energy_trigger() -> None:
    engine = MockWakeEngine(trigger_after_frames=None, energy_threshold=1000)
    engine.start()
    loud = np.full(1600, 5000, dtype=np.int16)
    event = engine.process(loud)
    assert event is not None
    engine.stop()


def test_confirm_speech_immediate_when_timeout_zero() -> None:
    gate = ConfirmSpeechGate(timeout_s=0)
    event = WakeEvent(phrase="hey_aegis", score=0.9, engine="mock")
    assert gate.on_wake(event) is event


def test_confirm_speech_requires_energy() -> None:
    gate = ConfirmSpeechGate(timeout_s=5.0, sample_rate_hz=16000)
    event = WakeEvent(phrase="hey_aegis", score=0.9, engine="mock")
    assert gate.on_wake(event) is None
    silence = np.zeros(1600, dtype=np.int16)
    assert gate.process_audio(silence) is None
    loud = np.full(1600, 5000, dtype=np.int16)
    confirmed = gate.process_audio(loud)
    assert confirmed is not None
    assert confirmed.phrase == "hey_aegis"
