"""Process and log tool tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from aegis.config.schema import ToolsConfig
from aegis.tools.builtin.process_tools import (
    handle_env_info,
    handle_list_processes,
    handle_tail_log,
)


@pytest.mark.asyncio
async def test_list_processes() -> None:
    tools = ToolsConfig()
    r = await handle_list_processes({}, tools=tools)
    assert not r.is_error
    assert "PID" in r.output or "pid" in r.output.lower() or len(r.output) > 0


@pytest.mark.asyncio
async def test_list_processes_filter() -> None:
    tools = ToolsConfig()
    r = await handle_list_processes({"filter": "python"}, tools=tools)
    assert not r.is_error


@pytest.mark.asyncio
async def test_tail_log(tmp_path: Path) -> None:
    f = tmp_path / "app.log"
    f.write_text("\n".join(f"line{i}" for i in range(100)), encoding="utf-8")
    tools = ToolsConfig(working_directory=str(tmp_path), sandbox_to_workdir=True)
    r = await handle_tail_log({"path": str(f), "lines": 5}, tools=tools)
    assert not r.is_error
    assert "line99" in r.output
    assert "line0" not in r.output


@pytest.mark.asyncio
async def test_tail_log_reads_only_bounded_suffix(tmp_path: Path) -> None:
    f = tmp_path / "large.log"
    f.write_text(("old\n" * 10_000) + "latest\n", encoding="utf-8")
    tools = ToolsConfig(
        working_directory=str(tmp_path), sandbox_to_workdir=True, max_output_bytes=1024
    )

    result = await handle_tail_log({"path": str(f), "lines": 1}, tools=tools)

    assert not result.is_error
    assert result.output == "latest"


@pytest.mark.asyncio
async def test_tail_log_missing(tmp_path: Path) -> None:
    tools = ToolsConfig(working_directory=str(tmp_path), sandbox_to_workdir=True)
    r = await handle_tail_log({"path": str(tmp_path / "nope.log")}, tools=tools)
    assert r.is_error


@pytest.mark.asyncio
async def test_tail_log_secrets_prompt(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("SECRET=1\n", encoding="utf-8")
    tools = ToolsConfig(working_directory=str(tmp_path), sandbox_to_workdir=True)
    r = await handle_tail_log({"path": str(env)}, tools=tools, approved=False)
    assert r.decision == "prompt"


@pytest.mark.asyncio
async def test_env_info() -> None:
    tools = ToolsConfig()
    r = await handle_env_info({}, tools=tools)
    assert not r.is_error
    assert "cwd" in r.output
    assert "sk-" not in r.output
