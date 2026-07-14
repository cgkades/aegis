"""Build Realtime session MCP tool entries for remote servers / connectors."""

from __future__ import annotations

from typing import Any

from aegis.config.schema import AegisConfig, McpApproval
from aegis.util.logging import get_logger
from aegis.util.net import is_private_url as _is_private_url

log = get_logger("mcp.remote")


def build_remote_mcp_tools(cfg: AegisConfig) -> list[dict[str, Any]]:
    """Return OpenAI Realtime `tools` entries of type mcp."""
    out: list[dict[str, Any]] = []
    for server in cfg.mcp.remote.servers:
        if _is_private_url(server.server_url) and not server.allow_private_server_url:
            log.warning(
                "skipping private MCP url for %s (set allow_private_server_url)",
                server.label,
            )
            continue
        entry: dict[str, Any] = {
            "type": "mcp",
            "server_label": server.label,
            "server_url": server.server_url,
            "require_approval": server.require_approval.value
            if isinstance(server.require_approval, McpApproval)
            else str(server.require_approval),
        }
        if server.allowed_tools:
            entry["allowed_tools"] = server.allowed_tools
        if server.authorization:
            entry["authorization"] = server.authorization
        if server.headers:
            entry["headers"] = server.headers
        out.append(entry)

    for item in cfg.mcp.connectors.items:
        entry = {
            "type": "mcp",
            "server_label": item.label,
            "connector_id": item.connector_id,
            "require_approval": item.require_approval.value
            if isinstance(item.require_approval, McpApproval)
            else str(item.require_approval),
        }
        if item.allowed_tools:
            entry["allowed_tools"] = item.allowed_tools
        if item.authorization:
            entry["authorization"] = item.authorization
        out.append(entry)

    return out
