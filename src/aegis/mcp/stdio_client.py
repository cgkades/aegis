"""Minimal MCP stdio client (JSON-RPC over stdin/stdout of child process)."""

from __future__ import annotations

import asyncio
import contextlib
import json
from dataclasses import dataclass, field
from typing import Any

from aegis.tools.policy import scrubbed_env
from aegis.util.logging import get_logger

log = get_logger("mcp.stdio")


@dataclass
class McpToolInfo:
    name: str
    description: str = ""
    input_schema: dict[str, Any] = field(default_factory=dict)


class McpStdioClient:
    """Speak MCP over a subprocess stdio transport (subset for tools/list + tools/call)."""

    def __init__(
        self,
        command: str,
        args: list[str] | None = None,
        *,
        env: dict[str, str] | None = None,
        cwd: str | None = None,
        name: str = "mcp",
    ) -> None:
        self.command = command
        self.args = args or []
        self.env = env
        self.cwd = cwd
        self.name = name
        self._proc: asyncio.subprocess.Process | None = None
        self._id = 0
        self._pending: dict[int, asyncio.Future[Any]] = {}
        self._reader_task: asyncio.Task[None] | None = None
        self._tools: list[McpToolInfo] = []

    @property
    def tools(self) -> list[McpToolInfo]:
        return list(self._tools)

    async def start(self) -> None:
        env = scrubbed_env()
        if self.env:
            env.update(self.env)
        self._proc = await asyncio.create_subprocess_exec(
            self.command,
            *self.args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            cwd=self.cwd,
            env=env,
        )
        self._reader_task = asyncio.create_task(self._read_loop(), name=f"mcp-read-{self.name}")
        await self._initialize()
        await self._list_tools()

    async def close(self) -> None:
        if self._reader_task:
            self._reader_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reader_task
        self._fail_pending("client closed")
        if self._proc:
            try:
                self._proc.terminate()
                await asyncio.wait_for(self._proc.wait(), timeout=3)
            except Exception:
                try:
                    self._proc.kill()
                except Exception:
                    pass
            self._proc = None

    def _fail_pending(self, reason: str) -> None:
        """Fail any in-flight requests so callers don't hang until the 60s timeout."""
        pending = self._pending
        self._pending = {}
        for fut in pending.values():
            if not fut.done():
                fut.set_exception(ConnectionError(f"mcp {self.name}: {reason}"))

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        return await self._request(
            "tools/call",
            {"name": name, "arguments": arguments},
        )

    async def _initialize(self) -> None:
        await self._request(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "aegis", "version": "0.1.0"},
            },
        )
        await self._notify("notifications/initialized", {})

    async def _list_tools(self) -> None:
        result = await self._request("tools/list", {})
        tools = []
        for t in (result or {}).get("tools") or []:
            tools.append(
                McpToolInfo(
                    name=str(t.get("name", "")),
                    description=str(t.get("description") or ""),
                    input_schema=t.get("inputSchema") or t.get("input_schema") or {},
                )
            )
        self._tools = [t for t in tools if t.name]
        log.info("mcp %s tools: %s", self.name, [t.name for t in self._tools])

    async def _request(self, method: str, params: dict[str, Any]) -> Any:
        self._id += 1
        req_id = self._id
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[Any] = loop.create_future()
        self._pending[req_id] = fut
        msg = {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}
        await self._write(msg)
        return await asyncio.wait_for(fut, timeout=60)

    async def _notify(self, method: str, params: dict[str, Any]) -> None:
        await self._write({"jsonrpc": "2.0", "method": method, "params": params})

    async def _write(self, msg: dict[str, Any]) -> None:
        if self._proc is None or self._proc.stdin is None:
            raise RuntimeError("mcp client not started")
        data = (json.dumps(msg) + "\n").encode("utf-8")
        self._proc.stdin.write(data)
        await self._proc.stdin.drain()

    async def _read_loop(self) -> None:
        if self._proc is None or self._proc.stdout is None:
            raise RuntimeError("mcp client not started")
        stdout = self._proc.stdout
        try:
            while True:
                line = await stdout.readline()
                if not line:
                    break
                try:
                    msg = json.loads(line.decode("utf-8"))
                except json.JSONDecodeError:
                    continue
                if "id" in msg and ("result" in msg or "error" in msg):
                    fut = self._pending.pop(int(msg["id"]), None)
                    if fut and not fut.done():
                        if "error" in msg:
                            fut.set_exception(RuntimeError(str(msg["error"])))
                        else:
                            fut.set_result(msg.get("result"))
        finally:
            # Server died / stream closed — don't leave requests hanging to timeout.
            self._fail_pending("server closed the connection")
