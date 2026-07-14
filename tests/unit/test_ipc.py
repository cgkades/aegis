"""IPC helpers tests."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from aegis.ipc import IpcResponse, parse_request, send_request


def test_parse_request() -> None:
    req = parse_request('{"op":"ping","id":"9","params":{"a":1}}')
    assert req.op == "ping"
    assert req.id == "9"
    assert req.params == {"a": 1}


def test_response_line() -> None:
    line = IpcResponse(id="1", ok=True, result={"pong": True}).to_line()
    assert line.endswith("\n")
    assert "pong" in line


@pytest.mark.asyncio
async def test_send_request_roundtrip(tmp_path: Path) -> None:
    sock = tmp_path / "t.sock"

    async def handler(
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        line = await reader.readline()
        req = parse_request(line.decode())
        resp = IpcResponse(id=req.id, ok=True, result={"op": req.op})
        writer.write(resp.to_line().encode())
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_unix_server(handler, path=str(sock))
    try:
        resp = await send_request(sock, "ping")
        assert resp.ok
        assert resp.result == {"op": "ping"}
    finally:
        server.close()
        await server.wait_closed()
