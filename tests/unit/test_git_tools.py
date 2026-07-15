"""Git tool tests."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from aegis.config.schema import ToolsConfig, ToolsGitConfig
from aegis.tools.builtin.git_tools import handle_git_log, handle_git_status
from aegis.tools.types import ToolResult


def _git_repo(tmp_path: Path) -> Path:
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "aegis@test"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Aegis"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    f = tmp_path / "f.txt"
    f.write_text("hi", encoding="utf-8")
    subprocess.run(["git", "add", "f.txt"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    return tmp_path


@pytest.mark.asyncio
async def test_git_status_and_log(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path)
    tools = ToolsConfig(
        working_directory=str(repo),
        git=ToolsGitConfig(enabled=True),
    )
    st = await handle_git_status({"path": str(repo)}, tools=tools)
    assert not st.is_error
    log = await handle_git_log({"path": str(repo), "n": 5}, tools=tools)
    assert not log.is_error
    assert "init" in log.output


@pytest.mark.asyncio
async def test_git_relative_path_uses_configured_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "repo").mkdir()
    other_cwd = tmp_path / "other"
    other_cwd.mkdir()
    monkeypatch.chdir(other_cwd)
    tools = ToolsConfig(working_directory=str(workspace), git=ToolsGitConfig(enabled=True))

    with patch(
        "aegis.tools.builtin.git_tools._git",
        new=AsyncMock(return_value=ToolResult(output="ok", risk="read")),
    ) as git:
        result = await handle_git_status({"path": "repo"}, tools=tools)

    assert not result.is_error
    assert git.await_args.args[1] == str((workspace / "repo").resolve())
