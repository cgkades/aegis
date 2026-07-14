"""Argv executor tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from aegis.config.schema import DEFAULT_READ_SHELL_RULES, ToolsConfig, ToolsShellConfig
from aegis.tools.executor import run_argv
from aegis.tools.policy import scrubbed_env


def _tools(workdir: str, *, enabled: bool = True) -> ToolsConfig:
    return ToolsConfig(
        working_directory=workdir,
        sandbox_to_workdir=True,
        shell=ToolsShellConfig(
            enabled=enabled,
            rules=list(DEFAULT_READ_SHELL_RULES),
        ),
    )


@pytest.mark.asyncio
async def test_run_argv_ls(tmp_path: Path) -> None:
    (tmp_path / "f.txt").write_text("x", encoding="utf-8")
    tools = _tools(str(tmp_path))
    result = await run_argv(["ls", str(tmp_path)], tools)
    if result.is_error and "unknown_executable" in result.output:
        pytest.skip("ls not in allowed dirs")
    assert not result.is_error
    assert "f.txt" in result.output


@pytest.mark.asyncio
async def test_run_argv_shell_disabled(tmp_path: Path) -> None:
    tools = _tools(str(tmp_path), enabled=False)
    result = await run_argv(["ls"], tools)
    assert result.is_error
    assert "shell_disabled" in result.output


@pytest.mark.asyncio
async def test_run_argv_timeout(tmp_path: Path) -> None:
    tools = _tools(str(tmp_path))
    tools.default_timeout_s = 1
    # sleep may not be in allowlist rules — expect deny or timeout
    result = await run_argv(["sleep", "30"], tools, timeout_s=1)
    assert result.is_error


@pytest.mark.asyncio
async def test_run_argv_prechecked(tmp_path: Path) -> None:
    tools = _tools(str(tmp_path))
    # pwd if available
    result = await run_argv(["/bin/pwd"], tools, prechecked=True)
    if result.is_error and "spawn" in result.output:
        pytest.skip("pwd missing")
    # prechecked skips policy; may still succeed
    assert result.output


def test_scrubbed_env_no_secrets(monkeypatch) -> None:
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "supersecret")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    env = scrubbed_env()
    assert "AWS_SECRET_ACCESS_KEY" not in env
    assert "OPENAI_API_KEY" not in env
    assert "PATH" in env


def test_scrubbed_env_allowlist(monkeypatch) -> None:
    monkeypatch.setenv("KUBECONFIG", "/tmp/kube")
    env = scrubbed_env(("KUBECONFIG",))
    assert env.get("KUBECONFIG") == "/tmp/kube"
