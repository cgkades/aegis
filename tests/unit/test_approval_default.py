"""tools.approval.default global gate."""

from __future__ import annotations

from pathlib import Path

import pytest

from aegis.config import build_config
from aegis.tools.factory import build_registry


@pytest.mark.asyncio
async def test_deny_all_blocks_auto_read_tools(tmp_path: Path) -> None:
    (tmp_path / "note.txt").write_text("hi", encoding="utf-8")
    cfg = build_config(
        {
            "tools": {
                "working_directory": str(tmp_path),
                "sandbox_to_workdir": True,
                "enabled": ["fs"],
                "approval": {"default": "deny_all"},
            }
        }
    )
    reg = build_registry(cfg)
    result = await reg.dispatch("read_file", {"path": str(tmp_path / "note.txt")})
    assert result.is_error
    assert result.decision == "deny"
    assert "deny_all" in result.output


@pytest.mark.asyncio
async def test_prompt_all_requires_approval_for_reads(tmp_path: Path) -> None:
    (tmp_path / "note.txt").write_text("hi", encoding="utf-8")
    cfg = build_config(
        {
            "tools": {
                "working_directory": str(tmp_path),
                "sandbox_to_workdir": True,
                "enabled": ["fs"],
                "approval": {"default": "prompt_all"},
            }
        }
    )
    reg = build_registry(cfg)
    probe = await reg.dispatch("read_file", {"path": str(tmp_path / "note.txt")})
    assert probe.is_error
    assert probe.decision == "prompt"
    assert probe.meta.get("needs_approval") is True

    allowed = await reg.dispatch(
        "read_file",
        {"path": str(tmp_path / "note.txt")},
        approved=True,
    )
    assert not allowed.is_error
    assert "hi" in allowed.output


@pytest.mark.asyncio
async def test_approval_probe_does_not_double_count_budget(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("S=1", encoding="utf-8")
    cfg = build_config(
        {
            "tools": {
                "working_directory": str(tmp_path),
                "sandbox_to_workdir": True,
                "enabled": ["fs"],
                "max_tool_calls_per_turn": 1,
            }
        }
    )
    reg = build_registry(cfg)
    probe = await reg.dispatch("read_file", {"path": str(tmp_path / ".env")})
    assert probe.meta.get("needs_approval") is True
    # After probe, budget must still allow the approved re-dispatch.
    allowed = await reg.dispatch(
        "read_file",
        {"path": str(tmp_path / ".env")},
        approved=True,
    )
    assert not allowed.is_error
