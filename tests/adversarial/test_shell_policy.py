"""Adversarial tests for argv shell policy."""

from __future__ import annotations

from pathlib import Path

import pytest

from aegis.config.schema import (
    DEFAULT_READ_SHELL_RULES,
    ToolsConfig,
    ToolsGitConfig,
    ToolsShellConfig,
)
from aegis.tools.policy import evaluate_run_command, matches_secrets_globs
from aegis.tools.types import PolicyDecision


def _tools(
    *,
    shell_enabled: bool = True,
    rules=None,
    workdir: str | None = None,
) -> ToolsConfig:
    cfg = ToolsConfig(
        working_directory=workdir or str(Path.cwd()),
        sandbox_to_workdir=True,
        shell=ToolsShellConfig(
            enabled=shell_enabled,
            rules=rules if rules is not None else list(DEFAULT_READ_SHELL_RULES),
        ),
        git=ToolsGitConfig(deny_via_shell=True, shell_readonly_rules=False),
    )
    return cfg


def test_shell_disabled_denies() -> None:
    r = evaluate_run_command(["ls"], _tools(shell_enabled=False))
    assert r.decision is PolicyDecision.DENY
    assert r.reason == "shell_disabled"


def test_kubectl_always_denied_via_shell() -> None:
    r = evaluate_run_command(["kubectl", "get", "pods"], _tools())
    assert r.decision is PolicyDecision.DENY
    assert "kubectl" in r.reason or "reserved" in r.reason


def test_sudo_denied() -> None:
    r = evaluate_run_command(["sudo", "ls"], _tools())
    assert r.decision is PolicyDecision.DENY


def test_ssh_denied() -> None:
    r = evaluate_run_command(["ssh", "host"], _tools())
    assert r.decision is PolicyDecision.DENY


def test_docker_denied() -> None:
    r = evaluate_run_command(["docker", "ps"], _tools())
    assert r.decision is PolicyDecision.DENY


def test_git_denied_when_structured_owner() -> None:
    r = evaluate_run_command(["git", "status"], _tools())
    assert r.decision is PolicyDecision.DENY
    assert r.reason == "use_structured_git"


def test_metachar_denied() -> None:
    r = evaluate_run_command(["ls", "$(whoami)"], _tools())
    assert r.decision is PolicyDecision.DENY
    assert r.reason == "metachar_in_argv"


def test_backtick_denied() -> None:
    r = evaluate_run_command(["ls", "`id`"], _tools())
    assert r.decision is PolicyDecision.DENY


def test_empty_argv_denied() -> None:
    r = evaluate_run_command([], _tools())
    assert r.decision is PolicyDecision.DENY


def test_no_rule_for_unknown_exe() -> None:
    # python3 might resolve but no rule
    r = evaluate_run_command(["python3", "-c", "print(1)"], _tools())
    assert r.decision is PolicyDecision.DENY
    assert r.reason in {"no_rule", "unknown_executable", "exe_dir"}


def test_ls_allowed_when_enabled(tmp_path: Path) -> None:
    tools = _tools(workdir=str(tmp_path))
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    r = evaluate_run_command(["ls", str(tmp_path)], tools)
    # may be auto if ls exists under /usr/bin
    if r.reason == "unknown_executable":
        pytest.skip("ls not found in allowed dirs")
    assert r.decision is PolicyDecision.AUTO
    assert r.risk == "read"


def test_secrets_path_never_auto(tmp_path: Path) -> None:
    secrets = tmp_path / ".env"
    secrets.write_text("KEY=1", encoding="utf-8")
    tools = _tools(workdir=str(tmp_path))
    r = evaluate_run_command(["cat", str(secrets)], tools)
    if r.reason == "unknown_executable":
        pytest.skip("cat not found")
    assert r.decision is PolicyDecision.PROMPT
    assert r.risk == "secrets"


def test_sandbox_escape_denied(tmp_path: Path) -> None:
    tools = _tools(workdir=str(tmp_path))
    r = evaluate_run_command(["cat", "/etc/passwd"], tools)
    if r.reason == "unknown_executable":
        pytest.skip("cat not found")
    assert r.decision is PolicyDecision.DENY
    assert r.reason == "sandbox"


def test_rg_pre_flag_denied() -> None:
    tools = _tools()
    r = evaluate_run_command(["rg", "--pre", "sh", "pattern"], tools)
    if r.reason == "unknown_executable":
        pytest.skip("rg not found")
    assert r.decision is PolicyDecision.DENY


def test_secrets_glob_ssh() -> None:
    assert matches_secrets_globs(str(Path.home() / ".ssh" / "id_rsa"), ["**/.ssh/**"])
    assert matches_secrets_globs("/tmp/project/.env", ["**/.env"])
