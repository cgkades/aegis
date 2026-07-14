"""Picovoice Porcupine backend (optional)."""

from __future__ import annotations

import os

import numpy as np

from aegis.util.logging import get_logger
from aegis.wake.base import WakeEvent

log = get_logger("wake.porcupine")


class PorcupineEngine:
    """Porcupine wake-word engine — requires pvporcupine + access key."""

    name = "porcupine"

    def __init__(
        self,
        *,
        phrase: str = "hey_aegis",
        access_key_env: str = "PICOVOICE_ACCESS_KEY",
        keyword_path: str = "",
        sensitivity: float = 0.5,
    ) -> None:
        self.phrase = phrase
        self.access_key_env = access_key_env
        self.keyword_path = keyword_path
        self.sensitivity = sensitivity
        self._porcupine = None
        self._frame_length = 512
        self._buf = np.zeros(0, dtype=np.int16)

    def start(self) -> None:
        try:
            import pvporcupine
        except Exception as exc:
            raise RuntimeError(
                "pvporcupine is not installed; cannot use Porcupine engine"
            ) from exc

        access_key = os.environ.get(self.access_key_env, "")
        if not access_key:
            raise RuntimeError(f"set {self.access_key_env} for Porcupine")

        if self.keyword_path:
            self._porcupine = pvporcupine.create(
                access_key=access_key,
                keyword_paths=[self.keyword_path],
                sensitivities=[self.sensitivity],
            )
        else:
            # Built-in keywords — "jarvis" is the closest common default
            keyword = "jarvis"
            self._porcupine = pvporcupine.create(
                access_key=access_key,
                keywords=[keyword],
                sensitivities=[self.sensitivity],
            )
        self._frame_length = int(self._porcupine.frame_length)
        log.info("Porcupine started frame_length=%s", self._frame_length)

    def stop(self) -> None:
        if self._porcupine is not None:
            try:
                self._porcupine.delete()
            except Exception:
                pass
        self._porcupine = None

    def reset(self) -> None:
        # Drop any partial frame buffered across the previous session.
        self._buf = np.zeros(0, dtype=np.int16)

    def process(self, pcm_16k: np.ndarray) -> WakeEvent | None:
        if self._porcupine is None:
            raise RuntimeError("engine not started")
        audio = np.asarray(pcm_16k, dtype=np.int16).reshape(-1)
        # Porcupine needs exact frame lengths; buffer partial frames across calls.
        self._buf = np.concatenate([self._buf, audio])
        event = None
        while self._buf.size >= self._frame_length:
            frame = self._buf[: self._frame_length]
            self._buf = self._buf[self._frame_length :]
            result = self._porcupine.process(frame.tolist())
            if result >= 0:
                event = WakeEvent(
                    phrase=self.phrase,
                    score=1.0,
                    engine=self.name,
                )
        return event
