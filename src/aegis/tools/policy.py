"""Argv-only command policy and path/secrets evaluation."""

from __future__ import annotations

import fnmatch
import os
import re
import shutil
from pathlib import Path
from typing import Any

from aegis.config.schema import (
    ShellRule,
    ToolsConfig,
    ToolsSecretsConfig,
)
from aegis.tools.types import PolicyDecision, PolicyResult, RiskClass, ToolResult, err_json

# Global hygiene: reject shell metacharacter injections in argv elements
_BAD_ARGV_RE = re.compile(r"[`\n\r]|\$\(|\$\{")

# Default flag tables (DESIGN)
_DEFAULT_FLAG_POLICY: dict[str, dict[str, set[str] | None]] = {
    "ls": {
        "allowed": {"-l", "-a", "-h", "-1", "-R", "-t", "-S", "--color", "--color=auto"},
        "denied": set(),
    },
    "pwd": {"allowed": {"-P", "-L"}, "denied": set()},
    "head": {"allowed": {"-n", "-c", "-q", "-v", "--lines", "--bytes"}, "denied": set()},
    "tail": {
        "allowed": {"-n", "-c", "-f", "-q", "-v", "--lines", "--bytes", "--follow"},
        "denied": set(),
    },
    "cat": {"allowed": {"-n", "-b", "-s", "-A", "-T", "-v", "-E"}, "denied": set()},
    "rg": {
        "allowed": {
            "-n",
            "-i",
            "-l",
            "-g",
            "-t",
            "-F",
            "-w",
            "-c",
            "-m",
            "-v",
            "-e",
            "--json",
            "--color",
            "--hidden",
            "--no-heading",
            "--line-number",
        },
        "denied": {"--pre", "--pre-glob", "--config", "--debugconfig"},
    },
    "git": {
        "allowed": set(),  # verbs checked separately
        "denied": {"-c", "--exec-path", "--git-dir", "--work-tree"},
    },
}

_GIT_READONLY_VERBS = {"status", "diff", "log", "show", "branch", "rev-parse", "remote"}


