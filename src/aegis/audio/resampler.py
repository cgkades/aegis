"""Software sample-rate conversion for the single-graph audio design."""

from __future__ import annotations

from functools import lru_cache

import numpy as np


@lru_cache(maxsize=32)
def _interp_grids(n_src: int, n_dst: int) -> tuple[np.ndarray, np.ndarray]:
    """Cached source/target sample-position grids.

    Frame sizes in the audio graph are constant (fixed block size × fixed rate),
    so this runs 50×/sec on the same few (n_src, n_dst) pairs — building the grids
    once instead of per frame removes two ``np.linspace`` allocations per call.
    """
    x_old = np.linspace(0.0, 1.0, n_src, dtype=np.float32)
    x_new = np.linspace(0.0, 1.0, n_dst, dtype=np.float32)
    return x_old, x_new


def resample_int16(
    pcm: np.ndarray,
    src_hz: int,
    dst_hz: int,
) -> np.ndarray:
    """Resample mono or multi-channel int16 PCM using linear interpolation.

    Parameters
    ----------
    pcm:
        Shape ``(n,)`` or ``(n, channels)``, dtype int16 (or castable).
    src_hz / dst_hz:
        Source and destination sample rates. If equal, returns a copy.

    Returns
    -------
    np.ndarray
        Same channel layout, dtype int16.
    """
    if src_hz <= 0 or dst_hz <= 0:
        raise ValueError("sample rates must be positive")
    # Check equal-rate before any float conversion (avoids a wasted copy on the
    # common no-op path).
    if src_hz == dst_hz:
        return np.asarray(pcm, dtype=np.int16)

    src16 = np.asarray(pcm, dtype=np.int16)
    if src16.size == 0:
        return src16

    # Fast path: exact integer downsample ratio (e.g. 48k→16k = 3:1, 48k→24k = 2:1)
    # is plain decimation — ~10× cheaper than np.interp and allocation-light.
    if src_hz % dst_hz == 0:
        step = src_hz // dst_hz
        return src16[::step] if src16.ndim == 1 else src16[::step, :]

    arr = src16.astype(np.float32)
    mono = arr.ndim == 1
    if mono:
        arr = arr.reshape(-1, 1)

    n_src = arr.shape[0]
    n_dst = max(1, int(round(n_src * dst_hz / src_hz)))
    if n_src == 1:
        out = np.repeat(arr, n_dst, axis=0)
    else:
        x_old, x_new = _interp_grids(n_src, n_dst)
        channels = [np.interp(x_new, x_old, arr[:, ch]) for ch in range(arr.shape[1])]
        out = np.stack(channels, axis=1)

    out = np.clip(out, -32768, 32767).astype(np.int16)
    if mono:
        return out.reshape(-1)
    return out


def bytes_to_int16(data: bytes, channels: int = 1) -> np.ndarray:
    """Decode little-endian int16 PCM bytes to ndarray."""
    arr = np.frombuffer(data, dtype="<i2")
    if channels > 1:
        if arr.size % channels != 0:
            raise ValueError("byte length not divisible by channel count")
        return arr.reshape(-1, channels)
    return arr


def int16_to_bytes(pcm: np.ndarray) -> bytes:
    """Encode int16 PCM ndarray to little-endian bytes."""
    return np.asarray(pcm, dtype="<i2").tobytes()
