"""Argv-only subprocess executor — never shell=True."""

from __future__ import annotations

import asyncio
import os
import signal
from pathlib import Path

from aegis.config.schema import ToolsConfig
from aegis.tools.policy import evaluate_run_command, scrubbed_env
from aegis.tools.types import PolicyDecision, ToolResult, err_json
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
                output=err_json(policy.reason or "denied"),
                is_error=True,
                risk=policy.risk,
                decision="deny",
            )
        if policy.decision is PolicyDecision.PROMPT:
            return ToolResult(
                output=err_json("approval_required", reason=policy.reason),
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
            output=err_json("spawn_failed", detail=str(exc)),
            is_error=True,
            risk=risk,
            decision="auto",
        )

    try:
        stdout, stderr, truncated = await asyncio.wait_for(
            _read_streams_capped(proc, tools.max_output_bytes),
            timeout=timeout,
        )
    except TimeoutError:
        await terminate_process(proc)
        return ToolResult(
            output='{"error":"timeout"}',
            is_error=True,
            risk=risk,
            decision="auto",
            meta={"timeout_s": timeout},
        )
    except asyncio.CancelledError:
        await terminate_process(proc)
        raise
    except BaseException:
        await terminate_process(proc)
        raise

    out = stdout.decode("utf-8", errors="replace")
    err = stderr.decode("utf-8", errors="replace")
    combined = out
    if err:
        combined = f"{out}\n[stderr]\n{err}" if out else f"[stderr]\n{err}"
    if truncated:
        combined = _truncate(combined, tools.max_output_bytes)
        if "…[truncated]" not in combined:
            combined += "\n…[truncated]"
    else:
        combined = _truncate(combined, tools.max_output_bytes)
    is_error = proc.returncode != 0
    return ToolResult(
        output=combined if combined else f"(exit {proc.returncode})",
        is_error=is_error,
        risk=risk,
        decision="auto",
        meta={"exit_code": proc.returncode, "argv": argv, "truncated": truncated},
    )


async def _read_streams_capped(
    proc: asyncio.subprocess.Process,
    max_bytes: int,
) -> tuple[bytes, bytes, bool]:
    """Read stdout/stderr with a combined hard byte cap; kill if exceeded."""
    assert proc.stdout is not None and proc.stderr is not None
    out_buf = bytearray()
    err_buf = bytearray()
    total = 0
    truncated = False
    chunk_size = 8192

    async def _pump(stream: asyncio.StreamReader, store: bytearray) -> None:
        nonlocal total, truncated
        while True:
            chunk = await stream.read(chunk_size)
            if not chunk:
                return
            if truncated:
                continue
            room = max_bytes - total
            if room <= 0:
                truncated = True
                _kill_process_group(proc.pid)
                continue
            if len(chunk) > room:
                store.extend(chunk[:room])
                total += room
                truncated = True
                _kill_process_group(proc.pid)
            else:
                store.extend(chunk)
                total += len(chunk)

    await asyncio.gather(
        _pump(proc.stdout, out_buf),
        _pump(proc.stderr, err_buf),
        proc.wait(),
    )
    return bytes(out_buf), bytes(err_buf), truncated


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


async def terminate_process(
    proc: asyncio.subprocess.Process,
    *,
    cleanup_timeout_s: float = 1.0,
) -> None:
    """Kill a process group and boundedly drain its pipes.

    ``Process.wait()`` can remain pending after a cancelled ``communicate()`` if
    buffered stdout/stderr is no longer being consumed. Draining both streams
    keeps cancellation and timeout cleanup from wedging the serial tool loop.
    """
    _kill_process_group(proc.pid)

    async def _drain(stream: asyncio.StreamReader | None) -> None:
        if stream is not None:
            await stream.read()

    async def _wait_and_drain() -> None:
        await asyncio.gather(
            proc.wait(),
            _drain(getattr(proc, "stdout", None)),
            _drain(getattr(proc, "stderr", None)),
            return_exceptions=True,
        )

    try:
        await asyncio.wait_for(_wait_and_drain(), timeout=cleanup_timeout_s)
    except TimeoutError:
        log.warning("process cleanup timed out pid=%s", proc.pid)


def _truncate(text: str, max_bytes: int) -> str:
    raw = text.encode("utf-8", errors="replace")
    if len(raw) <= max_bytes:
        return text
    cut = raw[: max_bytes - 64]
    return cut.decode("utf-8", errors="replace") + "\n…[truncated]"
