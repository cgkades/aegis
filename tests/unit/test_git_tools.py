"""Git tool tests."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from aegis.config.schema import ToolsConfig, ToolsGitConfig
from aegis.tools.builtin.git_tools import handle_git_log, handle_git_status


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
