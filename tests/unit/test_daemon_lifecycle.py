"""Daemon startup/shutdown robustness.

These cover failures found by actually running the daemon rather than by
reading it: a socket path over the AF_UNIX limit crashed with a bare traceback,
a second daemon would unlink a live daemon's socket, and the PID file was
written before the socket bound so a failed start left a stale one behind.
"""

from __future__ import annotations

import asyncio
import json
import socket
from pathlib import Path

import pytest

from aegis.config import build_config
from aegis.config.paths import AegisPaths
from aegis.daemon import AegisDaemon, DaemonStartError
from aegis.ipc import (
    check_socket_path,
    parse_request,
    remove_stale_socket,
    socket_is_live,
)


def _paths(root: Path) -> AegisPaths:
    p = AegisPaths(
        config_dir=root / "c",
        state_dir=root / "s",
        data_dir=root / "d",
        cache_dir=root / "x",
    )
    p.ensure_dirs()
    return p


def _daemon(root: Path, **overrides) -> AegisDaemon:
    cfg = build_config({"wake": {"enabled": False}, **overrides})
    return AegisDaemon(cfg, _paths(root))


# --------------------------------------------------------------------------- #
# Socket path limits
# --------------------------------------------------------------------------- #


def test_short_socket_path_is_accepted(tmp_path: Path) -> None:
    assert check_socket_path(Path("/tmp/a/aegis.sock")) is None


def test_overlong_socket_path_is_rejected_with_guidance() -> None:
    long_path = Path("/tmp/" + ("x" * 200) + "/aegis.sock")
    error = check_socket_path(long_path)
    assert error is not None
    assert "AF_UNIX" in error
    assert "XDG_STATE_HOME" in error


@pytest.mark.asyncio
async def test_daemon_start_rejects_overlong_socket_path(tmp_path: Path) -> None:
    """Previously an OSError traceback from deep inside asyncio."""
    deep = tmp_path / ("d" * 90) / ("e" * 90)
    daemon = _daemon(tmp_path)
    daemon.paths = AegisPaths(
        config_dir=deep / "c",
        state_dir=deep / "s",
        data_dir=deep / "d",
        cache_dir=deep / "x",
    )
    with pytest.raises(DaemonStartError, match="AF_UNIX"):
        await daemon.start()


@pytest.mark.asyncio
async def test_failed_start_leaves_no_pid_file(tmp_path: Path) -> None:
    """The PID file is claimed only once the socket is bound."""
    deep = tmp_path / ("d" * 90) / ("e" * 90)
    daemon = _daemon(tmp_path)
    daemon.paths = AegisPaths(
        config_dir=deep / "c",
        state_dir=deep / "s",
        data_dir=deep / "d",
        cache_dir=deep / "x",
    )
    with pytest.raises(DaemonStartError):
        await daemon.start()
    assert not daemon.paths.pid_file.exists()


# --------------------------------------------------------------------------- #
# A live daemon's socket must never be reclaimed
# --------------------------------------------------------------------------- #


def test_socket_is_live_detects_a_served_socket(tmp_path: Path) -> None:
    path = tmp_path / "s.sock"
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(path))
    server.listen(1)
    try:
        assert socket_is_live(path) is True
    finally:
        server.close()


def test_socket_is_live_false_for_abandoned_socket_file(tmp_path: Path) -> None:
    path = tmp_path / "s.sock"
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(path))
    server.close()  # file remains, nothing listening
    assert path.exists()
    assert socket_is_live(path) is False


def test_socket_is_live_false_when_missing(tmp_path: Path) -> None:
    assert socket_is_live(tmp_path / "nope.sock") is False


def test_remove_stale_socket_removes_an_abandoned_file(tmp_path: Path) -> None:
    path = tmp_path / "s.sock"
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(path))
    server.close()
    remove_stale_socket(path)
    assert not path.exists()


def test_remove_stale_socket_refuses_to_steal_a_live_socket(tmp_path: Path) -> None:
    """The PID-file guard misses this: cleaned state dir, or a lost PID file."""
    path = tmp_path / "s.sock"
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(path))
    server.listen(1)
    try:
        with pytest.raises(OSError, match="live daemon"):
            remove_stale_socket(path)
        assert path.exists(), "the live daemon's socket must survive"
    finally:
        server.close()


