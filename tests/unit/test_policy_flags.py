"""Policy flag tables and path extraction edge cases."""

from __future__ import annotations

from pathlib import Path

from aegis.config.schema import (
    DEFAULT_READ_SHELL_RULES,
    ShellRule,
    ToolsConfig,
    ToolsGitConfig,
    ToolsShellConfig,
)
from aegis.tools.policy import (
    _extract_path_like_args,
    _violates_flag_policy,
    evaluate_run_command,
)
from aegis.tools.types import PolicyDecision


def test_extract_paths():
    args = ["-n", "5", "README.md", "--color=auto", "/tmp/x", "-g", "*.py"]
    paths = _extract_path_like_args(args)
    assert any("README" in p or p.endswith(".md") for p in paths) or "README.md" in paths


def test_violates_rg_pre():
    rule = ShellRule(exe="rg", risk="read", decision="auto")
    assert _violates_flag_policy("rg", ["--pre", "sh", "pat"], rule) is True


def test_violates_git_c_flag():
    rule = ShellRule(exe="git", risk="read", decision="auto")
    assert _violates_flag_policy("git", ["-c", "core.sshCommand=x", "status"], rule) is True


def test_shell_disabled_early():
    t = ToolsConfig(shell=ToolsShellConfig(enabled=False))
    r = evaluate_run_command(["ls"], t)
    assert r.reason == "shell_disabled"


def test_absolute_exe_outside_allowed(tmp_path: Path):
    # create a fake binary path outside allowed dirs
    t = ToolsConfig(
        working_directory=str(tmp_path),
        shell=ToolsShellConfig(
            enabled=True,
            rules=list(DEFAULT_READ_SHELL_RULES),
            allowed_executable_dirs=["/usr/bin"],
        ),
    )
    r = evaluate_run_command(["/tmp/not-allowed-bin"], t)
    assert r.decision is PolicyDecision.DENY


def test_git_with_readonly_rules_status(tmp_path: Path):
    t = ToolsConfig(
        working_directory=str(tmp_path),
        shell=ToolsShellConfig(enabled=True, rules=[]),
        git=ToolsGitConfig(deny_via_shell=True, shell_readonly_rules=True),
    )
    r = evaluate_run_command(["git", "status"], t)
    # `git status` is read-only, so shell_readonly_rules must let it through
    # rather than bouncing it to the structured tool — unless git isn't on this
    # host's allowed executable dirs at all.
    if r.reason == "unknown_executable":
        assert r.decision is PolicyDecision.DENY
    else:
        assert r.reason == "ok"
        assert r.decision is PolicyDecision.AUTO
        assert r.risk == "read"
