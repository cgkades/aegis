"""The settings surface must come from one table, not four parallel copies."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from aegis.config import build_config
from aegis.config.save import (
    SETTING_NAMES,
    SETTINGS,
    apply_llm_settings,
    apply_settings,
    config_to_toml,
    save_config,
    settings_payload,
)
from aegis.ui.settings_server import _settings_dict

_PAGE = Path(__file__).parents[2] / "src" / "aegis" / "ui" / "settings_page.html"


def test_get_payload_keys_are_exactly_the_table() -> None:
    payload = settings_payload(build_config({}))
    assert tuple(payload) == SETTING_NAMES


def test_settings_server_get_uses_the_shared_table() -> None:
    cfg = build_config({})
    assert _settings_dict(cfg) == settings_payload(cfg)


def test_every_setting_has_an_input_on_the_page() -> None:
    """Catches a field added to the table but never surfaced in the UI."""
    html = _PAGE.read_text(encoding="utf-8")
    ids = set(re.findall(r'id="([a-z0-9_]+)"', html))
    missing = [name for name in SETTING_NAMES if name not in ids]
    assert not missing, f"settings with no form control: {missing}"


def test_page_declares_the_field_list_from_the_server() -> None:
    html = _PAGE.read_text(encoding="utf-8")
    assert "__AEGIS_SETTING_NAMES__" in html, "page must take the list from the server"
    # And it must not have grown a hand-written copy again.
    assert "bedrock_endpoint_url:" not in html


def test_served_page_substitutes_the_field_list(tmp_path, monkeypatch) -> None:
    from aegis.ui import settings_server as ss

    html = (
        _PAGE.read_text(encoding="utf-8")
        .replace("__AEGIS_CSRF_TOKEN__", "tok")
        .replace("__AEGIS_SETTING_NAMES__", json.dumps(list(ss.SETTING_NAMES)))
    )
    assert "__AEGIS_SETTING_NAMES__" not in html
    match = re.search(r"const SETTING_NAMES = (\[[^\]]*\]);", html)
    assert match, "SETTING_NAMES declaration not found in served page"
    assert json.loads(match.group(1)) == list(SETTING_NAMES)


def test_payload_round_trips_through_apply() -> None:
    """Saving what the UI was shown must not change anything.

    The first apply normalizes path-valued fields (``~`` expands on
    validation), which predates the table; what matters is that no value is
    lost or drifts on any subsequent save.
    """
    cfg = apply_settings(build_config({}), {})
    payload = settings_payload(cfg)
    reapplied = settings_payload(apply_settings(cfg, payload))
    assert reapplied == payload


def test_first_apply_only_normalizes_paths() -> None:
    cfg = build_config({})
    before = settings_payload(cfg)
    after = settings_payload(apply_settings(cfg, before))
    changed = {k for k in before if before[k] != after[k]}
    assert changed <= {"chatgpt_token_path"}, changed


def test_every_setting_actually_writes_somewhere() -> None:
    """A spec whose target path is wrong would silently drop the value."""
    cfg = build_config({})
    baseline = settings_payload(cfg)
    for spec in SETTINGS:
        current = baseline[spec.name]
        if spec.coerce is int:
            new: object = int(current or 0) + 7
        elif spec.coerce is float:
            new = float(current or 0) + 0.25
        elif spec.name in {"provider", "voice", "log_level", "profile"}:
            continue  # constrained enums; covered by the round-trip test
        elif spec.name in {"reasoning_effort", "azure_api_style", "azure_auth_mode"}:
            continue  # literal unions
        else:
            new = f"probe-{spec.name}"
        updated = apply_settings(cfg, {spec.name: new})
        assert settings_payload(updated)[spec.name] == new, spec.name


def test_mirrored_settings_update_both_locations() -> None:
    cfg = apply_settings(
        build_config({}),
        {
            "api_key_env": "MY_KEY",
            "realtime_url": "wss://example/rt",
            "openai_chat_base_url": "https://example/v1",
            "provider": "ollama",
        },
    )
    assert cfg.openai.api_key_env == "MY_KEY"
    assert cfg.llm.openai.api_key_env == "MY_KEY"
    assert cfg.openai.realtime_url == "wss://example/rt"
    assert cfg.llm.openai.realtime_url == "wss://example/rt"
    assert cfg.openai.chat_base_url == "https://example/v1"
    assert cfg.llm.openai.chat_base_url == "https://example/v1"
    assert cfg.llm.chat_provider.value == "ollama"


def test_deployment_and_model_id_alias_session_model() -> None:
    cfg = build_config({})
    azure = apply_settings(cfg, {"azure_deployment": "gpt4o-deploy"})
    assert azure.session.model == "gpt4o-deploy"

    bedrock = apply_settings(cfg, {"bedrock_model_id": "anthropic.claude"})
    assert bedrock.session.model == "anthropic.claude"

    # An explicit model always wins over the aliases.
    explicit = apply_settings(
        cfg, {"model": "chosen", "azure_deployment": "d", "bedrock_model_id": "m"}
    )
    assert explicit.session.model == "chosen"


def test_absent_keys_leave_values_untouched() -> None:
    cfg = apply_settings(build_config({}), {"voice": "coral"})
    updated = apply_settings(cfg, {"model": "gpt-x"})
    assert updated.session.voice == "coral"
    assert updated.session.model == "gpt-x"


def test_unknown_keys_are_ignored_not_fatal(caplog) -> None:
    cfg = build_config({})
    updated = apply_settings(cfg, {"model": "gpt-x", "not_a_setting": "boom"})
    assert updated.session.model == "gpt-x"


def test_apply_llm_settings_wrapper_still_works() -> None:
    """Public keyword API used by the CLI and older callers."""
    cfg = apply_llm_settings(build_config({}), voice="coral", max_tokens=999)
    assert cfg.session.voice == "coral"
    assert cfg.llm.max_tokens == 999


def test_save_round_trip_preserves_nested_tables(tmp_path: Path) -> None:
    """The bug this refactor guards against: silently wiping arrays-of-tables."""
    cfg = build_config(
        {
            "mcp": {
                "local": {
                    "servers": [
                        {"name": "files", "command": "mcp-files", "args": ["--root", "/tmp"]}
                    ]
                }
            },
            "tools": {"shell": {"enabled": True}},
        }
    )
    updated = apply_settings(cfg, {"voice": "coral"})
    target = tmp_path / "config.toml"
    save_config(updated, target)

    text = target.read_text(encoding="utf-8")
    assert "mcp-files" in text
    assert "[[mcp.local.servers]]" in text
    assert config_to_toml(updated) == text


@pytest.mark.parametrize("name", SETTING_NAMES)
def test_setting_names_are_stable_identifiers(name: str) -> None:
    """They are wire keys and DOM ids; keep them boring."""
    assert re.fullmatch(r"[a-z][a-z0-9_]*", name), name
