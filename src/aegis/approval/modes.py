"""Approval decision helpers for tool calls."""

from __future__ import annotations

import asyncio
import contextlib
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal

from aegis.config.schema import ToolsApprovalConfig
from aegis.tools.types import ToolResult
from aegis.util.logging import get_logger

log = get_logger("approval.modes")

# Implemented scopes only. "same_tool" is an exact argument fingerprint for
# read-risk tools (see tool_loop + registry.grant_session).
GrantScope = Literal["once", "same_tool"]

ApprovalHandler = Callable[["ApprovalRequest"], Awaitable["ApprovalResponse"]]

# Serialize CLI prompts so concurrent tools cannot race stdin.
_cli_approval_lock = asyncio.Lock()


@dataclass(slots=True)
class ApprovalRequest:
    tool_name: str
    summary: str
    risk: str
    call_id: str


@dataclass(slots=True)
class ApprovalResponse:
    allowed: bool
    grant_scope: GrantScope = "once"
    reason: str = ""


async def prompt_cli_approval(
    request: ApprovalRequest,
    config: ToolsApprovalConfig,
) -> ApprovalResponse:
    """CLI prompt with timeout; at most one prompt reads stdin at a time.

    Prefer ``loop.add_reader`` so a timeout removes the reader without leaving a
    blocked ``readline`` thread that steals the next answer (F015).
    """
    async with _cli_approval_lock:
        return await _prompt_cli_unlocked(request, config)


async def _prompt_cli_unlocked(
    request: ApprovalRequest,
    config: ToolsApprovalConfig,
) -> ApprovalResponse:
    print(
        f"\n[Aegis approval] tool={request.tool_name} risk={request.risk}\n"
        f"  {request.summary}\n"
        f"  Allow? [y]es / [n]o / [s]ession (same args, read tools only): ",
        end="",
        file=sys.stderr,
        flush=True,
    )

    loop = asyncio.get_running_loop()
    fut: asyncio.Future[ApprovalResponse] = loop.create_future()

    def _parse_line(line: str) -> ApprovalResponse:
        ans = (line or "").strip().lower()
        if ans in {"y", "yes"}:
            return ApprovalResponse(True, grant_scope="once")
        if ans in {"s", "session"}:
            return ApprovalResponse(True, grant_scope="same_tool")
        return ApprovalResponse(False, reason="user_denied")

    def _on_readable() -> None:
        if fut.done():
            return
        try:
            line = sys.stdin.readline()
        except Exception:
            fut.set_result(ApprovalResponse(False, reason="read_failed"))
            return
        fut.set_result(_parse_line(line))

    fd: int | None = None
    try:
        if sys.stdin.isatty():
            fd = sys.stdin.fileno()
    except (AttributeError, OSError, ValueError):
        fd = None

    if fd is not None:
        try:
            loop.add_reader(fd, _on_readable)
            try:
                return await asyncio.wait_for(fut, timeout=float(config.timeout_s))
            except TimeoutError:
                return ApprovalResponse(False, reason="timeout")
            finally:
                with contextlib.suppress(OSError, RuntimeError, ValueError):
                    loop.remove_reader(fd)
        except (OSError, RuntimeError) as exc:
            log.debug("add_reader failed (%s); falling back to to_thread", exc)

    def _ask() -> ApprovalResponse:
        try:
            line = sys.stdin.readline()
        except Exception:
            return ApprovalResponse(False, reason="read_failed")
        return _parse_line(line)

    try:
        return await asyncio.wait_for(
            asyncio.to_thread(_ask),
            timeout=float(config.timeout_s),
        )
    except TimeoutError:
        return ApprovalResponse(False, reason="timeout")


def denial_payload(reason: str = "denied") -> str:
    """Canonical denial string returned to the model."""
    import json

    return json.dumps({"error": "denied", "reason": reason})


def result_from_denial(reason: str = "denied") -> ToolResult:
    return ToolResult(output=denial_payload(reason), is_error=True, decision="deny")
