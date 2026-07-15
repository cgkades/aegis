"""Executor output cap and cancel kill."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from aegis.config.schema import ToolsConfig
from aegis.tools.executor import run_argv


@pytest.mark.asyncio
async def test_run_argv_truncates_large_output(tmp_path: Path) -> None:
    from aegis.config.schema import ToolsShellConfig

    tools = ToolsConfig(
        working_directory=str(tmp_path),
        max_output_bytes=1024,
        shell=ToolsShellConfig(enabled=True),
    )
    # prechecked bypasses policy; use python to emit many bytes
    r = await run_argv(
        ["python3", "-c", "print('x' * 20000)"],
        tools,
        prechecked=True,
        timeout_s=10,
    )
    assert len(r.output.encode()) <= tools.max_output_bytes + 128
    assert r.meta.get("truncated") is True or "truncated" in r.output


@pytest.mark.asyncio
async def test_run_argv_cancel_kills_child(tmp_path: Path) -> None:
    from aegis.config.schema import ToolsShellConfig

    tools = ToolsConfig(
        working_directory=str(tmp_path),
        shell=ToolsShellConfig(enabled=True),
    )

    async def _run() -> None:
        await run_argv(
            ["python3", "-c", "import time; time.sleep(30)"],
            tools,
            prechecked=True,
            timeout_s=60,
        )

    task = asyncio.create_task(_run())
    await asyncio.sleep(0.2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
