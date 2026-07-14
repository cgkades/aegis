"""Remote MCP tool injection tests."""

from __future__ import annotations

import pytest

from aegis.config import ConfigError, build_config
from aegis.mcp.remote_spec import build_remote_mcp_tools


def test_remote_mcp_builds() -> None:
    cfg = build_config(
        {
            "mcp": {
                "remote": {
                    "servers": [
                        {
                            "label": "docs",
                            "server_url": "https://developers.openai.com/mcp",
                            "allowed_tools": ["search"],
                            "require_approval": "always",
                        }
                    ]
                }
            }
        }
    )
    tools = build_remote_mcp_tools(cfg)
    assert len(tools) == 1
    assert tools[0]["type"] == "mcp"
    assert tools[0]["server_label"] == "docs"


def test_private_url_rejected_at_config() -> None:
    with pytest.raises(ConfigError, match="private/local URL"):
        build_config(
            {
                "mcp": {
                    "remote": {
                        "servers": [
                            {
                                "label": "local",
                                "server_url": "http://127.0.0.1:9/mcp",
                                "allow_private_server_url": False,
                            }
                        ]
                    }
                }
            }
        )


def test_private_url_allowed_with_flag() -> None:
    cfg = build_config(
        {
            "mcp": {
                "remote": {
                    "servers": [
                        {
                            "label": "local",
                            "server_url": "http://127.0.0.1:9/mcp",
                            "allow_private_server_url": True,
                        }
                    ]
                }
            }
        }
    )
    tools = build_remote_mcp_tools(cfg)
    assert len(tools) == 1
