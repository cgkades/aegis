"""Write tool tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from aegis.config.schema import ToolsConfig
from aegis.tools.builtin.write_tools import handle_apply_patch, handle_write_file


@pytest.mark.asyncio
async def test_write_requires_approval(tmp_path: Path) -> None:
    tools = ToolsConfig(working_directory=str(tmp_path), sandbox_to_workdir=True)
    path = str(tmp_path / "out.txt")
    r = await handle_write_file(
        {"path": path, "content": "hello"},
        tools=tools,
        approved=False,
    )
    assert r.decision == "prompt"

    r2 = await handle_write_file(
        {"path": path, "content": "hello"},
        tools=tools,
        approved=True,
    )
    assert not r2.is_error
    assert Path(path).read_text(encoding="utf-8") == "hello"


@pytest.mark.asyncio
async def test_apply_patch(tmp_path: Path) -> None:
    tools = ToolsConfig(working_directory=str(tmp_path), sandbox_to_workdir=True)
    f = tmp_path / "a.txt"
    f.write_text("foo bar foo", encoding="utf-8")
    r = await handle_apply_patch(
        {"path": str(f), "old": "bar", "new": "baz"},
        tools=tools,
        approved=True,
    )
    assert not r.is_error
    assert f.read_text(encoding="utf-8") == "foo baz foo"


@pytest.mark.asyncio
async def test_apply_patch_denies_secrets_path_even_when_approved(tmp_path: Path) -> None:
    tools = ToolsConfig(working_directory=str(tmp_path), sandbox_to_workdir=True)
    target = tmp_path / ".env"
    target.write_text("KEY=old\n", encoding="utf-8")

    result = await handle_apply_patch(
        {"path": str(target), "old": "old", "new": "new"},
        tools=tools,
        approved=True,
    )

    assert result.is_error
    assert result.decision == "deny"
    assert target.read_text(encoding="utf-8") == "KEY=old\n"
