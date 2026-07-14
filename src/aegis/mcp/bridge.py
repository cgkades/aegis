"""Bridge local MCP stdio servers into ToolRegistry as function tools."""

from __future__ import annotations

import json
from typing import Any

from aegis.audit import AuditLogger
from aegis.config.schema import AegisConfig, McpLocalServer
from aegis.mcp.stdio_client import McpStdioClient
from aegis.tools.registry import ToolRegistry
from aegis.tools.types import ToolResult, ToolSpec
from aegis.util.logging import get_logger

log = get_logger("mcp.bridge")


class LocalMcpBridge:
    """Owns stdio MCP clients and registers prefixed tools on a registry."""

    def __init__(
        self,
        cfg: AegisConfig,
        registry: ToolRegistry,
        *,
        audit: AuditLogger | None = None,
    ) -> None:
        self.cfg = cfg
        self.registry = registry
        self.audit = audit
        self._clients: list[McpStdioClient] = []

    async def start(self) -> list[str]:
        registered: list[str] = []
        for server in self.cfg.mcp.local.servers:
            names = await self._start_server(server)
            registered.extend(names)
        return registered

    async def close(self) -> None:
        for client in self._clients:
            await client.close()
        self._clients.clear()

    async def _start_server(self, server: McpLocalServer) -> list[str]:
        client = McpStdioClient(
            server.command,
            server.args,
            env=server.env or None,
            cwd=server.cwd,
            name=server.name,
        )
        try:
            await client.start()
        except Exception as exc:
            log.error("failed to start MCP server %s: %s", server.name, exc)
            return []

        self._clients.append(client)
        names: list[str] = []
        for tool in client.tools:
            # Prefix to avoid collisions: mcp_<server>_<tool>
            safe_server = _safe(server.name)
            safe_tool = _safe(tool.name)
            reg_name = f"mcp_{safe_server}_{safe_tool}"
            spec = ToolSpec(
                name=reg_name,
                description=f"[MCP:{server.name}] {tool.description or tool.name}",
                parameters=tool.input_schema
                or {"type": "object", "properties": {}, "additionalProperties": True},
                risk="exec",  # MCP tools default to prompt via risk!=read under auto_readonly
                handler=_make_handler(client, tool.name, server.name),
                source=f"mcp:{server.name}",
            )
            self.registry.register(spec)
            names.append(reg_name)
        return names


def _make_handler(client: McpStdioClient, tool_name: str, server_name: str):
    async def handler(
        arguments: dict[str, Any],
        *,
        tools=None,
        approved: bool = False,
        spec: ToolSpec | None = None,
    ) -> ToolResult:
        # Non-read MCP defaults to prompt under auto_readonly unless approved
        if not approved:
            return ToolResult(
                output='{"error":"approval_required","reason":"mcp_tool"}',
                is_error=True,
                risk="exec",
                decision="prompt",
                meta={"needs_approval": True, "arguments": arguments},
            )
        try:
            result = await client.call_tool(tool_name, arguments)
            text = _format_mcp_result(result)
            return ToolResult(output=text, risk="exec", decision="auto")
        except Exception as exc:
            return ToolResult(
                output=json.dumps({"error": "mcp_call_failed", "detail": str(exc)}),
                is_error=True,
                risk="exec",
            )

    return handler


_MCP_MAX_BYTES = 100_000


def _cap(text: str, max_bytes: int = _MCP_MAX_BYTES) -> str:
    raw = text.encode("utf-8", errors="replace")
    if len(raw) <= max_bytes:
        return text
    return raw[: max_bytes - 16].decode("utf-8", errors="replace") + "\n…[truncated]"


def _format_mcp_result(result: Any) -> str:
    # Cap every branch: a compromised/hostile MCP server must not be able to flood
    # the model context or exhaust memory (and it feeds prompt-injection defenses).
    if result is None:
        return "(empty)"
    if isinstance(result, str):
        return _cap(result)
    # MCP tools/call often returns {content: [{type:text,text:...}]}
    if isinstance(result, dict):
        content = result.get("content")
        if isinstance(content, list):
            parts = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    parts.append(str(block.get("text", "")))
            if parts:
                return _cap("\n".join(parts))
        return _cap(json.dumps(result, indent=2, default=str))
    return _cap(str(result))


def _safe(name: str) -> str:
    return "".join(c if c.isalnum() or c in "_-" else "_" for c in name)[:48]
