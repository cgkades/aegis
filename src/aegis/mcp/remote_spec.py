"""Build Realtime session MCP tool entries for remote servers / connectors."""

from __future__ import annotations

from typing import Any

from aegis.config.paths import default_paths
from aegis.config.schema import AegisConfig, McpApproval
from aegis.util.logging import get_logger
from aegis.util.net import is_private_url as _is_private_url
from aegis.util.secrets import resolve_api_key

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
        try:
            if server.authorization:
                entry["authorization"] = _resolve_secret_reference(server.authorization)
            if server.headers:
                entry["headers"] = {
                    name: _resolve_secret_reference(reference)
                    for name, reference in server.headers.items()
                }
        except RuntimeError as exc:
            log.warning("skipping remote MCP %s: %s", server.label, exc)
            continue
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
        try:
            if item.authorization:
                entry["authorization"] = _resolve_secret_reference(item.authorization)
        except RuntimeError as exc:
            log.warning("skipping MCP connector %s: %s", item.label, exc)
            continue
        out.append(entry)

    return out


def _resolve_secret_reference(reference: str) -> str:
    env_var = reference.removeprefix("env:")
    value = resolve_api_key(env_var=env_var, secrets_file=default_paths().secrets_env)
    if not value:
        raise RuntimeError(f"secret reference {reference!r} is not set")
    return value
