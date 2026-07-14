"""More git and write tool coverage."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from aegis.config import build_config
from aegis.config.schema import ToolsConfig, ToolsGitConfig
from aegis.tools.builtin.git_tools import (
    git_tool_specs,
    handle_git_commit,
    handle_git_diff,
)
from aegis.tools.builtin.write_tools import handle_write_file
from aegis.tools.factory import build_registry


def _repo(tmp_path: Path) -> Path:
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "t@t"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "t"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    (tmp_path / "a.txt").write_text("a\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "c1"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    (tmp_path / "a.txt").write_text("b\n", encoding="utf-8")
    return tmp_path


@pytest.mark.asyncio
async def test_git_diff(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    tools = ToolsConfig(working_directory=str(repo), git=ToolsGitConfig(enabled=True))
    r = await handle_git_diff({"path": str(repo)}, tools=tools)
    assert not r.is_error


@pytest.mark.asyncio
async def test_git_commit_disabled(tmp_path: Path) -> None:
    tools = ToolsConfig(git=ToolsGitConfig(enabled=True, allow_commit=False))
    r = await handle_git_commit({"message": "x"}, tools=tools, approved=True)
    assert r.is_error
    assert "disabled" in r.output


@pytest.mark.asyncio
async def test_git_commit_needs_approval(tmp_path: Path) -> None:
    tools = ToolsConfig(git=ToolsGitConfig(enabled=True, allow_commit=True))
    r = await handle_git_commit({"message": "x"}, tools=tools, approved=False)
    assert r.decision == "prompt"


@pytest.mark.asyncio
async def test_write_secrets_denied(tmp_path: Path) -> None:
    tools = ToolsConfig(working_directory=str(tmp_path), sandbox_to_workdir=True)
    r = await handle_write_file(
        {"path": str(tmp_path / ".env"), "content": "x=1"},
        tools=tools,
        approved=True,
    )
    assert r.is_error
    assert "secrets" in r.output


def test_factory_standard_and_oncall_packs() -> None:
    cfg = build_config({"profile": {"name": "standard"}})
    reg = build_registry(cfg)
    names = reg.names()
    assert "git_status" in names
    assert "list_processes" in names
    assert "write_file" in names

    cfg2 = build_config({"profile": {"name": "oncall"}})
    reg2 = build_registry(cfg2)
    assert "kubectl" in reg2.names()


def test_git_tool_specs() -> None:
    assert any(s.name == "git_status" for s in git_tool_specs())