def evaluate_run_command(argv: list[str] | Any, tools: ToolsConfig) -> PolicyResult:
    """Normative evaluate_run_command algorithm from DESIGN.md."""
    # 1. Shape
    if not isinstance(argv, list) or not argv:
        return PolicyResult(PolicyDecision.DENY, "exec", "empty_or_invalid_argv")
    if not all(isinstance(a, str) for a in argv):
        return PolicyResult(PolicyDecision.DENY, "exec", "argv_not_all_strings")
    if any("\x00" in a for a in argv):
        return PolicyResult(PolicyDecision.DENY, "exec", "nul_in_argv")
    if any(_BAD_ARGV_RE.search(a) for a in argv):
        return PolicyResult(PolicyDecision.DENY, "exec", "metachar_in_argv")

    if not tools.shell.enabled:
        return PolicyResult(PolicyDecision.DENY, "exec", "shell_disabled")

    # 2. Resolve executable (basename reserved check applies even if missing on PATH)
    exe = argv[0]
    base_hint = Path(exe).name.lower()
    reserved = {b.lower() for b in tools.shell.reserved_binaries}
    if base_hint in reserved and "/" not in exe:
        if base_hint in {"kubectl", "oc", "helm"}:
            return PolicyResult(
                PolicyDecision.DENY, "exec", "reserved_use_structured_kubectl"
            )
        if base_hint in {"docker", "podman", "nerdctl"}:
            return PolicyResult(
                PolicyDecision.DENY,
                "exec",
                "reserved_use_structured_docker_or_disabled",
            )
        if base_hint == "git":
            if tools.git.deny_via_shell and not tools.git.shell_readonly_rules:
                return PolicyResult(PolicyDecision.DENY, "exec", "use_structured_git")
        else:
            return PolicyResult(PolicyDecision.DENY, "exec", "reserved_binary")

    resolved = _resolve_executable(exe, tools.shell.allowed_executable_dirs)
    if resolved is None:
        return PolicyResult(PolicyDecision.DENY, "exec", "unknown_executable")
    try:
        real = Path(resolved).resolve()
    except OSError:
        return PolicyResult(PolicyDecision.DENY, "exec", "resolve_failed")
    if not _is_under_dirs(real, tools.shell.allowed_executable_dirs):
        return PolicyResult(PolicyDecision.DENY, "exec", "exe_dir")

    base = real.name

    # 3. Reserved binaries — membership ⇒ DENY (resolved path basename)
    if base.lower() in reserved:
        if base.lower() in {"kubectl", "oc", "helm"}:
            return PolicyResult(
                PolicyDecision.DENY, "exec", "reserved_use_structured_kubectl"
            )
        if base.lower() in {"docker", "podman", "nerdctl"}:
            return PolicyResult(
                PolicyDecision.DENY,
                "exec",
                "reserved_use_structured_docker_or_disabled",
            )
        return PolicyResult(PolicyDecision.DENY, "exec", "reserved_binary")

    # 3b. Git ownership
    if base.lower() == "git":
        if tools.git.deny_via_shell and not tools.git.shell_readonly_rules:
            return PolicyResult(PolicyDecision.DENY, "exec", "use_structured_git")
        # shell_readonly_rules path: only allow read verbs
        verb = _git_verb(argv[1:])
        if verb not in _GIT_READONLY_VERBS:
            return PolicyResult(PolicyDecision.DENY, "exec", "git_verb_not_readonly")

    # 4. Match rules
    rule = _match_rule(base, argv[1:], tools.shell.rules)
    if rule is None and base.lower() == "git" and tools.git.shell_readonly_rules:
        rule = ShellRule(exe="git", verbs=["*"], risk="read", decision="auto")
    if rule is None:
        return PolicyResult(PolicyDecision.DENY, "exec", "no_rule")

    risk: RiskClass = rule.risk if rule.risk in {
        "read",
        "write",
        "destroy",
        "secrets",
        "network",
        "admin",
    } else "exec"
    # Map admin -> exec for our risk set
    if risk == "admin":  # type: ignore[comparison-overlap]
        risk = "exec"

    # Normalize risk to allowed set
    if risk not in {"read", "exec", "write", "network", "destroy", "secrets"}:
        risk = "exec"

    secrets_hit = False
    workdir = Path(tools.working_directory).expanduser().resolve()

    # 5. Path args sandbox + secrets
    for path_arg in _extract_path_like_args(argv[1:]):
        try:
            rp = Path(path_arg).expanduser().resolve()
        except OSError:
            return PolicyResult(PolicyDecision.DENY, risk, "path_resolve_failed")
        if tools.sandbox_to_workdir and not _is_inside(rp, workdir):
            return PolicyResult(PolicyDecision.DENY, risk, "sandbox")
        if matches_secrets_globs(str(rp), tools.secrets.path_globs):
            secrets_hit = True
            risk = "secrets"

    # 6. Flag policy
    if _violates_flag_policy(base, argv[1:], rule):
        return PolicyResult(PolicyDecision.DENY, risk, "flag_policy")

    # 7. Decision
    decision = PolicyDecision(rule.decision)
    if secrets_hit:
        if tools.secrets.decision.value == "deny":
            return PolicyResult(PolicyDecision.DENY, "secrets", "secrets_path")
        decision = PolicyDecision.PROMPT
        risk = "secrets"
    if decision is PolicyDecision.AUTO and risk != "read":
        # auto_readonly profile behavior
        decision = PolicyDecision.PROMPT

    # 8. Substring denylist (belt)
    joined = " ".join(argv)
    for sub in tools.shell.denylist_substrings:
        if sub and sub in joined:
            return PolicyResult(PolicyDecision.DENY, risk, "denylist_substring")

    return PolicyResult(
        decision,
        risk if risk in {"read", "exec", "write", "network", "destroy", "secrets"} else "exec",
        "ok",
        resolved_argv=[str(real), *argv[1:]],
    )


