"""Remote MCP connectors and private URL edge cases."""

from __future__ import annotations

from aegis.config import build_config
from aegis.mcp.remote_spec import _is_private_url, build_remote_mcp_tools


def test_connectors(monkeypatch) -> None:
    monkeypatch.setenv("MCP_CONNECTOR_TOKEN", "tok")
    cfg = build_config(
        {
            "mcp": {
                "connectors": {
                    "items": [
                        {
                            "label": "gcal",
                            "connector_id": "connector_googlecalendar",
                            "require_approval": "always",
                            "allowed_tools": ["search_events"],
                            "authorization": "env:MCP_CONNECTOR_TOKEN",
                        }
                    ]
                }
            }
        }
    )
    tools = build_remote_mcp_tools(cfg)
    assert len(tools) == 1
    assert tools[0]["connector_id"] == "connector_googlecalendar"
    assert tools[0]["authorization"] == "tok"


def test_private_url_helpers() -> None:
    assert _is_private_url("http://localhost:8080/mcp")
    assert _is_private_url("http://10.0.0.5/mcp")
    assert _is_private_url("http://192.168.1.1/mcp")
    assert _is_private_url("http://172.16.0.1/mcp")
    assert not _is_private_url("https://developers.openai.com/mcp")


def test_remote_with_headers(monkeypatch) -> None:
    monkeypatch.setenv("MCP_TEST_HEADER", "1")
    cfg = build_config(
        {
            "mcp": {
                "remote": {
                    "servers": [
                        {
                            "label": "docs",
                            "server_url": "https://example.com/mcp",
                            "headers": {"X-Test": "env:MCP_TEST_HEADER"},
                            "require_approval": "always",
                        }
                    ]
                }
            }
        }
    )
    tools = build_remote_mcp_tools(cfg)
    assert tools[0]["headers"]["X-Test"] == "1"
