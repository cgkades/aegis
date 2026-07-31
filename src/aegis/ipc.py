"""Unix socket IPC protocol for daemon control."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import socket
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aegis.util.logging import get_logger

log = get_logger("ipc")

# Newline-delimited JSON messages.


@dataclass(slots=True)
class IpcRequest:
    op: str
    id: str = "1"
    params: dict[str, Any] | None = None


@dataclass(slots=True)
class IpcResponse:
    id: str
    ok: bool
    result: dict[str, Any] | None = None
    error: str | None = None

    def to_line(self) -> str:
        payload: dict[str, Any] = {"id": self.id, "ok": self.ok}
        if self.result is not None:
            payload["result"] = self.result
        if self.error is not None:
            payload["error"] = self.error
        return json.dumps(payload) + "\n"


def parse_request(line: str) -> IpcRequest:
    data = json.loads(line)
    if not isinstance(data, dict):
        # Valid JSON of the wrong shape used to surface as an AttributeError
        # ("'str' object has no attribute 'get'") on the wire.
        raise ValueError("request must be a JSON object")
    raw_params = data.get("params")
    params: dict[str, Any] = raw_params if isinstance(raw_params, dict) else {}
    # Early approval clients sent their fields at the top level. Preserve that
    # wire form while normalizing all handlers onto ``params``.
    for key in ("call_id", "allow", "allowed", "scope", "grant_scope", "reason"):
        if key in data and key not in params:
            params[key] = data[key]
    return IpcRequest(
        op=str(data.get("op", "")),
        id=str(data.get("id", "1")),
        params=params,
    )


async def send_request(
    socket_path: Path,
    op: str,
    params: dict[str, Any] | None = None,
    *,
    timeout: float = 5.0,
) -> IpcResponse:
    """Client helper: connect, send one request, read one response."""
    reader, writer = await asyncio.wait_for(
        asyncio.open_unix_connection(path=str(socket_path)),
        timeout=timeout,
    )
    try:
        req = {"op": op, "id": "1", "params": params or {}}
        writer.write((json.dumps(req) + "\n").encode("utf-8"))
        await writer.drain()
        line = await asyncio.wait_for(reader.readline(), timeout=timeout)
        if not line:
            return IpcResponse(id="1", ok=False, error="empty_response")
        data = json.loads(line.decode("utf-8"))
        return IpcResponse(
            id=str(data.get("id", "1")),
            ok=bool(data.get("ok")),
            result=data.get("result") if isinstance(data.get("result"), dict) else None,
            error=data.get("error"),
        )
    finally:
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()


# sockaddr_un.sun_path is 108 bytes on Linux including the NUL terminator.
# Binding a longer path fails with a bare OSError from deep inside asyncio.
_SUN_PATH_MAX = 107


def check_socket_path(path: Path) -> str | None:
    """Return an actionable error if ``path`` cannot be used for an AF_UNIX socket."""
    encoded = len(str(path).encode("utf-8"))
    if encoded > _SUN_PATH_MAX:
        return (
            f"control socket path is {encoded} bytes, over the {_SUN_PATH_MAX}-byte "
            f"AF_UNIX limit: {path}. Set XDG_STATE_HOME to a shorter directory."
        )
    return None


def socket_is_live(path: Path, *, timeout: float = 0.5) -> bool:
    """Whether something is already accepting connections on ``path``.

    Used before reclaiming a socket file: unlinking unconditionally would steal
    the control channel from a running daemon, leaving it holding the
    microphone with no way to reach it.
    """
    if not path.exists():
        return False
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect(str(path))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def remove_stale_socket(path: Path) -> None:
    """Remove a socket file left by a dead daemon.

    Refuses to remove one that is still being served — see :func:`socket_is_live`.
    """
    if not path.exists():
        return
    if socket_is_live(path):
        raise OSError(f"control socket {path} is already served by a live daemon")
    try:
        path.unlink()
    except OSError as exc:
        log.warning("could not remove stale socket %s: %s", path, exc)


def write_pid(path: Path, pid: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(pid or os.getpid()), encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass


def read_pid(path: Path) -> int | None:
    if not path.is_file():
        return None
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except (ValueError, OSError):
        return None


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # The pid exists, it just belongs to another user. Treating it as dead
        # would let a second daemon start and steal the control socket.
        return True
    except OSError:
        return False
    return True