def evaluate_read_file(path: str, tools: ToolsConfig) -> PolicyResult:
    """Path sandbox + secrets for structured read_file."""
    try:
        rp = Path(path).expanduser().resolve()
    except OSError:
        return PolicyResult(PolicyDecision.DENY, "read", "path_resolve_failed")
    workdir = Path(tools.working_directory).expanduser().resolve()
    if tools.sandbox_to_workdir and not _is_inside(rp, workdir):
        return PolicyResult(PolicyDecision.DENY, "read", "sandbox")
    if matches_secrets_globs(str(rp), tools.secrets.path_globs):
        if tools.secrets.decision.value == "deny":
            return PolicyResult(PolicyDecision.DENY, "secrets", "secrets_path")
        return PolicyResult(PolicyDecision.PROMPT, "secrets", "secrets_path")
    return PolicyResult(PolicyDecision.AUTO, "read", "ok")


def evaluate_list_dir(path: str, tools: ToolsConfig) -> PolicyResult:
    return evaluate_read_file(path, tools)


def path_within_workdir(path: str, tools: ToolsConfig) -> bool:
    """True if a directory path is allowed as a working directory for a tool.

    Used by structured git tools to keep their ``path`` (used as cwd) inside the
    sandbox, matching the file tools' behavior. When sandboxing is off, any path
    is allowed.
    """
    if not tools.sandbox_to_workdir:
        return True
    try:
        rp = Path(path).expanduser().resolve()
    except OSError:
        return False
    workdir = Path(tools.working_directory).expanduser().resolve()
    return _is_inside(rp, workdir)


def matches_secrets_globs(path: str, globs: list[str]) -> bool:
    """Match path against secrets globs (fnmatch on full path and home-relative)."""
    p = path.replace("\\", "/")
    home = str(Path.home()).replace("\\", "/")
    candidates = {p, p.lower()}
    if p.startswith(home):
        rel = "~" + p[len(home) :]
        candidates.add(rel)
        candidates.add(rel.lower())
    # Also basename-focused patterns
    name = Path(p).name
    for g in globs:
        g_norm = g.replace("\\", "/")
        for cand in candidates:
            if fnmatch.fnmatch(cand, g_norm) or fnmatch.fnmatch(cand, g_norm.lower()):
                return True
            # **/foo style against full path segments
            if g_norm.startswith("**/") and fnmatch.fnmatch(name, g_norm[3:]):
                return True
            if fnmatch.fnmatch(name, g_norm):
                return True
    return False


def _resolve_executable(exe: str, allowed_dirs: list[str]) -> str | None:
    if "/" in exe or exe.startswith("."):
        path = Path(exe).expanduser()
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)
        return None
    # Restrict PATH lookup to allowed dirs only
    for d in allowed_dirs:
        candidate = Path(d) / exe
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    # Fallback: which, then verify under allowed dirs
    found = shutil.which(exe)
    if found and _is_under_dirs(Path(found).resolve(), allowed_dirs):
        return found
    return None


def _is_under_dirs(path: Path, dirs: list[str]) -> bool:
    for d in dirs:
        try:
            base = Path(d).resolve()
            path.relative_to(base)
            return True
        except ValueError:
            continue
    return False


def _is_inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return path == root


def _match_rule(base: str, args: list[str], rules: list[ShellRule]) -> ShellRule | None:
    for rule in rules:
        if rule.exe != base and rule.exe != Path(base).name:
            continue
        if "*" in rule.verbs:
            return rule
        if not args:
            # bare command
            if "" in rule.verbs or "*" in rule.verbs:
                return rule
            continue
        verb = args[0].lstrip("-") if not args[0].startswith("-") else args[0]
        # For simple tools like ls, first non-flag is path not verb — treat as *
        if rule.exe in {"ls", "pwd", "head", "tail", "cat", "rg"}:
            return rule
        if verb in rule.verbs or args[0] in rule.verbs:
            return rule
    return None


def _git_verb(args: list[str]) -> str:
    for a in args:
        if a.startswith("-"):
            continue
        return a
    return ""


