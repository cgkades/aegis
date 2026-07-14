"""Tool loop with mock voice session."""

from __future__ import annotations

from pathlib import Path

import pytest

from aegis.config import build_config
from aegis.session.events import SessionState, Trigger
from aegis.session.machine import SessionMachine
from aegis.session.tool_loop import handle_tool_call
from aegis.tools.factory import build_registry
from aegis.voice.mock import MockVoiceSession
from aegis.voice.protocol import ToolCallRequest


@pytest.mark.asyncio
async def test_tool_loop_list_dir(tmp_path: Path) -> None:
    (tmp_path / "x.txt").write_text("1", encoding="utf-8")
    cfg = build_config(
        {
            "profile": {"name": "mvp"},
            "tools": {"working_directory": str(tmp_path), "sandbox_to_workdir": True},
        }
    )
    reg = build_registry(cfg)
    machine = SessionMachine()
    machine.trigger(Trigger.CLI_START)
    machine.trigger(Trigger.CAPTURE_READY)
    machine.trigger(Trigger.SESSION_READY)
    assert machine.state is SessionState.ACTIVE

    session = MockVoiceSession(auto_end=False)
    await session.connect(cfg.session)

    result = await handle_tool_call(
        ToolCallRequest(
            call_id="c1",
            name="list_dir",
            arguments={"path": str(tmp_path)},
        ),
        session=session,
        registry=reg,
        machine=machine,
        cfg=cfg,
        interactive_approval=False,
    )
    assert not result.is_error
    assert "x.txt" in result.output
    assert machine.state is SessionState.ACTIVE
