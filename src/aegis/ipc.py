"""Unix socket IPC protocol for daemon control."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
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
    params = data.get("params") if isinstance(data.get("params"), dict) else {}
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


def remove_stale_socket(path: Path) -> None:
    if path.exists():
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
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False
