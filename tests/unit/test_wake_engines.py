"""Wake engine factory and optional backends."""

from __future__ import annotations

from types import ModuleType
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from aegis.config.schema import WakeConfig, WakeEngine
from aegis.wake.factory import create_wake_engine
from aegis.wake.mock import MockWakeEngine
from aegis.wake.openwakeword import OpenWakeWordEngine
from aegis.wake.porcupine import PorcupineEngine


def test_create_openwakeword_engine() -> None:
    eng = create_wake_engine(WakeConfig(engine=WakeEngine.OPENWAKEWORD))
    assert isinstance(eng, OpenWakeWordEngine)


def test_create_porcupine_engine() -> None:
    eng = create_wake_engine(WakeConfig(engine=WakeEngine.PORCUPINE))
    assert isinstance(eng, PorcupineEngine)


def test_openwakeword_start_missing() -> None:
    eng = OpenWakeWordEngine()
    with pytest.raises(RuntimeError, match="openwakeword"):
        # force import failure
        with patch.dict("sys.modules", {"openwakeword": None, "openwakeword.model": None}):
            import builtins

            real = builtins.__import__

            def fail(name, *a, **k):
                if "openwakeword" in name:
                    raise ImportError("no")
                return real(name, *a, **k)

            with patch("builtins.__import__", side_effect=fail):
                eng.start()


def test_openwakeword_requires_a_custom_model_for_hey_aegis() -> None:
    eng = OpenWakeWordEngine()
    package = ModuleType("openwakeword")
    model_module = ModuleType("openwakeword.model")
    model_module.Model = MagicMock()  # type: ignore[attr-defined]
    with patch.dict(
        "sys.modules", {"openwakeword": package, "openwakeword.model": model_module}
    ):
        with pytest.raises(RuntimeError, match="custom_model_path"):
            eng.start()


def test_openwakeword_process_mocked() -> None:
    eng = OpenWakeWordEngine(threshold=0.5)
    model = MagicMock()
    model.predict.return_value = {"hey_jarvis": 0.9}
    eng._model = model
    event = eng.process(np.zeros(1600, dtype=np.int16))
    assert event is not None
    assert event.score >= 0.5
    # cooldown
    assert eng.process(np.zeros(1600, dtype=np.int16)) is None
    eng.reset()
    eng.stop()


def test_porcupine_start_missing_key(monkeypatch) -> None:
    monkeypatch.delenv("PICOVOICE_ACCESS_KEY", raising=False)
    eng = PorcupineEngine()
    # pvporcupine may not be installed
    try:
        eng.start()
        pytest.fail("should have failed")
    except RuntimeError as exc:
        assert "pvporcupine" in str(exc) or "PICOVOICE" in str(exc)


def test_porcupine_process_mocked() -> None:
    eng = PorcupineEngine()
    porc = MagicMock()
    porc.frame_length = 4
    porc.process.return_value = 0
    eng._porcupine = porc
    eng._frame_length = 4
    audio = np.arange(10, dtype=np.int16)
    event = eng.process(audio)
    assert event is not None
    eng.stop()


def test_mock_not_started() -> None:
    eng = MockWakeEngine()
    with pytest.raises(RuntimeError):
        eng.process(np.zeros(10, dtype=np.int16))