def _extract_path_like_args(args: list[str]) -> list[str]:
    """Heuristic: args that look like paths (contain / or start with . or ~)."""
    out: list[str] = []
    skip_next = False
    flag_takes_value = {
        "-n",
        "-c",
        "-g",
        "-t",
        "-m",
        "-e",
        "--lines",
        "--bytes",
        "-C",
        "-A",
        "-B",
    }
    for a in args:
        if skip_next:
            skip_next = False
            # value might still be a path for -g etc.
            if _looks_like_path(a):
                out.append(a)
            continue
        if a in flag_takes_value or a.startswith("--lines=") or a.startswith("--bytes="):
            if "=" not in a and a in flag_takes_value:
                skip_next = True
            continue
        if a.startswith("-") and not a.startswith("./") and a not in {"-"}:
            # flag; if --pre=cmd style already denied elsewhere
            if "=" in a:
                _, _, val = a.partition("=")
                if _looks_like_path(val):
                    out.append(val)
            continue
        if _looks_like_path(a) or (a and not a.startswith("-")):
            # include bare filenames as path-like for sandbox
            if _looks_like_path(a) or "/" in a or a in {".", ".."} or a.startswith("~"):
                out.append(a)
            elif a and not a.startswith("-"):
                # relative bare path
                out.append(a)
    return out


def _looks_like_path(s: str) -> bool:
    return (
        "/" in s
        or s.startswith("~")
        or s.startswith(".")
        or s.endswith((".txt", ".log", ".py", ".json", ".toml", ".yaml", ".yml", ".md"))
    )


def _violates_flag_policy(base: str, args: list[str], rule: ShellRule) -> bool:
    denied: set[str] = set()
    allowed: set[str] | None = None
    table = _DEFAULT_FLAG_POLICY.get(base) or _DEFAULT_FLAG_POLICY.get(Path(base).name)
    if table:
        denied |= set(table.get("denied") or set())  # type: ignore[arg-type]
        allowed = set(table.get("allowed") or set())  # type: ignore[assignment]
    if rule.denied_flags:
        denied |= set(rule.denied_flags)
    if rule.allowed_flags is not None:
        allowed = set(rule.allowed_flags)

    for a in args:
        if not a.startswith("-") or a in {"-"}:
            continue
        # split --flag=value
        flag = a.split("=", 1)[0]
        if flag in denied:
            return True
        # denied long options for rg
        if base in {"rg", "git"} and flag in denied:
            return True
        if allowed is not None and base in {"rg"} and flag.startswith("--"):
            if flag not in allowed and flag not in {"--help", "--version"}:
                # allow common short; for long require allowlist for dangerous ones only
                if flag in {"--pre", "--pre-glob", "--config", "--debugconfig"}:
                    return True
        if flag in {"--pre", "--pre-glob", "--config", "--debugconfig", "-c", "--exec-path"}:
            if flag in denied or base in {"rg", "git"}:
                return True
    return False


def scrubbed_env(extra_allow: tuple[str, ...] = ()) -> dict[str, str]:
    """Minimal env for run_command; optional extra keys for structured tools."""
    base_keys = ("PATH", "HOME", "USER", "LANG", "LC_ALL", "TERM")
    env: dict[str, str] = {}
    for k in (*base_keys, *extra_allow):
        if k in os.environ:
            env[k] = os.environ[k]
    # Ensure PATH at least
    env.setdefault("PATH", "/usr/bin:/bin")
    return env


# re-export for secrets tests
def secrets_config_matches(path: str, secrets: ToolsSecretsConfig) -> bool:
    return matches_secrets_globs(path, secrets.path_globs)


def gate(
    policy: PolicyResult,
    *,
    arguments: dict[str, Any],
    approved: bool,
    extra_meta: dict[str, Any] | None = None,
) -> ToolResult | None:
    """Shared policy gate for tool handlers.

    Returns a DENY/approval-required ToolResult, or None if the handler should
    proceed. Collapses the ~15-line block that was copy-pasted across every tool
    (with drift), and builds JSON via ``err_json`` so error text can't corrupt it.
    """
    if policy.decision is PolicyDecision.DENY:
        return ToolResult(
            output=err_json(policy.reason or "denied"),
            is_error=True,
            risk=policy.risk,
            decision="deny",
        )
    if policy.decision is PolicyDecision.PROMPT and not approved:
        meta: dict[str, Any] = {"needs_approval": True, "arguments": arguments}
        if extra_meta:
            meta.update(extra_meta)
        return ToolResult(
            output=err_json("approval_required", reason=policy.reason),
            is_error=True,
            risk=policy.risk,
            decision="prompt",
            meta=meta,
        )
    return None
