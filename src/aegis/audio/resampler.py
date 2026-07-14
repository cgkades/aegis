"""Software sample-rate conversion for the single-graph audio design."""

from __future__ import annotations

import numpy as np


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
    arr = np.asarray(pcm, dtype=np.float32)
    if arr.size == 0:
        return np.asarray(pcm, dtype=np.int16).reshape(arr.shape)

    if src_hz == dst_hz:
        return np.asarray(pcm, dtype=np.int16)

    mono = arr.ndim == 1
    if mono:
        arr = arr.reshape(-1, 1)

    n_src = arr.shape[0]
    n_dst = max(1, int(round(n_src * dst_hz / src_hz)))
    if n_src == 1:
        out = np.repeat(arr, n_dst, axis=0)
    else:
        x_old = np.linspace(0.0, 1.0, n_src, dtype=np.float64)
        x_new = np.linspace(0.0, 1.0, n_dst, dtype=np.float64)
        channels = []
        for ch in range(arr.shape[1]):
            channels.append(np.interp(x_new, x_old, arr[:, ch].astype(np.float64)))
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
