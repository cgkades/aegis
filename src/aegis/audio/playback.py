"""Speaker playback for agent audio."""

from __future__ import annotations

import contextlib
import queue
import threading
from dataclasses import dataclass

import numpy as np

from aegis.audio.resampler import resample_int16
from aegis.util.logging import get_logger

log = get_logger("audio.playback")


@dataclass(slots=True)
class PlaybackConfig:
    device: str | int | None = None
    device_rate_hz: int = 48000
    channels: int = 1
    dtype: str = "int16"
    queue_size: int = 32


class AudioPlayback:
    """Plays int16 PCM, resampling from source rate to device rate as needed."""

    def __init__(self, config: PlaybackConfig | None = None) -> None:
        self.config = config or PlaybackConfig()
        self._queue: queue.Queue[np.ndarray | None] = queue.Queue(
            maxsize=self.config.queue_size
        )
        self._stream = None
        self._running = False
        self._actual_rate_hz = self.config.device_rate_hz or 48000
        self._lock = threading.Lock()
        # Remainder of the chunk currently being drained by the callback. Kept
        # here (never re-queued) so audio plays back in the order it arrived.
        self._carry = np.zeros(0, dtype=np.int16)

    @property
    def sample_rate_hz(self) -> int:
        return self._actual_rate_hz

    def start(self) -> None:
        with self._lock:
            if self._running:
                return
            try:
                import sounddevice as sd
            except Exception as exc:
                raise RuntimeError(
                    "sounddevice is required for playback; install with: "
                    "uv sync --extra audio"
                ) from exc

            rate = self.config.device_rate_hz or None

            channels = self.config.channels

            def _callback(outdata, frames, time_info, status) -> None:  # noqa: ANN001
                if status:
                    log.debug("playback status: %s", status)
                need = frames * channels
                # Accumulate samples in order: leftover carry first, then whole
                # chunks pulled from the queue, until we have a full block.
                buf = self._carry
                while buf.size < need:
                    try:
                        chunk = self._queue.get_nowait()
                    except queue.Empty:
                        break
                    if chunk is None:
                        break
                    buf = np.concatenate((buf, np.asarray(chunk, dtype=np.int16).reshape(-1)))
                if buf.size >= need:
                    out = buf[:need]
                    self._carry = buf[need:]
                else:
                    out = np.zeros(need, dtype=np.int16)
                    out[: buf.size] = buf
                    self._carry = np.zeros(0, dtype=np.int16)
                if channels == 1:
                    outdata[:, 0] = out
                else:
                    outdata[:] = out.reshape(-1, channels)

            self._stream = sd.OutputStream(
                samplerate=rate,
                channels=self.config.channels,
                dtype=self.config.dtype,
                device=None
                if self.config.device in (None, "default")
                else self.config.device,
                callback=_callback,
            )
            self._stream.start()
            self._actual_rate_hz = int(self._stream.samplerate)
            self._running = True
            log.info("playback started rate=%sHz", self._actual_rate_hz)

    def stop(self) -> None:
        with self._lock:
            self._running = False
            if self._stream is not None:
                try:
                    self._stream.stop()
                    self._stream.close()
                except Exception as exc:
                    log.debug("playback stop error: %s", exc)
                self._stream = None
            while not self._queue.empty():
                try:
                    self._queue.get_nowait()
                except queue.Empty:
                    break
            self._carry = np.zeros(0, dtype=np.int16)
            log.info("playback stopped")

    def write(self, pcm: np.ndarray, source_hz: int | None = None) -> None:
        """Enqueue PCM for playback. Optionally resample from ``source_hz``."""
        if not self._running:
            raise RuntimeError("playback not started")
        arr = np.asarray(pcm, dtype=np.int16)
        if source_hz and source_hz != self._actual_rate_hz:
            arr = resample_int16(arr, source_hz, self._actual_rate_hz)
        # Non-blocking so callers on the asyncio event loop never stall waiting
        # for the audio device to drain. On overflow drop the oldest chunk.
        try:
            self._queue.put_nowait(arr)
        except queue.Full:
            with contextlib.suppress(queue.Empty):
                self._queue.get_nowait()
            with contextlib.suppress(queue.Full):
                self._queue.put_nowait(arr)

    def write_bytes(self, data: bytes, source_hz: int) -> None:
        pcm = np.frombuffer(data, dtype="<i2")
        self.write(pcm, source_hz=source_hz)

    def __enter__(self) -> AudioPlayback:
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.stop()
