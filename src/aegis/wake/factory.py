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

    Construction is cheap and cannot fail on a missing dependency — the heavy
    import happens in ``engine.start()``. Callers that need a fallback should
    handle ``start()`` raising (the daemon does). ``allow_mock`` is retained for
    CI callers that want a mock without touching real engine internals.
    """
    if allow_mock:
        return MockWakeEngine(phrase=config.phrase)
    if config.engine is WakeEngineName.OPENWAKEWORD:
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
