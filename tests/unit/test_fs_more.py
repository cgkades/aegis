"""Additional fs tool edge cases."""

from __future__ import annotations

from pathlib import Path

import pytest

from aegis.config.schema import ToolsConfig
from aegis.tools.builtin.fs_tools import (
    handle_list_dir,
    handle_read_file,
    handle_search_files,
)
from aegis.tools.registry import ToolRegistry
from aegis.tools.types import ToolSpec


@pytest.mark.asyncio
async def test_list_dir_not_found(tmp_path: Path) -> None:
    tools = ToolsConfig(working_directory=str(tmp_path), sandbox_to_workdir=True)
    r = await handle_list_dir({"path": str(tmp_path / "nope")}, tools=tools)
    assert r.is_error


@pytest.mark.asyncio
async def test_list_dir_file_not_dir(tmp_path: Path) -> None:
    f = tmp_path / "f"
    f.write_text("x", encoding="utf-8")
    tools = ToolsConfig(working_directory=str(tmp_path), sandbox_to_workdir=True)
    r = await handle_list_dir({"path": str(f)}, tools=tools)
    assert r.is_error
    assert "not_a_directory" in r.output


@pytest.mark.asyncio
async def test_read_invalid_path(tmp_path: Path) -> None:
    tools = ToolsConfig(working_directory=str(tmp_path), sandbox_to_workdir=True)
    r = await handle_read_file({"path": 123}, tools=tools)  # type: ignore[arg-type]
    assert r.is_error


@pytest.mark.asyncio
async def test_read_file_clamps_model_requested_size_to_output_limit(tmp_path: Path) -> None:
    target = tmp_path / "large.txt"
    target.write_text("x" * 10_000, encoding="utf-8")
    tools = ToolsConfig(
        working_directory=str(tmp_path), sandbox_to_workdir=True, max_output_bytes=1024
    )

    result = await handle_read_file(
        {"path": str(target), "max_bytes": 1_000_000}, tools=tools
    )

    assert not result.is_error
    assert len(result.output.encode("utf-8")) <= 1_024 + len("\n…[truncated]")


@pytest.mark.asyncio
async def test_search_invalid(tmp_path: Path) -> None:
    tools = ToolsConfig(working_directory=str(tmp_path), sandbox_to_workdir=True)
    r = await handle_search_files({"pattern": 1}, tools=tools)  # type: ignore[arg-type]
    assert r.is_error


@pytest.mark.asyncio
async def test_registry_unknown_and_limits(tmp_path: Path) -> None:
    tools = ToolsConfig(
        working_directory=str(tmp_path),
        max_tool_calls_per_turn=1,
        max_tool_calls_per_session=2,
    )
    reg = ToolRegistry(tools)

    async def h(arguments, **kwargs):
        from aegis.tools.types import ToolResult

        return ToolResult(output="ok")

    reg.register(
        ToolSpec(
            name="t",
            description="t",
            parameters={"type": "object", "properties": {}},
            risk="read",
            handler=h,
        )
    )
    r = await reg.dispatch("missing", {})
    assert r.is_error
    assert "unknown_tool" in r.output

    r1 = await reg.dispatch("t", {})
    assert not r1.is_error
    r2 = await reg.dispatch("t", {})
    assert r2.is_error
    assert "max_tool_calls_per_turn" in r2.output

    reg.reset_turn()
    # session counter still has 1 successful call; turn reset allows one more
    r3 = await reg.dispatch("t", {})
    assert not r3.is_error  # session calls = 2
    reg.reset_turn()
    r4 = await reg.dispatch("t", {})
    assert r4.is_error
    assert "max_tool_calls_per_session" in r4.output
