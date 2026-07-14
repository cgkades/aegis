"""Wake-word engines and confirm-speech gate."""

from aegis.wake.base import WakeEngine, WakeEvent
from aegis.wake.factory import create_wake_engine
from aegis.wake.mock import MockWakeEngine
from aegis.wake.scores import ConfirmSpeechGate

__all__ = [
    "ConfirmSpeechGate",
    "MockWakeEngine",
    "WakeEngine",
    "WakeEvent",
    "create_wake_engine",
]
