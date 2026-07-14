"""Argv-only subprocess executor — never shell=True."""

from __future__ import annotations

import asyncio
import os
import signal
from pathlib import Path

from aegis.config.schema import ToolsConfig
from aegis.tools.policy import evaluate_run_command, scrubbed_env
from aegis.tools.types import PolicyDecision, ToolResult
from aegis.util.logging import get_logger

log = get_logger("tools.executor")


async def run_argv(
    argv: list[str],
    tools: ToolsConfig,
    *,
    timeout_s: int | None = None,
    env_allowlist: tuple[str, ...] = (),
    prechecked: bool = False,
) -> ToolResult:
    """Execute argv under policy (unless prechecked) with scrubbed env."""
    if not prechecked:
        # Reject alternate shapes at boundary
        policy = evaluate_run_command(argv, tools)
        if policy.decision is PolicyDecision.DENY:
            return ToolResult(
                output=f'{{"error":"{policy.reason}"}}',
                is_error=True,
                risk=policy.risk,
                decision="deny",
            )
        if policy.decision is PolicyDecision.PROMPT:
            return ToolResult(
                output=f'{{"error":"approval_required","reason":"{policy.reason}"}}',
                is_error=True,
                risk=policy.risk,
                decision="prompt",
                meta={"needs_approval": True, "argv": policy.resolved_argv or argv},
            )
        argv = policy.resolved_argv or argv
        risk = policy.risk
    else:
        risk = "exec"

    timeout = timeout_s or tools.default_timeout_s
    cwd = str(Path(tools.working_directory).expanduser())
    env = scrubbed_env(env_allowlist)

    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            env=env,
            start_new_session=True,
        )
    except OSError as exc:
        return ToolResult(
            output=f'{{"error":"spawn_failed","detail":"{exc}"}}',
            is_error=True,
            risk=risk,
            decision="auto",
        )

    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError:
        _kill_process_group(proc.pid)
        with contextlib_suppress():
            await proc.wait()
        return ToolResult(
            output='{"error":"timeout"}',
            is_error=True,
            risk=risk,
            decision="auto",
            meta={"timeout_s": timeout},
        )

    out = stdout.decode("utf-8", errors="replace")
    err = stderr.decode("utf-8", errors="replace")
    combined = out
    if err:
        combined = f"{out}\n[stderr]\n{err}" if out else f"[stderr]\n{err}"
    combined = _truncate(combined, tools.max_output_bytes)
    is_error = proc.returncode != 0
    return ToolResult(
        output=combined if combined else f"(exit {proc.returncode})",
        is_error=is_error,
        risk=risk,
        decision="auto",
        meta={"exit_code": proc.returncode, "argv": argv},
    )


def _kill_process_group(pid: int | None) -> None:
    if pid is None:
        return
    try:
        os.killpg(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            os.kill(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            pass


def _truncate(text: str, max_bytes: int) -> str:
    raw = text.encode("utf-8", errors="replace")
    if len(raw) <= max_bytes:
        return text
    cut = raw[: max_bytes - 64]
    return cut.decode("utf-8", errors="replace") + "\n…[truncated]"


class contextlib_suppress:
    def __enter__(self) -> None:
        return None

    def __exit__(self, *exc: object) -> bool:
        return True
