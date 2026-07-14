"""Full devices module with mocked sounddevice."""

from __future__ import annotations

import importlib
import sys
from unittest.mock import MagicMock

from aegis.audio import devices as devices_mod


def test_list_and_defaults_with_fake_sd(monkeypatch):
    fake = MagicMock()
    fake.query_hostapis.return_value = [{"name": "Pulse"}]
    fake.query_devices.return_value = [
        {
            "name": "In",
            "max_input_channels": 2,
            "max_output_channels": 0,
            "default_samplerate": 44100.0,
            "hostapi": 0,
        },
        {
            "name": "Out",
            "max_input_channels": 0,
            "max_output_channels": 2,
            "default_samplerate": 48000.0,
            "hostapi": 0,
        },
    ]
    fake.default.device = (0, 1)
    fake.query_devices.side_effect = None

    def query_devices(idx=None):
        all_d = [
            {
                "name": "In",
                "max_input_channels": 2,
                "max_output_channels": 0,
                "default_samplerate": 44100.0,
                "hostapi": 0,
            },
            {
                "name": "Out",
                "max_input_channels": 0,
                "max_output_channels": 2,
                "default_samplerate": 48000.0,
                "hostapi": 0,
            },
        ]
        if idx is None:
            return all_d
        return all_d[idx]

    fake.query_devices.side_effect = query_devices
    monkeypatch.setitem(sys.modules, "sounddevice", fake)
    importlib.reload(devices_mod)

    assert devices_mod.sounddevice_available() is True
    devs = devices_mod.list_devices()
    assert len(devs) == 2
    assert devs[0].name == "In"
    din = devices_mod.default_input_device()
    dout = devices_mod.default_output_device()
    assert din is not None and din.name == "In"
    assert dout is not None and dout.name == "Out"

    # error paths
    fake.query_devices.side_effect = RuntimeError("boom")
    assert devices_mod.list_devices() == []
    assert devices_mod.default_input_device() is None
    assert devices_mod.default_output_device() is None
