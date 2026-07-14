"""MCP local stdio bridge and remote server specs."""

from aegis.mcp.bridge import LocalMcpBridge
from aegis.mcp.remote_spec import build_remote_mcp_tools

__all__ = ["LocalMcpBridge", "build_remote_mcp_tools"]
