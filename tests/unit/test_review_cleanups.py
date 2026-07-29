"""Smaller review follow-ups: provider capabilities, contracts, validators."""

from __future__ import annotations

from pathlib import Path

import pytest

from aegis.config import build_config
from aegis.config.save import SETTING_NAMES
from aegis.llm.chat_session import ChatLLMSession
from aegis.tools.builtin.shell_tools import shell_tool_specs, validate_run_command_args
from aegis.voice.capabilities import (
    TEXT_ONLY_PROVIDERS,
    UNIMPLEMENTED_PROVIDERS,
    Transport,
    is_text_only,
    is_unimplemented,
    is_voice_capable,
    normalize_provider,
    transport_for,
)
from aegis.voice.factory import create_voice_session
from aegis.voice.mock import MockVoiceSession
from aegis.voice.realtime import RealtimeVoiceSession

# --------------------------------------------------------------------------- #
# Provider capabilities: one table, not three parallel string sets
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Azure-OpenAI", "azure_openai"),
        ("  REALTIME ", "realtime"),
        ("gpt-live", "gpt_live"),
    ],
)
def test_normalize_provider(raw: str, expected: str) -> None:
    assert normalize_provider(raw) == expected


def test_transport_classes_are_disjoint() -> None:
    assert not (TEXT_ONLY_PROVIDERS & UNIMPLEMENTED_PROVIDERS)


def test_realtime_and_mock_are_the_voice_backends() -> None:
    assert is_voice_capable("realtime")
    assert is_voice_capable("mock")
    assert not is_text_only("realtime")


@pytest.mark.parametrize("provider", sorted(TEXT_ONLY_PROVIDERS))
def test_factory_routes_every_text_provider_to_the_chat_session(provider: str) -> None:
    """The gate and the factory must agree — drift here opens a useless session."""
    from aegis.llm.chat_session import ChatLLMSession as Chat

    cfg = build_config({})
    session = create_voice_session(cfg, backend=provider)
    assert isinstance(session, Chat), provider


@pytest.mark.parametrize("provider", sorted(UNIMPLEMENTED_PROVIDERS))
def test_unimplemented_providers_are_flagged(provider: str) -> None:
    assert is_unimplemented(provider)
    assert not is_text_only(provider)


def test_voice_providers_get_a_voice_session() -> None:
    cfg = build_config({})
    assert isinstance(create_voice_session(cfg, backend="mock"), MockVoiceSession)
    assert isinstance(
        create_voice_session(cfg, backend="realtime"), RealtimeVoiceSession
    )


def test_unknown_provider_defaults_to_realtime() -> None:
    assert transport_for("something-new") is Transport.VOICE


def test_runner_aliases_point_at_the_shared_table() -> None:
    from aegis.session import runner

    assert runner.TEXT_ONLY_BACKENDS is TEXT_ONLY_PROVIDERS
    assert runner.UNIMPLEMENTED_BACKENDS is UNIMPLEMENTED_PROVIDERS


@pytest.mark.asyncio
async def test_daemon_refuses_stub_providers_instead_of_reporting_started(
    tmp_path: Path,
) -> None:
    from aegis.config.paths import AegisPaths
    from aegis.config.save import save_config
    from aegis.daemon import AegisDaemon

    paths = AegisPaths(
        config_dir=tmp_path / "cfg",
        state_dir=tmp_path / "state",
        data_dir=tmp_path / "data",
        cache_dir=tmp_path / "cache",
    )
    paths.ensure_dirs()
    cfg = build_config({"session": {"provider": "text_fallback"}})
    # _start_session re-reads config from disk, so the file is what counts.
    save_config(cfg, paths.config_file)
    daemon = AegisDaemon(cfg, paths, config_path=paths.config_file)

    result = await daemon._start_session(source="ipc")
    assert result["started"] is False
    assert "provider_not_implemented" in result["reason"]
    assert daemon._session_task is None


@pytest.mark.asyncio
async def test_daemon_refuses_text_only_providers(tmp_path: Path) -> None:
    from aegis.config.paths import AegisPaths
    from aegis.config.save import save_config
    from aegis.daemon import AegisDaemon

    paths = AegisPaths(
        config_dir=tmp_path / "cfg",
        state_dir=tmp_path / "state",
        data_dir=tmp_path / "data",
        cache_dir=tmp_path / "cache",
    )
    paths.ensure_dirs()
    cfg = build_config({"session": {"provider": "ollama"}})
    save_config(cfg, paths.config_file)
    daemon = AegisDaemon(cfg, paths, config_path=paths.config_file)

    result = await daemon._start_session(source="ipc")
    assert result["started"] is False
    assert "text_only_provider" in result["reason"]


# --------------------------------------------------------------------------- #
# VoiceSession error contract
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_chat_session_raises_on_tool_result_when_disconnected() -> None:
    """Silently dropping a tool result leaves the model waiting forever."""
    session = ChatLLMSession(build_config({}), provider="mock")
    with pytest.raises(RuntimeError, match="not connected"):
        await session.send_tool_result("c1", "output")


@pytest.mark.asyncio
async def test_chat_session_send_audio_stays_a_documented_no_op() -> None:
    session = ChatLLMSession(build_config({}), provider="mock")
    assert await session.send_audio(b"\x00\x00") is None


def test_protocol_documents_the_error_and_usage_contract() -> None:
    from aegis.voice.protocol import VoiceSession

    doc = VoiceSession.__doc__ or ""
    assert "RuntimeError" in doc
    assert "delta" in doc.lower()


# --------------------------------------------------------------------------- #
# ToolSpec.validate_args replaces the registry's hardcoded tool name
# --------------------------------------------------------------------------- #


def test_run_command_declares_its_own_shape_validator() -> None:
    spec = shell_tool_specs()[0]
    assert spec.validate_args is validate_run_command_args


@pytest.mark.parametrize(
    "arguments",
    [{"argv": ["ls"], "cwd": "/tmp"}, {}, {"cmd": "ls"}],
)
def test_validate_run_command_rejects_non_argv_shapes(arguments: dict) -> None:
    assert validate_run_command_args(arguments) is not None


def test_validate_run_command_accepts_argv_only() -> None:
    assert validate_run_command_args({"argv": ["ls", "-la"]}) is None


@pytest.mark.asyncio
async def test_registry_applies_the_validator_generically(tmp_path: Path) -> None:
    from aegis.tools.factory import build_registry

    cfg = build_config(
        {
            "tools": {
                "enabled": ["fs", "shell"],
                "working_directory": str(tmp_path),
                "shell": {"enabled": True},
            }
        }
    )
    registry = build_registry(cfg)
    result = await registry.dispatch("run_command", {"argv": ["ls"], "extra": 1})
    assert result.is_error
    assert result.decision == "deny"
    assert "argv_only_schema" in result.output


# --------------------------------------------------------------------------- #
# reasoning_effort is no longer advertised as a working control
# --------------------------------------------------------------------------- #


def test_reasoning_effort_is_not_an_editable_setting() -> None:
    """It is never sent to a provider; a dead control is worse than none."""
    assert "reasoning_effort" not in SETTING_NAMES
    page = (
        Path(__file__).parents[2] / "src" / "aegis" / "ui" / "settings_page.html"
    ).read_text(encoding="utf-8")
    assert 'id="reasoning_effort"' not in page


def test_reasoning_effort_still_validates_in_config() -> None:
    """Existing configs that set it must keep loading."""
    cfg = build_config({"session": {"reasoning_effort": "high"}})
    assert cfg.session.reasoning_effort == "high"
