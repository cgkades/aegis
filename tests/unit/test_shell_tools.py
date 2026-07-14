"""Shell tool handler tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from aegis.config.schema import DEFAULT_READ_SHELL_RULES, ToolsConfig, ToolsShellConfig
from aegis.tools.builtin.shell_tools import handle_run_command, shell_tool_specs


@pytest.mark.asyncio
async def test_run_command_argv_schema() -> None:
    tools = ToolsConfig(
        shell=ToolsShellConfig(enabled=True, rules=list(DEFAULT_READ_SHELL_RULES))
    )
    r = await handle_run_command({"command": "ls"}, tools=tools)
    assert "argv_only_schema" in r.output


@pytest.mark.asyncio
async def test_run_command_ok(tmp_path: Path) -> None:
    tools = ToolsConfig(
        working_directory=str(tmp_path),
        sandbox_to_workdir=True,
        shell=ToolsShellConfig(enabled=True, rules=list(DEFAULT_READ_SHELL_RULES)),
    )
    r = await handle_run_command({"argv": ["pwd"]}, tools=tools)
    if r.is_error and "unknown" in r.output:
        pytest.skip("pwd not found")
    # pwd might auto


@pytest.mark.asyncio
async def test_run_command_disabled(tmp_path: Path) -> None:
    tools = ToolsConfig(
        working_directory=str(tmp_path),
        shell=ToolsShellConfig(enabled=False),
    )
    r = await handle_run_command({"argv": ["ls"]}, tools=tools)
    assert r.is_error


def test_shell_tool_specs() -> None:
    specs = shell_tool_specs()
    assert specs[0].name == "run_command"
