"""Executor timeout and kill path."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from aegis.config.schema import DEFAULT_READ_SHELL_RULES, ToolsConfig, ToolsShellConfig
from aegis.tools.executor import _kill_process_group, _truncate, run_argv, terminate_process


@pytest.mark.asyncio
async def test_timeout_kills_process(tmp_path: Path):
    tools = ToolsConfig(
        working_directory=str(tmp_path),
        shell=ToolsShellConfig(enabled=True, rules=list(DEFAULT_READ_SHELL_RULES)),
        default_timeout_s=1,
    )
    # Use prechecked with sleep if available
    if not Path("/usr/bin/sleep").exists() and not Path("/bin/sleep").exists():
        pytest.skip("no sleep binary")
    sleep = "/usr/bin/sleep" if Path("/usr/bin/sleep").exists() else "/bin/sleep"
    result = await run_argv([sleep, "30"], tools, timeout_s=1, prechecked=True)
    assert result.is_error
    assert "timeout" in result.output


def test_truncate():
    text = "x" * 1000
    out = _truncate(text, 100)
    assert "truncated" in out
    assert _truncate("hi", 100) == "hi"


def test_kill_process_group_invalid():
    _kill_process_group(None)
    _kill_process_group(99999999)


@pytest.mark.asyncio
async def test_terminate_process_drains_verbose_child() -> None:
    proc = await asyncio.create_subprocess_exec(
        "python3",
        "-c",
        "import sys, time; sys.stdout.write('x' * 200000); sys.stdout.flush(); time.sleep(30)",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
    )
    await asyncio.wait_for(terminate_process(proc), timeout=2)
    assert proc.returncode is not None
