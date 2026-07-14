"""Microphone capture with single-graph open + software branch rates."""

from __future__ import annotations

import queue
import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass

import numpy as np

from aegis.audio.resampler import resample_int16
from aegis.util.logging import get_logger

log = get_logger("audio.capture")

FrameCallback = Callable[[np.ndarray], None]


@dataclass(slots=True)
class CaptureConfig:
    """Capture opens once at ``device_rate_hz`` (or native if 0)."""

    device: str | int | None = None
    device_rate_hz: int = 48000  # 0 = let backend choose
    channels: int = 1
    block_duration_ms: int = 20
    dtype: str = "int16"


class AudioCapture:
    """PortAudio/sounddevice capture feeding a thread-safe PCM queue.

    Design: keep one input stream open; callers resample to 16 kHz (wake) or
    24 kHz (session) via :func:`aegis.audio.resampler.resample_int16`.
    """

    def __init__(self, config: CaptureConfig | None = None) -> None:
        self.config = config or CaptureConfig()
        self._queue: queue.Queue[np.ndarray | None] = queue.Queue(maxsize=64)
        self._stream = None
        self._running = False
        self._actual_rate_hz: int = self.config.device_rate_hz or 48000
        self._lock = threading.Lock()

    @property
    def sample_rate_hz(self) -> int:
        return self._actual_rate_hz

    @property
    def is_running(self) -> bool:
        return self._running

    def start(self) -> None:
        with self._lock:
            if self._running:
                return
            try:
                import sounddevice as sd
            except Exception as exc:
                raise RuntimeError(
                    "sounddevice is required for capture; install with: "
                    "uv sync --extra audio"
                ) from exc

            rate = self.config.device_rate_hz or None
            blocksize = None
            if rate:
                blocksize = max(1, int(rate * self.config.block_duration_ms / 1000))

            def _callback(indata, frames, time_info, status) -> None:  # noqa: ANN001
                if status:
                    log.debug("capture status: %s", status)
                # indata is float32 by default unless dtype set — force int16 stream
                pcm = np.asarray(indata, dtype=np.int16).copy()
                if pcm.ndim > 1 and pcm.shape[1] == 1:
                    pcm = pcm.reshape(-1)
                try:
                    self._queue.put_nowait(pcm)
                except queue.Full:
                    try:
                        self._queue.get_nowait()
                    except queue.Empty:
                        pass
                    try:
                        self._queue.put_nowait(pcm)
                    except queue.Full:
                        pass

            self._stream = sd.InputStream(
                samplerate=rate,
                channels=self.config.channels,
                dtype=self.config.dtype,
                device=None if self.config.device in (None, "default") else self.config.device,
                blocksize=blocksize,
                callback=_callback,
            )
            self._stream.start()
            self._actual_rate_hz = int(self._stream.samplerate)
            self._running = True
            log.info(
                "capture started rate=%sHz device=%s",
                self._actual_rate_hz,
                self.config.device,
            )

    def stop(self) -> None:
        with self._lock:
            self._running = False
            if self._stream is not None:
                try:
                    self._stream.stop()
                    self._stream.close()
                except Exception as exc:
                    log.debug("capture stop error: %s", exc)
                self._stream = None
            # Unblock iterators
            try:
                self._queue.put_nowait(None)
            except queue.Full:
                pass
            log.info("capture stopped")

    def read(self, timeout: float | None = 1.0) -> np.ndarray | None:
        """Read one capture block; None on timeout or stop sentinel."""
        try:
            item = self._queue.get(timeout=timeout)
        except queue.Empty:
            return None
        return item

    def frames(self, timeout: float | None = 1.0) -> Iterator[np.ndarray]:
        """Yield PCM blocks until stopped."""
        while self._running:
            frame = self.read(timeout=timeout)
            if frame is None:
                continue
            yield frame

    def frames_at(self, target_hz: int, timeout: float | None = 1.0) -> Iterator[np.ndarray]:
        """Yield frames resampled to ``target_hz``."""
        for frame in self.frames(timeout=timeout):
            yield resample_int16(frame, self._actual_rate_hz, target_hz)

    def __enter__(self) -> AudioCapture:
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.stop()
