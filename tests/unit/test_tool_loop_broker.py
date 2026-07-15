"""Tool loop with ApprovalBroker (daemon-style)."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from aegis.approval.broker import ApprovalBroker
from aegis.config import build_config
from aegis.session.events import Trigger
from aegis.session.machine import SessionMachine
from aegis.session.tool_loop import handle_tool_call
from aegis.tools.factory import build_registry
from aegis.voice.mock import MockVoiceSession
from aegis.voice.protocol import ToolCallRequest


@pytest.mark.asyncio
async def test_tool_loop_uses_approval_handler(tmp_path: Path) -> None:
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

    broker = ApprovalBroker(timeout_s=5)

    async def allow() -> None:
        await asyncio.sleep(0.05)
        pending = broker.list_pending()
        assert pending
        broker.respond(pending[0]["call_id"], allowed=True)

    task = asyncio.create_task(allow())
    result = await handle_tool_call(
        ToolCallRequest(
            call_id="broker-1",
            name="read_file",
            arguments={"path": str(env)},
        ),
        session=session,
        registry=reg,
        machine=machine,
        cfg=cfg,
        interactive_approval=False,
        approval_handler=broker.request,
    )
    await task
    assert not result.is_error
    assert "SECRET=1" in result.output
