"""Daemon wake path and dispatch coverage."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest

from aegis.config import build_config
from aegis.config.paths import AegisPaths
from aegis.daemon import AegisDaemon
from aegis.session.events import SessionState
from aegis.wake.base import WakeEvent


@pytest.fixture
def paths(tmp_path: Path) -> AegisPaths:
    p = AegisPaths(
        config_dir=tmp_path / "c",
        state_dir=tmp_path / "s",
        data_dir=tmp_path / "d",
        cache_dir=tmp_path / "k",
    )
    p.ensure_dirs()
    return p


@pytest.mark.asyncio
async def test_dispatch_unknown_op(paths: AegisPaths) -> None:
    cfg = build_config({"wake": {"enabled": False}})
    d = AegisDaemon(cfg, paths)
    resp = await d._dispatch_ipc("nope", "1", {})
    assert not resp.ok


@pytest.mark.asyncio
async def test_start_session_when_busy(paths: AegisPaths) -> None:
    cfg = build_config({"wake": {"enabled": False}})
    d = AegisDaemon(cfg, paths)
    d.machine._state = SessionState.ACTIVE  # noqa: SLF001
    r = await d._start_session(source="test")
    assert r["started"] is False


@pytest.mark.asyncio
async def test_wake_loop_triggers_session(paths: AegisPaths, monkeypatch) -> None:
    cfg = build_config(
        {
            "wake": {"enabled": True, "confirm_speech_timeout_s": 0},
        }
    )
    d = AegisDaemon(cfg, paths)
    started = asyncio.Event()

    async def fake_start(**kwargs):
        started.set()
        return {"started": True}

    d._start_session = fake_start  # type: ignore[method-assign]
    d._wake = MagicMock()
    d._wake.process.return_value = WakeEvent("hey_aegis", 0.9, "mock")
    d._graph = MagicMock()
    d._graph.capture.read.return_value = np.zeros(4800, dtype=np.int16)
    d._graph.to_wake_rate.side_effect = lambda x: x

    task = asyncio.create_task(d._wake_loop())
    await asyncio.wait_for(started.wait(), timeout=2)
    d._stop.set()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