@pytest.mark.asyncio
async def test_second_daemon_refuses_when_socket_is_live(tmp_path: Path) -> None:
    first = _daemon(tmp_path)
    task = asyncio.create_task(first.start())
    try:
        for _ in range(100):
            if first.paths.socket_path.exists():
                break
            await asyncio.sleep(0.02)
        assert first.paths.socket_path.exists()

        second = _daemon(tmp_path)
        with pytest.raises(DaemonStartError, match="live daemon"):
            await second.start()

        # The original is still serving.
        assert socket_is_live(first.paths.socket_path)
    finally:
        first._stop.set()
        await asyncio.wait_for(task, timeout=5)


@pytest.mark.asyncio
async def test_shutdown_removes_socket_and_pid(tmp_path: Path) -> None:
    daemon = _daemon(tmp_path)
    task = asyncio.create_task(daemon.start())
    for _ in range(100):
        if daemon.paths.pid_file.exists():
            break
        await asyncio.sleep(0.02)
    assert daemon.paths.socket_path.exists()
    assert daemon.paths.pid_file.exists()

    daemon._stop.set()
    await asyncio.wait_for(task, timeout=5)

    assert not daemon.paths.socket_path.exists()
    assert not daemon.paths.pid_file.exists()


# --------------------------------------------------------------------------- #
# Control-socket robustness (previously untested)
# --------------------------------------------------------------------------- #


def test_parse_request_rejects_non_object_json() -> None:
    """Used to surface as "'str' object has no attribute 'get'" on the wire."""
    with pytest.raises(ValueError, match="JSON object"):
        parse_request('"just a string"')
    with pytest.raises(ValueError, match="JSON object"):
        parse_request("[1, 2, 3]")


def test_parse_request_accepts_a_normal_request() -> None:
    req = parse_request('{"op":"status","id":"7"}')
    assert req.op == "status"
    assert req.id == "7"


@pytest.mark.asyncio
async def test_daemon_survives_malformed_ipc_and_keeps_serving(tmp_path: Path) -> None:
    """Garbage on the control channel must not take the daemon down."""
    daemon = _daemon(tmp_path)
    task = asyncio.create_task(daemon.start())
    try:
        for _ in range(100):
            if daemon.paths.socket_path.exists():
                break
            await asyncio.sleep(0.02)

        async def roundtrip(payload: bytes) -> bytes:
            reader, writer = await asyncio.open_unix_connection(
                str(daemon.paths.socket_path)
            )
            writer.write(payload)
            await writer.drain()
            writer.write_eof()
            data = await asyncio.wait_for(reader.read(65536), timeout=5)
            writer.close()
            with __import__("contextlib").suppress(Exception):
                await writer.wait_closed()
            return data

        for payload in (
            b"not json\n",
            b'"a string"\n',
            b"[1,2,3]\n",
            b'{"op":"does_not_exist"}\n',
            b"\n",
            b"z" * 200_000 + b"\n",
        ):
            resp = await roundtrip(payload)
            assert b'"ok": false' in resp, payload[:40]
            # Internal Python type errors must not leak to the wire.
            assert b"object has no attribute" not in resp

        # Still healthy afterwards.
        resp = await roundtrip(b'{"op":"ping"}\n')
        assert json.loads(resp.decode())["ok"] is True
    finally:
        daemon._stop.set()
        await asyncio.wait_for(task, timeout=5)


@pytest.mark.asyncio
async def test_control_socket_is_owner_only(tmp_path: Path) -> None:
    daemon = _daemon(tmp_path)
    task = asyncio.create_task(daemon.start())
    try:
        for _ in range(100):
            if daemon.paths.socket_path.exists():
                break
            await asyncio.sleep(0.02)
        mode = daemon.paths.socket_path.stat().st_mode & 0o777
        assert mode == 0o600, oct(mode)
    finally:
        daemon._stop.set()
        await asyncio.wait_for(task, timeout=5)
