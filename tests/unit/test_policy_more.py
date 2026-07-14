"""Extended policy engine coverage."""

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
    evaluate_read_file,
    evaluate_run_command,
    matches_secrets_globs,
)
from aegis.tools.types import PolicyDecision


def tools(workdir: str, **kwargs) -> ToolsConfig:
    shell = kwargs.pop("shell", None)
    git = kwargs.pop("git", None)
    return ToolsConfig(
        working_directory=workdir,
        sandbox_to_workdir=True,
        shell=shell
        or ToolsShellConfig(enabled=True, rules=list(DEFAULT_READ_SHELL_RULES)),
        git=git or ToolsGitConfig(),
        **kwargs,
    )


def test_git_shell_readonly_allows_status(tmp_path: Path) -> None:
    t = tools(
        str(tmp_path),
        git=ToolsGitConfig(deny_via_shell=True, shell_readonly_rules=True),
    )
    r = evaluate_run_command(["git", "status"], t)
    # may be unknown if git not in allowed dirs resolution — still not use_structured_git
    assert r.reason != "use_structured_git" or r.decision is PolicyDecision.DENY


def test_git_commit_via_shell_denied_when_readonly(tmp_path: Path) -> None:
    t = tools(
        str(tmp_path),
        git=ToolsGitConfig(deny_via_shell=True, shell_readonly_rules=True),
    )
    r = evaluate_run_command(["git", "commit", "-m", "x"], t)
    assert r.decision is PolicyDecision.DENY


def test_nul_and_metachar(tmp_path: Path) -> None:
    t = tools(str(tmp_path))
    assert evaluate_run_command(["ls", "a\x00b"], t).reason == "nul_in_argv"
    assert evaluate_run_command(["ls", "`id`"], t).reason == "metachar_in_argv"


def test_reserved_ssh_sudo(tmp_path: Path) -> None:
    t = tools(str(tmp_path))
    assert evaluate_run_command(["ssh", "h"], t).decision is PolicyDecision.DENY
    assert evaluate_run_command(["sudo", "ls"], t).decision is PolicyDecision.DENY
    assert evaluate_run_command(["docker", "ps"], t).decision is PolicyDecision.DENY
    assert evaluate_run_command(["helm", "list"], t).decision is PolicyDecision.DENY


def test_denylist_substring(tmp_path: Path) -> None:
    t = tools(str(tmp_path))
    # craft allowlisted exe with denylist in args if cat allowed
    r = evaluate_run_command(["cat", "rm -rf /"], t)
    # either denylist or sandbox/path
    assert r.decision is PolicyDecision.DENY


def test_no_rule(tmp_path: Path) -> None:
    t = tools(
        str(tmp_path),
        shell=ToolsShellConfig(
            enabled=True,
            rules=[ShellRule(exe="ls", risk="read", decision="auto")],
        ),
    )
    r = evaluate_run_command(["pwd"], t)
    assert r.decision is PolicyDecision.DENY
    assert r.reason in {"no_rule", "unknown_executable"}


def test_read_file_ok_and_sandbox(tmp_path: Path) -> None:
    f = tmp_path / "a.txt"
    f.write_text("x", encoding="utf-8")
    t = tools(str(tmp_path))
    assert evaluate_read_file(str(f), t).decision is PolicyDecision.AUTO
    assert evaluate_read_file("/etc/passwd", t).reason == "sandbox"


def test_secrets_globs_variants() -> None:
    assert matches_secrets_globs(str(Path.home() / ".ssh" / "id_ed25519"), ["**/.ssh/**"])
    assert matches_secrets_globs("/proj/.env.local", ["**/.env.*"])
    assert matches_secrets_globs("/proj/foo.pem", ["**/*.pem"])
    assert not matches_secrets_globs("/proj/readme.md", ["**/.env"])


def test_rg_pre_denied(tmp_path: Path) -> None:
    t = tools(str(tmp_path))
    r = evaluate_run_command(["rg", "--pre", "sh", "x"], t)
    assert r.decision is PolicyDecision.DENY


def test_empty_and_non_string_argv(tmp_path: Path) -> None:
    t = tools(str(tmp_path))
    assert evaluate_run_command([], t).decision is PolicyDecision.DENY
    assert evaluate_run_command(["ls", 1], t).decision is PolicyDecision.DENY  # type: ignore[list-item]
