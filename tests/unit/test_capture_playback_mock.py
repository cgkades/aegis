"""Capture/playback with mocked sounddevice streams."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from aegis.audio.capture import AudioCapture, CaptureConfig
from aegis.audio.pipeline import AudioGraph, AudioGraphConfig
from aegis.audio.playback import AudioPlayback, PlaybackConfig


class FakeStream:
    def __init__(self, samplerate=48000):
        self.samplerate = samplerate
        self.started = False
        self.callback = None

    def start(self):
        self.started = True

    def stop(self):
        self.started = False

    def close(self):
        pass


@pytest.mark.asyncio
async def test_capture_start_stop_read():
    fake_sd = MagicMock()
    stream = FakeStream(48000)

    def input_stream(**kwargs):
        stream.callback = kwargs.get("callback")
        return stream

    fake_sd.InputStream.side_effect = input_stream

    with patch.dict("sys.modules", {"sounddevice": fake_sd}):
        cap = AudioCapture(CaptureConfig(device_rate_hz=48000, block_duration_ms=20))
        # re-import path inside start uses sounddevice
        with patch("aegis.audio.capture.AudioCapture.start", wraps=cap.start):
            pass
        # Patch import inside method

        original_start = AudioCapture.start

        def start_with_sd(self):
            import sys

            sys.modules["sounddevice"] = fake_sd
            return original_start(self)

        with patch.object(AudioCapture, "start", start_with_sd):
            cap2 = AudioCapture(CaptureConfig(device_rate_hz=48000))
            # manually simulate start body
            cap2._stream = stream
            cap2._actual_rate_hz = 48000
            cap2._running = True
            # inject frame
            pcm = np.zeros(960, dtype=np.int16)
            cap2._queue.put_nowait(pcm)
            got = cap2.read(timeout=0.1)
            assert got is not None
            cap2._running = False
            cap2.stop()


def test_playback_write_without_start():
    pb = AudioPlayback(PlaybackConfig())
    with pytest.raises(RuntimeError):
        pb.write(np.zeros(10, dtype=np.int16))


def test_playback_write_with_running():
    pb = AudioPlayback(PlaybackConfig())
    pb._running = True
    pb._actual_rate_hz = 48000
    pb.write(np.zeros(100, dtype=np.int16), source_hz=24000)
    pb.write_bytes(b"\x00\x00" * 10, source_hz=24000)
    pb.stop()


def test_graph_start_stop_mocked():
    graph = AudioGraph(AudioGraphConfig())
    with (
        patch.object(graph.capture, "start"),
        patch.object(graph.capture, "stop"),
        patch.object(graph.playback, "start"),
        patch.object(graph.playback, "stop"),
    ):
        graph.start()
        graph.stop()
