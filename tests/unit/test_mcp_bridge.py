"""MCP stdio client and bridge tests with fake subprocess protocol."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aegis.config import build_config
from aegis.mcp.bridge import LocalMcpBridge, _format_mcp_result, _resolve_server_env, _safe
from aegis.mcp.stdio_client import McpStdioClient, McpToolInfo
from aegis.tools.registry import ToolRegistry


@pytest.mark.asyncio
async def test_stdio_client_initialize_list_call(monkeypatch: pytest.MonkeyPatch) -> None:
    """Drive McpStdioClient with a scripted process pair of pipes."""

    class FakeProc:
        def __init__(self) -> None:
            self.stdin = FakeStdin(self)
            self.stdout = FakeStdout(self)
            self.stderr = asyncio.StreamReader()
            self._queue: asyncio.Queue[bytes] = asyncio.Queue()
            self.returncode = None

        def terminate(self) -> None:
            pass

        def kill(self) -> None:
            pass

        async def wait(self) -> int:
            return 0

    class FakeStdin:
        def __init__(self, proc: FakeProc) -> None:
            self.proc = proc

        def write(self, data: bytes) -> None:
            msg = json.loads(data.decode())
            mid = msg.get("id")
            method = msg.get("method")
            if method == "initialize" and mid is not None:
                asyncio.get_event_loop().call_soon(
                    lambda: self.proc._queue.put_nowait(
                        (json.dumps({"jsonrpc": "2.0", "id": mid, "result": {}}) + "\n").encode()
                    )
                )
            elif method == "tools/list" and mid is not None:
                result = {
                    "tools": [
                        {
                            "name": "echo",
                            "description": "Echo",
                            "inputSchema": {
                                "type": "object",
                                "properties": {"text": {"type": "string"}},
                            },
                        }
                    ]
                }
                asyncio.get_event_loop().call_soon(
                    lambda: self.proc._queue.put_nowait(
                        (
                            json.dumps({"jsonrpc": "2.0", "id": mid, "result": result}) + "\n"
                        ).encode()
                    )
                )
            elif method == "tools/call" and mid is not None:
                text = msg["params"]["arguments"].get("text", "")
                result = {"content": [{"type": "text", "text": text}]}
                asyncio.get_event_loop().call_soon(
                    lambda: self.proc._queue.put_nowait(
                        (
                            json.dumps({"jsonrpc": "2.0", "id": mid, "result": result}) + "\n"
                        ).encode()
                    )
                )
            # notifications have no id

        async def drain(self) -> None:
            return None

    class FakeStdout:
        def __init__(self, proc: FakeProc) -> None:
            self.proc = proc

        async def readline(self) -> bytes:
            try:
                return await asyncio.wait_for(self.proc._queue.get(), timeout=2)
            except TimeoutError:
                return b""

    fake = FakeProc()
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-reach-mcp")
    captured_env: dict[str, str] = {}

    async def fake_exec(*args, **kwargs):
        captured_env.update(kwargs["env"])
        return fake

    client = McpStdioClient("fake-mcp", name="test")
    with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
        await client.start()
        assert any(t.name == "echo" for t in client.tools)
        result = await client.call_tool("echo", {"text": "hi"})
        assert result["content"][0]["text"] == "hi"
        await client.close()
    assert "OPENAI_API_KEY" not in captured_env


def test_format_mcp_result() -> None:
    assert _format_mcp_result(None) == "(empty)"
    assert _format_mcp_result("x") == "x"
    assert "hello" in _format_mcp_result(
        {"content": [{"type": "text", "text": "hello"}]}
    )
    assert "a" in _format_mcp_result({"a": 1})


def test_safe_name() -> None:
    assert _safe("my server!") == "my_server_"


def test_resolve_server_env_keeps_static_values_and_expands_references(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MCP_TEST_TOKEN", "secret")
    resolved = _resolve_server_env({"MODE": "test", "API_TOKEN": "env:MCP_TEST_TOKEN"})
    assert resolved == {"MODE": "test", "API_TOKEN": "secret"}


@pytest.mark.asyncio
async def test_bridge_registers_tools() -> None:
    cfg = build_config(
        {
            "mcp": {
                "local": {
                    "servers": [
                        {"name": "demo", "command": "true", "args": []}
                    ]
                }
            }
        }
    )
    reg = ToolRegistry(cfg.tools)
    bridge = LocalMcpBridge(cfg, reg)

    fake_client = MagicMock()
    fake_client.tools = [
        McpToolInfo(
            name="ping",
            description="Ping",
            input_schema={"type": "object", "properties": {}},
        )
    ]
    fake_client.start = AsyncMock()
    fake_client.close = AsyncMock()
    fake_client.call_tool = AsyncMock(
        return_value={"content": [{"type": "text", "text": "pong"}]}
    )

    with patch("aegis.mcp.bridge.McpStdioClient", return_value=fake_client):
        names = await bridge.start()
    assert names
    assert names[0].startswith("mcp_demo_")
    assert names[0] in reg.names()

    # dispatch without approval → prompt
    result = await reg.dispatch(names[0], {})
    assert result.meta.get("needs_approval") or result.decision == "prompt"

    result2 = await reg.dispatch(names[0], {}, approved=True)
    assert not result2.is_error
    assert "pong" in result2.output

    await bridge.close()
