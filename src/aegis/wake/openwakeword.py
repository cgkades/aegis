"""openWakeWord backend (optional dependency)."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from aegis.util.logging import get_logger
from aegis.wake.base import WakeEvent

log = get_logger("wake.openwakeword")


class OpenWakeWordEngine:
    """Wraps openWakeWord if installed; otherwise raises on start."""

    name = "openwakeword"

    def __init__(
        self,
        *,
        phrase: str = "hey_aegis",
        threshold: float = 0.5,
        custom_model_path: str = "",
        # Fallback pretrained models when custom phrase model missing
        model_names: list[str] | None = None,
    ) -> None:
        self.phrase = phrase
        self.threshold = threshold
        self.custom_model_path = custom_model_path
        self.model_names = model_names or ["hey_jarvis"]  # closest built-in until custom
        self._model = None
        self._cooldown_frames = 0
        # openWakeWord's feature pipeline is designed for 1280-sample (80ms @ 16kHz)
        # chunks. We're fed 320-sample (20ms) frames 50×/sec, so buffer up to a full
        # chunk before calling predict() — 4× fewer inference calls in the idle loop.
        self._chunk_samples = 1280
        self._buf = np.zeros(0, dtype=np.int16)

    def start(self) -> None:
        try:
            from openwakeword.model import Model
        except Exception as exc:
            raise RuntimeError(
                "openwakeword is not installed. Install wake deps or use a mock engine."
            ) from exc

        kwargs: dict = {}
        if self.custom_model_path:
            path = Path(self.custom_model_path).expanduser()
            if not path.is_file():
                raise FileNotFoundError(f"wake model not found: {path}")
            kwargs["wakeword_models"] = [str(path)]
        else:
            # Built-in models; custom "hey_aegis" requires training (Phase 1).
            kwargs["wakeword_models"] = self.model_names

        self._model = Model(**kwargs)
        log.info(
            "openWakeWord started phrase=%s models=%s threshold=%s",
            self.phrase,
            kwargs.get("wakeword_models"),
            self.threshold,
        )

    def stop(self) -> None:
        self._model = None

    def reset(self) -> None:
        if self._model is not None:
            try:
                self._model.reset()
            except Exception:
                pass
        self._cooldown_frames = 0
        self._buf = np.zeros(0, dtype=np.int16)

    def process(self, pcm_16k: np.ndarray) -> WakeEvent | None:
        if self._model is None:
            raise RuntimeError("engine not started")

        audio = np.asarray(pcm_16k, dtype=np.int16).reshape(-1)
        if audio.size:
            self._buf = np.concatenate([self._buf, audio])

        event: WakeEvent | None = None
        # Drain accumulated audio one full chunk at a time.
        while self._buf.size >= self._chunk_samples:
            chunk = self._buf[: self._chunk_samples]
            self._buf = self._buf[self._chunk_samples :]
            if self._cooldown_frames > 0:
                self._cooldown_frames -= 1
                continue
            # openWakeWord expects int16 mono at 16 kHz
            prediction = self._model.predict(chunk)
            best_name = None
            best_score = 0.0
            for name, score in prediction.items():
                s = float(score)
                if s > best_score:
                    best_score = s
                    best_name = name
            if best_name is not None and best_score >= self.threshold:
                self._cooldown_frames = 15  # debounce ~ few hundred ms
                event = WakeEvent(phrase=best_name, score=best_score, engine=self.name)
        return event
