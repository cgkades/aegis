"""Filesystem tool tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from aegis.config import build_config
from aegis.config.schema import ToolsConfig
from aegis.tools.builtin.fs_tools import handle_list_dir, handle_read_file, handle_search_files
from aegis.tools.factory import build_registry


@pytest.mark.asyncio
async def test_list_and_read(tmp_path: Path) -> None:
    f = tmp_path / "hello.txt"
    f.write_text("hello aegis", encoding="utf-8")
    tools = ToolsConfig(working_directory=str(tmp_path), sandbox_to_workdir=True)

    listed = await handle_list_dir({"path": str(tmp_path)}, tools=tools)
    assert not listed.is_error
    assert "hello.txt" in listed.output

    read = await handle_read_file({"path": str(f)}, tools=tools)
    assert not read.is_error
    assert "hello aegis" in read.output


@pytest.mark.asyncio
async def test_read_outside_sandbox_denied(tmp_path: Path) -> None:
    tools = ToolsConfig(working_directory=str(tmp_path), sandbox_to_workdir=True)
    result = await handle_read_file({"path": "/etc/passwd"}, tools=tools)
    assert result.is_error
    assert "sandbox" in result.output


@pytest.mark.asyncio
async def test_read_env_requires_approval(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("SECRET=1", encoding="utf-8")
    tools = ToolsConfig(working_directory=str(tmp_path), sandbox_to_workdir=True)
    result = await handle_read_file({"path": str(env)}, tools=tools, approved=False)
    assert result.decision == "prompt"
    assert result.meta.get("needs_approval")

    allowed = await handle_read_file({"path": str(env)}, tools=tools, approved=True)
    assert not allowed.is_error
    assert "SECRET=1" in allowed.output


@pytest.mark.asyncio
async def test_search_files(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("x", encoding="utf-8")
    (tmp_path / "b.txt").write_text("y", encoding="utf-8")
    tools = ToolsConfig(working_directory=str(tmp_path), sandbox_to_workdir=True)
    result = await handle_search_files({"pattern": "*.py", "path": str(tmp_path)}, tools=tools)
    assert not result.is_error
    assert "a.py" in result.output


@pytest.mark.asyncio
async def test_list_dir_default_is_workdir_not_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    work = tmp_path / "work"
    other = tmp_path / "other"
    work.mkdir()
    other.mkdir()
    (work / "in_work.txt").write_text("w", encoding="utf-8")
    (other / "in_other.txt").write_text("o", encoding="utf-8")
    monkeypatch.chdir(other)
    tools = ToolsConfig(working_directory=str(work), sandbox_to_workdir=True)
    listed = await handle_list_dir({}, tools=tools)
    assert not listed.is_error
    assert "in_work.txt" in listed.output
    assert "in_other.txt" not in listed.output


@pytest.mark.asyncio
async def test_relative_read_joins_workdir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    work = tmp_path / "work"
    work.mkdir()
    (work / "rel.txt").write_text("relative-ok", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    tools = ToolsConfig(working_directory=str(work), sandbox_to_workdir=True)
    read = await handle_read_file({"path": "rel.txt"}, tools=tools)
    assert not read.is_error
    assert "relative-ok" in read.output


@pytest.mark.asyncio
async def test_search_files_skips_symlink_escape(tmp_path: Path) -> None:
    work = tmp_path / "work"
    outside = tmp_path / "outside"
    work.mkdir()
    outside.mkdir()
    secret = outside / "secret.key"
    secret.write_text("top-secret", encoding="utf-8")
    (work / "escape").symlink_to(outside)
    tools = ToolsConfig(working_directory=str(work), sandbox_to_workdir=True)
    result = await handle_search_files({"pattern": "*", "path": str(work)}, tools=tools)
    assert not result.is_error
    assert "secret.key" not in result.output
    assert str(outside) not in result.output


def test_mvp_registry_has_fs_not_shell() -> None:
    cfg = build_config({"profile": {"name": "mvp"}})
    reg = build_registry(cfg)
    names = reg.names()
    assert "list_dir" in names
    assert "read_file" in names
    assert "run_command" not in names


def test_shell_registry_when_enabled() -> None:
    cfg = build_config(
        {
            "profile": {"name": "mvp"},
            "tools": {
                "enabled": ["fs", "shell"],
                "shell": {"enabled": True},
            },
        }
    )
    # ensure default rules applied via factory
    reg = build_registry(cfg)
    assert "run_command" in reg.names()


@pytest.mark.asyncio
async def test_run_command_rejects_command_key() -> None:
    cfg = build_config(
        {
            "tools": {
                "enabled": ["fs", "shell"],
                "shell": {"enabled": True},
            }
        }
    )
    reg = build_registry(cfg)
    result = await reg.dispatch("run_command", {"command": "ls"})
    assert result.is_error
    assert "argv_only_schema" in result.output
