"""Approval decision helpers for tool calls."""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass
from typing import Literal

from aegis.config.schema import ToolsApprovalConfig
from aegis.tools.types import ToolResult

GrantScope = Literal["once", "same_tool", "same_risk_class", "all"]


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
    """Blocking-style CLI prompt with timeout (async-friendly via to_thread)."""

    def _ask() -> ApprovalResponse:
        print(
            f"\n[Aegis approval] tool={request.tool_name} risk={request.risk}\n"
            f"  {request.summary}\n"
            f"  Allow? [y]es / [n]o / [s]ession (same tool): ",
            end="",
            file=sys.stderr,
            flush=True,
        )
        try:
            line = sys.stdin.readline()
        except Exception:
            return ApprovalResponse(False, reason="read_failed")
        ans = (line or "").strip().lower()
        if ans in {"y", "yes"}:
            return ApprovalResponse(True, grant_scope="once")
        if ans in {"s", "session"}:
            return ApprovalResponse(True, grant_scope="same_tool")
        return ApprovalResponse(False, reason="user_denied")

    try:
        return await asyncio.wait_for(
            asyncio.to_thread(_ask),
            timeout=float(config.timeout_s),
        )
    except TimeoutError:
        return ApprovalResponse(False, reason="timeout")


def denial_payload(reason: str = "denied") -> str:
    """Canonical denial string returned to the model."""
    return f'{{"error":"denied","reason":"{reason}"}}'


def result_from_denial(reason: str = "denied") -> ToolResult:
    return ToolResult(output=denial_payload(reason), is_error=True, decision="deny")
