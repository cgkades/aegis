"""Construct wake engines from config."""

from __future__ import annotations

from aegis.config.schema import WakeConfig
from aegis.config.schema import WakeEngine as WakeEngineName
from aegis.wake.base import WakeEngine
from aegis.wake.mock import MockWakeEngine
from aegis.wake.openwakeword import OpenWakeWordEngine
from aegis.wake.porcupine import PorcupineEngine


def create_wake_engine(config: WakeConfig, *, allow_mock: bool = False) -> WakeEngine:
    """Create the configured wake engine.

    If ``allow_mock`` is True and the real engine cannot be imported, fall back
    to :class:`MockWakeEngine` (useful for CI).
    """
    if config.engine is WakeEngineName.OPENWAKEWORD:
        try:
            return OpenWakeWordEngine(
                phrase=config.phrase,
                threshold=config.threshold,
                custom_model_path=config.custom_model_path,
            )
        except Exception:
            if allow_mock:
                return MockWakeEngine(phrase=config.phrase)
            return OpenWakeWordEngine(
                phrase=config.phrase,
                threshold=config.threshold,
                custom_model_path=config.custom_model_path,
            )
    if config.engine is WakeEngineName.PORCUPINE:
        return PorcupineEngine(
            phrase=config.phrase,
            access_key_env=config.porcupine_access_key_env,
            keyword_path=config.porcupine_keyword_path,
            sensitivity=config.threshold,
        )
    raise ValueError(f"unsupported wake engine: {config.engine}")
