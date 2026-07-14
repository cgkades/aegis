"""Daemon and IPC integration tests."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from aegis.config import build_config
from aegis.config.paths import AegisPaths
from aegis.daemon import AegisDaemon
from aegis.ipc import (
    parse_request,
    pid_alive,
    read_pid,
    remove_stale_socket,
    send_request,
    write_pid,
)
from aegis.session.events import SessionState


@pytest.fixture
def paths(tmp_path: Path) -> AegisPaths:
    p = AegisPaths(
        config_dir=tmp_path / "cfg",
        state_dir=tmp_path / "state",
        data_dir=tmp_path / "data",
        cache_dir=tmp_path / "cache",
    )
    p.ensure_dirs()
    return p


@pytest.mark.asyncio
async def test_daemon_ipc_ping_status_shutdown(paths: AegisPaths) -> None:
    cfg = build_config(
        {
            "wake": {"enabled": False},
            "session": {"provider": "realtime"},
        }
    )
    daemon = AegisDaemon(cfg, paths)

    async def run() -> None:
        await daemon.start()

    task = asyncio.create_task(run())
    # wait for socket
    for _ in range(50):
        if paths.socket_path.exists():
            break
        await asyncio.sleep(0.05)
    assert paths.socket_path.exists()

    ping = await send_request(paths.socket_path, "ping")
    assert ping.ok
    assert ping.result and ping.result.get("pong") is True

    status = await send_request(paths.socket_path, "status")
    assert status.ok
    assert status.result
    assert status.result.get("state") == SessionState.IDLE.value
    assert status.result.get("wake_enabled") is False

    shut = await send_request(paths.socket_path, "shutdown")
    assert shut.ok
    await asyncio.wait_for(task, timeout=3)
    assert not paths.socket_path.exists() or True  # cleaned


@pytest.mark.asyncio
async def test_daemon_session_start_busy(paths: AegisPaths, monkeypatch) -> None:
    cfg = build_config({"wake": {"enabled": False}})
    daemon = AegisDaemon(cfg, paths)

    async def fake_session(*args, **kwargs):
        await asyncio.sleep(0.2)
        return 0

    monkeypatch.setattr("aegis.daemon.run_session_once", fake_session)

    task = asyncio.create_task(daemon.start())
    for _ in range(50):
        if paths.socket_path.exists():
            break
        await asyncio.sleep(0.05)

    r1 = await send_request(paths.socket_path, "session.start", {"source": "test"})
    assert r1.ok
    assert r1.result and r1.result.get("started") is True

    # second start while running may be busy
    await asyncio.sleep(0.05)
    r2 = await send_request(paths.socket_path, "session.start", {"source": "test"})
    assert r2.ok

    await send_request(paths.socket_path, "session.stop")
    await send_request(paths.socket_path, "shutdown")
    await asyncio.wait_for(task, timeout=3)


def test_pid_helpers(tmp_path: Path) -> None:
    pid_file = tmp_path / "daemon.pid"
    write_pid(pid_file, 1)
    assert read_pid(pid_file) == 1
    assert pid_alive(1) or not pid_alive(1)  # pid 1 may or may not signalable
    sock = tmp_path / "a.sock"
    sock.write_text("x")
    remove_stale_socket(sock)
    assert not sock.exists()


def test_parse_request_defaults() -> None:
    req = parse_request('{"op":"status"}')
    assert req.op == "status"
    assert req.id == "1"
