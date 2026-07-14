"""Structured git tools (preferred over shell git)."""

from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path
from typing import Any

from aegis.config.schema import ToolsConfig
from aegis.tools.executor import _kill_process_group
from aegis.tools.policy import path_within_workdir, scrubbed_env
from aegis.tools.types import ToolResult, ToolSpec, err_json


def _resolve_cwd(arguments: dict[str, Any], tools: ToolsConfig) -> str | ToolResult:
    """Resolve the repo path, enforcing the workdir sandbox (like the file tools)."""
    path = str(Path(arguments.get("path") or tools.working_directory).expanduser())
    if not path_within_workdir(path, tools):
        return ToolResult(
            output=err_json("sandbox"), is_error=True, risk="read", decision="deny"
        )
    return path

# git needs only PATH/HOME (plus these) — never the full secret-bearing env.
_GIT_ENV_ALLOW = ("GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM", "XDG_CONFIG_HOME")


async def _git(args: list[str], cwd: str, timeout: int = 30) -> ToolResult:
    # Scrubbed env so git children (and any hook/credential helper) don't inherit
    # OPENAI_API_KEY / AWS creds. Disable credential prompts and helpers so a repo
    # can't exfiltrate or hang on auth.
    env = scrubbed_env(_GIT_ENV_ALLOW)
    env["GIT_TERMINAL_PROMPT"] = "0"
    git_args = ["-c", "credential.helper=", *args]
    try:
        proc = await asyncio.create_subprocess_exec(
            "git",
            *git_args,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            start_new_session=True,
        )
    except FileNotFoundError:
        return ToolResult(output='{"error":"git_not_found"}', is_error=True, risk="read")
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError:
        _kill_process_group(proc.pid)
        with contextlib.suppress(Exception):
            await proc.wait()
        return ToolResult(output='{"error":"timeout"}', is_error=True, risk="read")
    out = stdout.decode("utf-8", errors="replace")
    err = stderr.decode("utf-8", errors="replace")
    text = out if out else err
    if proc.returncode != 0:
        return ToolResult(
            output=text or f"git exit {proc.returncode}",
            is_error=True,
            risk="read",
        )
    return ToolResult(output=text or "(ok)", risk="read", decision="auto")


async def handle_git_status(
    arguments: dict[str, Any],
    *,
    tools: ToolsConfig,
    approved: bool = False,
    spec: ToolSpec | None = None,
) -> ToolResult:
    cwd = _resolve_cwd(arguments, tools)
    if isinstance(cwd, ToolResult):
        return cwd
    return await _git(["status", "--short", "--branch"], cwd)


async def handle_git_diff(
    arguments: dict[str, Any],
    *,
    tools: ToolsConfig,
    approved: bool = False,
    spec: ToolSpec | None = None,
) -> ToolResult:
    cwd = _resolve_cwd(arguments, tools)
    if isinstance(cwd, ToolResult):
        return cwd
    staged = bool(arguments.get("staged"))
    args = ["diff", "--stat"] if not arguments.get("full") else ["diff"]
    if staged:
        args.insert(1, "--cached")
    result = await _git(args, cwd)
    if len(result.output) > tools.max_output_bytes:
        result.output = result.output[: tools.max_output_bytes] + "\n…[truncated]"
    return result


async def handle_git_log(
    arguments: dict[str, Any],
    *,
    tools: ToolsConfig,
    approved: bool = False,
    spec: ToolSpec | None = None,
) -> ToolResult:
    cwd = _resolve_cwd(arguments, tools)
    if isinstance(cwd, ToolResult):
        return cwd
    n = int(arguments.get("n") or 10)
    n = max(1, min(n, 50))
    return await _git(["log", f"-n{n}", "--oneline", "--decorate"], cwd)


async def handle_git_commit(
    arguments: dict[str, Any],
    *,
    tools: ToolsConfig,
    approved: bool = False,
    spec: ToolSpec | None = None,
) -> ToolResult:
    if not tools.git.allow_commit:
        return ToolResult(
            output='{"error":"git_commit_disabled"}',
            is_error=True,
            risk="write",
            decision="deny",
        )
    if not approved:
        return ToolResult(
            output='{"error":"approval_required","reason":"git_commit"}',
            is_error=True,
            risk="write",
            decision="prompt",
            meta={"needs_approval": True, "arguments": arguments},
        )
    message = arguments.get("message")
    if not isinstance(message, str) or not message.strip():
        return ToolResult(output=err_json("message_required"), is_error=True, risk="write")
    cwd = _resolve_cwd(arguments, tools)
    if isinstance(cwd, ToolResult):
        return cwd
    if arguments.get("add_all"):
        add_result = await _git(["add", "-A"], cwd)
        if add_result.is_error:
            return add_result
    return await _git(["commit", "-m", message], cwd)


def git_tool_specs() -> list[ToolSpec]:
    return [
        ToolSpec(
            name="git_status",
            description="Show git status (short) for a repository path.",
            parameters={
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "additionalProperties": False,
            },
            risk="read",
            handler=handle_git_status,
        ),
        ToolSpec(
            name="git_diff",
            description="Show git diff summary (or full diff if full=true).",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "staged": {"type": "boolean"},
                    "full": {"type": "boolean"},
                },
                "additionalProperties": False,
            },
            risk="read",
            handler=handle_git_diff,
        ),
        ToolSpec(
            name="git_log",
            description="Show recent git commits (oneline).",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "n": {"type": "integer", "description": "Number of commits (max 50)."},
                },
                "additionalProperties": False,
            },
            risk="read",
            handler=handle_git_log,
        ),
        ToolSpec(
            name="git_commit",
            description="Create a git commit (requires allow_commit + approval).",
            parameters={
                "type": "object",
                "properties": {
                    "message": {"type": "string"},
                    "path": {"type": "string"},
                    "add_all": {"type": "boolean"},
                },
                "required": ["message"],
                "additionalProperties": False,
            },
            risk="write",
            handler=handle_git_commit,
        ),
    ]
