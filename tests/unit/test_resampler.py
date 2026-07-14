"""Resampler unit tests."""

from __future__ import annotations

import numpy as np

from aegis.audio.resampler import bytes_to_int16, int16_to_bytes, resample_int16


def test_identity_rate() -> None:
    pcm = np.arange(100, dtype=np.int16)
    out = resample_int16(pcm, 16000, 16000)
    assert out.dtype == np.int16
    np.testing.assert_array_equal(out, pcm)


def test_downsample_length() -> None:
    # 1 second of 48 kHz → 16 kHz
    pcm = np.zeros(48000, dtype=np.int16)
    pcm[::100] = 1000
    out = resample_int16(pcm, 48000, 16000)
    assert abs(out.shape[0] - 16000) <= 1


def test_upsample_length() -> None:
    pcm = np.zeros(16000, dtype=np.int16)
    out = resample_int16(pcm, 16000, 24000)
    assert abs(out.shape[0] - 24000) <= 1


def test_bytes_roundtrip() -> None:
    pcm = np.array([0, 1, -1, 32767, -32768], dtype=np.int16)
    raw = int16_to_bytes(pcm)
    back = bytes_to_int16(raw)
    np.testing.assert_array_equal(back, pcm)


def test_empty() -> None:
    out = resample_int16(np.array([], dtype=np.int16), 48000, 16000)
    assert out.size == 0
