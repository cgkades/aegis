"""Config schema, load, and profile tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from aegis.config import (
    ConfigError,
    build_config,
    default_paths,
    load_config,
    validate_config_file,
)
from aegis.config.load import config_to_display_dict
from aegis.config.paths import AegisPaths
from aegis.config.profiles import deep_merge, profile_overlay
from aegis.config.schema import ProfileName


def test_default_mvp_config() -> None:
    cfg = build_config({})
    assert cfg.profile.name is ProfileName.MVP
    assert cfg.session.model == "gpt-realtime-2.1-mini"
    assert cfg.tools.shell.enabled is False
    assert cfg.tools.kubectl.enabled is False
    assert cfg.tools.enabled == ["fs"]
    assert cfg.wake.enabled is False
    assert cfg.wake.engine.value == "porcupine"
    assert "workspace" in cfg.tools.working_directory
    assert cfg.audio.local_vad_enabled is True


def test_llm_openai_block_is_not_overwritten_by_legacy_defaults() -> None:
    cfg = build_config(
        {"llm": {"openai": {"chat_base_url": "https://example.invalid/v1"}}}
    )
    assert cfg.openai.chat_base_url == "https://example.invalid/v1"
    assert cfg.llm.openai.chat_base_url == "https://example.invalid/v1"


def test_config_display_redacts_mcp_credentials() -> None:
    cfg = build_config(
        {
            "mcp": {
                "remote": {
                    "servers": [
                        {
                            "label": "demo",
                            "server_url": "https://example.com/mcp",
                            "authorization": "env:MCP_AUTHORIZATION",
                            "headers": {"X-API-Key": "env:MCP_API_KEY"},
                        }
                    ]
                }
            }
        }
    )

    displayed = config_to_display_dict(cfg)
    server = displayed["mcp"]["remote"]["servers"][0]
    assert server["authorization"] == "[REDACTED]"
    assert server["headers"] == "[REDACTED]"


def test_mcp_literal_credentials_are_rejected() -> None:
    with pytest.raises(ConfigError, match="env:VARIABLE"):
        build_config(
            {
                "mcp": {
                    "connectors": {
                        "items": [
                            {
                                "label": "demo",
                                "connector_id": "connector_demo",
                                "authorization": "literal-secret",
                            }
                        ]
                    }
                }
            }
        )


def test_mcp_local_literal_secret_env_is_rejected() -> None:
    with pytest.raises(ConfigError, match="env:VARIABLE"):
        build_config(
            {
                "mcp": {
                    "local": {
                        "servers": [
                            {
                                "name": "demo",
                                "command": "demo",
                                "env": {"API_KEY": "literal-secret"},
                            }
                        ]
                    }
                }
            }
        )


def test_oncall_profile_raises_model_and_kubectl() -> None:
    cfg = build_config({"profile": {"name": "oncall"}})
    assert cfg.profile.name is ProfileName.ONCALL
    assert cfg.session.model == "gpt-realtime-2.1"
    assert cfg.tools.kubectl.enabled is True
    assert "kubectl" in cfg.tools.enabled
    assert cfg.tools.shell.enabled is False  # shell still off
    assert cfg.session.max_session_cost_usd == 8.0


def test_user_override_wins_over_profile() -> None:
    cfg = build_config(
        {
            "profile": {"name": "oncall"},
            "session": {"model": "gpt-realtime-2.1-mini", "max_session_cost_usd": 1.0},
        }
    )
    assert cfg.session.model == "gpt-realtime-2.1-mini"
    assert cfg.session.max_session_cost_usd == 1.0
    assert cfg.tools.kubectl.enabled is True  # still from profile


def test_unknown_profile_errors() -> None:
    with pytest.raises(ConfigError, match="unknown profile"):
        build_config({"profile": {"name": "spaceship"}})


def test_load_from_toml_file(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        """
[profile]
name = "standard"

[wake]
phrase = "hey_aegis"
threshold = 0.7

[tools.shell]
enabled = false
""",
        encoding="utf-8",
    )
    cfg = validate_config_file(path)
    assert cfg.profile.name is ProfileName.STANDARD
    assert cfg.tools.git.enabled is True
    assert cfg.wake.threshold == 0.7


def test_missing_config_ok_defaults(tmp_path: Path) -> None:
    paths = AegisPaths(
        config_dir=tmp_path / "cfg",
        state_dir=tmp_path / "state",
        data_dir=tmp_path / "data",
        cache_dir=tmp_path / "cache",
    )
    cfg = load_config(paths=paths, missing_ok=True)
    assert cfg.profile.name is ProfileName.MVP
    assert cfg.tools.working_directory == str(paths.workspace_dir)


def test_legacy_session_grant_scopes_migrate_to_once() -> None:
    for legacy_scope in ("same_risk_class", "all"):
        cfg = build_config(
            {"tools": {"approval": {"session_grant_applies_to": legacy_scope}}}
        )
        assert cfg.tools.approval.session_grant_applies_to.value == "once"


def test_missing_config_not_ok(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="not found"):
        validate_config_file(tmp_path / "nope.toml")


def test_private_mcp_url_rejected_by_default() -> None:
    with pytest.raises(ConfigError, match="private/local URL"):
        build_config(
            {
                "mcp": {
                    "remote": {
                        "servers": [
                            {
                                "label": "local",
                                "server_url": "http://127.0.0.1:8765/mcp",
                            }
                        ]
                    }
                }
            }
        )


def test_private_mcp_url_allowed_with_flag() -> None:
    cfg = build_config(
        {
            "mcp": {
                "remote": {
                    "servers": [
                        {
                            "label": "local",
                            "server_url": "http://127.0.0.1:8765/mcp",
                            "allow_private_server_url": True,
                        }
                    ]
                }
            }
        }
    )
    assert cfg.mcp.remote.servers[0].allow_private_server_url is True


def test_deep_merge() -> None:
    base = {"a": 1, "nested": {"x": 1, "y": 2}}
    over = {"nested": {"y": 9, "z": 3}, "b": 2}
    assert deep_merge(base, over) == {"a": 1, "b": 2, "nested": {"x": 1, "y": 9, "z": 3}}


def test_profile_overlay_mvp_shell_off() -> None:
    overlay = profile_overlay("mvp")
    assert overlay["tools"]["shell"]["enabled"] is False


def test_default_paths_under_home() -> None:
    paths = default_paths()
    assert paths.config_dir.name == "aegis"
    assert paths.config_file.name == "config.toml"
    assert "aegis" in str(paths.socket_path)


def test_path_expand_home() -> None:
    cfg = build_config({"tools": {"working_directory": "~"}})
    assert not cfg.tools.working_directory.startswith("~")


def test_invalid_toml(tmp_path: Path) -> None:
    path = tmp_path / "bad.toml"
    path.write_text("[[[not valid", encoding="utf-8")
    with pytest.raises(ConfigError, match="invalid TOML"):
        validate_config_file(path)


def test_example_config_loads() -> None:
    root = Path(__file__).resolve().parents[2]
    example = root / "configs" / "aegis.example.toml"
    assert example.is_file()
    cfg = validate_config_file(example)
    assert cfg.profile.name is ProfileName.MVP
    assert cfg.tools.shell.enabled is False
