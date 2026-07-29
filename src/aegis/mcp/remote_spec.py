"""Build Realtime session MCP tool entries for remote servers / connectors."""

from __future__ import annotations

from typing import Any

from aegis.config.paths import default_paths
from aegis.config.schema import AegisConfig, McpApproval
from aegis.util.logging import get_logger
from aegis.util.net import is_private_url as _is_private_url
from aegis.util.secrets import resolve_api_key

log = get_logger("mcp.remote")


def build_remote_mcp_tools(
    cfg: AegisConfig,
    *,
    audit: Any | None = None,
    session_id: str | None = None,
) -> list[dict[str, Any]]:
    """Return OpenAI Realtime `tools` entries of type mcp.

    ``audit`` records the high-severity ``remote_mcp.private_url_enabled``
    event the design requires whenever a private/loopback tool surface is
    exposed to the cloud provider.
    """
    out: list[dict[str, Any]] = []
    for server in cfg.mcp.remote.servers:
        private = _is_private_url(server.server_url)
        if private and not server.allow_private_server_url:
            log.warning(
                "skipping private MCP url for %s (set allow_private_server_url)",
                server.label,
            )
            continue
        if private:
            log.warning(
                "exposing private MCP url for %s to the model provider",
                server.label,
            )
            if audit is not None:
                audit.log(
                    "remote_mcp.private_url_enabled",
                    session_id=session_id,
                    tool_name=server.label,
                    decision="allow",
                    risk="network",
                    server_label=server.label,
                )
        # Fail closed. An empty allowed_tools used to mean "expose every tool
        # this server offers" — the same fail-open shape as an empty kubectl
        # context allowlist. Remote MCP output is executed provider-side and
        # never passes through our sanitizer, so the tool allowlist is the only
        # blast-radius control we actually hold.
        if not server.allowed_tools:
            log.error(
                "skipping remote MCP %s: allowed_tools is empty; list the tools "
                "this server may expose",
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
            "allowed_tools": server.allowed_tools,
        }
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
        if not item.allowed_tools:
            log.error(
                "skipping MCP connector %s: allowed_tools is empty; list the "
                "tools this connector may expose",
                item.label,
            )
            continue
        entry = {
            "type": "mcp",
            "server_label": item.label,
            "connector_id": item.connector_id,
            "require_approval": item.require_approval.value
            if isinstance(item.require_approval, McpApproval)
            else str(item.require_approval),
            "allowed_tools": item.allowed_tools,
        }
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
