"""Tool loop approval paths."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from aegis.approval.modes import ApprovalResponse, denial_payload, result_from_denial
from aegis.config import build_config
from aegis.session.events import SessionState, Trigger
from aegis.session.machine import SessionMachine
from aegis.session.tool_loop import _approval_summary, handle_tool_call
from aegis.tools.factory import build_registry
from aegis.voice.mock import MockVoiceSession
from aegis.voice.protocol import ToolCallRequest


def test_approval_summary_keeps_path_when_content_is_long() -> None:
    """Path must remain visible even when content would fill a 300-char dump."""
    path = "/home/user/.config/aegis/config.toml"
    content = "a" * 500 + "\n[tools.shell]\nenabled = true\n"
    summary = _approval_summary({"content": content, "path": path})
    assert path in summary
    assert "path=" in summary
    assert "content=<" in summary
    assert "sha256=" in summary
    # Must not be pure content prefix (old sort_keys JSON truncate bug).
    assert not summary.startswith('{"content"')
    assert not summary.startswith("aaa")


def test_denial_payload() -> None:
    assert "denied" in denial_payload("nope")
    r = result_from_denial("timeout")
    assert r.is_error


@pytest.mark.asyncio
async def test_tool_loop_approval_allow(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("SECRET=1", encoding="utf-8")
    cfg = build_config(
        {
            "tools": {
                "working_directory": str(tmp_path),
                "sandbox_to_workdir": True,
                "enabled": ["fs"],
            }
        }
    )
    reg = build_registry(cfg)
    machine = SessionMachine()
    machine.trigger(Trigger.CLI_START)
    machine.trigger(Trigger.CAPTURE_READY)
    machine.trigger(Trigger.SESSION_READY)

    session = MockVoiceSession(auto_end=False)
    await session.connect(cfg.session)

    with patch(
        "aegis.session.tool_loop.prompt_cli_approval",
        new=AsyncMock(return_value=ApprovalResponse(True, grant_scope="same_tool")),
    ):
        result = await handle_tool_call(
            ToolCallRequest(
                call_id="c1",
                name="read_file",
                arguments={"path": str(env)},
            ),
            session=session,
            registry=reg,
            machine=machine,
            cfg=cfg,
            interactive_approval=True,
        )
    assert not result.is_error
    assert "SECRET=1" in result.output
    assert machine.state is SessionState.ACTIVE


@pytest.mark.asyncio
async def test_session_grant_never_auto_approves_another_secrets_path(tmp_path: Path) -> None:
    first = tmp_path / ".env"
    first.write_text("FIRST=1", encoding="utf-8")
    second = tmp_path / ".ssh" / "id_ed25519"
    second.parent.mkdir()
    second.write_text("SECOND=2", encoding="utf-8")
    cfg = build_config(
        {
            "tools": {
                "working_directory": str(tmp_path),
                "sandbox_to_workdir": True,
                "enabled": ["fs"],
            }
        }
    )
    reg = build_registry(cfg)
    machine = SessionMachine()
    machine.trigger(Trigger.CLI_START)
    machine.trigger(Trigger.CAPTURE_READY)
    machine.trigger(Trigger.SESSION_READY)
    session = MockVoiceSession(auto_end=False)
    await session.connect(cfg.session)

    with patch(
        "aegis.session.tool_loop.prompt_cli_approval",
        new=AsyncMock(return_value=ApprovalResponse(True, grant_scope="same_tool")),
    ):
        result = await handle_tool_call(
            ToolCallRequest(
                call_id="c-secret-1",
                name="read_file",
                arguments={"path": str(first)},
            ),
            session=session,
            registry=reg,
            machine=machine,
            cfg=cfg,
            interactive_approval=True,
        )

    assert not result.is_error
    second_result = await reg.dispatch("read_file", {"path": str(second)})
    assert second_result.is_error
    assert second_result.decision == "prompt"


@pytest.mark.asyncio
async def test_tool_loop_approval_deny(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("SECRET=1", encoding="utf-8")
    cfg = build_config(
        {
            "tools": {
                "working_directory": str(tmp_path),
                "sandbox_to_workdir": True,
                "enabled": ["fs"],
            }
        }
    )
    reg = build_registry(cfg)
    machine = SessionMachine()
    machine.trigger(Trigger.CLI_START)
    machine.trigger(Trigger.CAPTURE_READY)
    machine.trigger(Trigger.SESSION_READY)
    session = MockVoiceSession(auto_end=False)
    await session.connect(cfg.session)

    with patch(
        "aegis.session.tool_loop.prompt_cli_approval",
        new=AsyncMock(return_value=ApprovalResponse(False, reason="user_denied")),
    ):
        result = await handle_tool_call(
            ToolCallRequest(
                call_id="c2",
                name="read_file",
                arguments={"path": str(env)},
            ),
            session=session,
            registry=reg,
            machine=machine,
            cfg=cfg,
            interactive_approval=True,
        )
    assert result.is_error
    assert "denied" in result.output
