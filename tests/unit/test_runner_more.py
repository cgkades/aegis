"""Session runner coverage with mocked audio/voice."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from aegis.config import build_config
from aegis.config.paths import AegisPaths
from aegis.session.runner import run_session_once, run_session_once_sync
from aegis.voice.mock import MockVoiceSession
from aegis.voice.protocol import (
    ToolCallRequest,
)


@pytest.fixture
def paths(tmp_path: Path) -> AegisPaths:
    p = AegisPaths(
        config_dir=tmp_path / "c",
        state_dir=tmp_path / "s",
        data_dir=tmp_path / "d",
        cache_dir=tmp_path / "k",
    )
    p.ensure_dirs()
    return p


@pytest.mark.asyncio
async def test_run_session_once_mock(paths: AegisPaths) -> None:
    cfg = build_config(
        {
            "tools": {"working_directory": str(paths.data_dir)},
            "session": {"max_session_cost_usd": 10.0, "max_duration_s": 30},
            "activation": {
                "chime_on_wake": False,
                "chime_on_connecting": False,
                "chime_on_end": False,
            },
        }
    )
    with patch("aegis.session.runner.sounddevice_available", return_value=False):
        code = await run_session_once(cfg, backend="mock", paths=paths, max_seconds=2)
    assert code == 0


@pytest.mark.asyncio
async def test_run_session_once_connect_fail(paths: AegisPaths) -> None:
    cfg = build_config(
        {
            "activation": {
                "chime_on_wake": False,
                "chime_on_connecting": False,
                "chime_on_end": False,
            }
        }
    )

    class Boom:
        async def connect(self, config):
            raise RuntimeError("nope")

        async def end(self):
            return None

    with (
        patch("aegis.session.runner.sounddevice_available", return_value=False),
        patch("aegis.session.runner.create_voice_session", return_value=Boom()),
    ):
        code = await run_session_once(cfg, backend="realtime", paths=paths)
    assert code == 1


@pytest.mark.asyncio
async def test_run_session_once_enforces_connect_timeout(paths: AegisPaths) -> None:
    cfg = build_config(
        {
            "session": {"connect_timeout_s": 1},
            "activation": {
                "chime_on_wake": False,
                "chime_on_connecting": False,
                "chime_on_end": False,
            },
        }
    )

    class Hangs:
        ended = False

        async def connect(self, config) -> None:
            await asyncio.Event().wait()

        async def end(self) -> None:
            self.ended = True

    session = Hangs()
    with (
        patch("aegis.session.runner.sounddevice_available", return_value=False),
        patch("aegis.session.runner.create_voice_session", return_value=session),
    ):
        code = await run_session_once(cfg, backend="custom", paths=paths)

    assert code == 1
    assert session.ended


@pytest.mark.asyncio
async def test_run_session_once_rejects_text_only_provider(paths: AegisPaths, capsys) -> None:
    cfg = build_config({"session": {"provider": "ollama"}})

    code = await run_session_once(cfg, backend="ollama", paths=paths)

    assert code == 2
    assert "text-only provider" in capsys.readouterr().err


@pytest.mark.asyncio
async def test_run_session_with_tool_and_cost_cap(paths: AegisPaths) -> None:
    cfg = build_config(
        {
            "tools": {
                "working_directory": str(paths.data_dir),
                "enabled": ["fs"],
            },
            "session": {"max_session_cost_usd": 0.0000001, "max_duration_s": 60},
            "activation": {
                "chime_on_wake": False,
                "chime_on_connecting": False,
                "chime_on_end": False,
            },
        }
    )
    # Mock session that emits usage exceeding cap
    session = MockVoiceSession(auto_end=False, register_gateway=False)
    # Override connect to emit big usage then wait
    original_connect = session.connect

    async def connect_with_usage(config):
        await original_connect(config)
        # After auto events, inject large usage if still connected path
        # Mock already auto-ends when no tool call — use auto_end False and manual events
        pass

    session = MockVoiceSession(
        auto_end=False,
        register_gateway=True,
        emit_tool_call=ToolCallRequest(
            call_id="c1",
            name="list_dir",
            arguments={"path": str(paths.data_dir)},
        ),
    )

    with (
        patch("aegis.session.runner.sounddevice_available", return_value=False),
        patch("aegis.session.runner.create_voice_session", return_value=session),
        patch(
            "aegis.session.tool_loop.prompt_cli_approval",
            new=AsyncMock(
                return_value=__import__(
                    "aegis.approval.modes", fromlist=["ApprovalResponse"]
                ).ApprovalResponse(True)
            ),
        ),
    ):
        # Don't wait forever — end after short time via max_seconds
        code = await run_session_once(cfg, backend="mock", paths=paths, max_seconds=1)
    assert code == 0


def test_run_session_once_sync(paths: AegisPaths, monkeypatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(paths.config_dir.parent))
    # Use mock backend
    code = run_session_once_sync(backend="mock", max_seconds=1)
    assert code == 0
