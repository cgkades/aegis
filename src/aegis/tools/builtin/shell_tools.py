"""run_command structured registration (argv-only)."""

from __future__ import annotations

from typing import Any

from aegis.config.schema import ToolsConfig
from aegis.tools.executor import run_argv
from aegis.tools.policy import evaluate_run_command
from aegis.tools.types import (
    RUN_COMMAND_PARAMETERS,
    PolicyDecision,
    ToolResult,
    ToolSpec,
)


async def handle_run_command(
    arguments: dict[str, Any],
    *,
    tools: ToolsConfig,
    approved: bool = False,
    spec: ToolSpec | None = None,
) -> ToolResult:
    if set(arguments.keys()) - {"argv"}:
        return ToolResult(
            output='{"error":"argv_only_schema"}',
            is_error=True,
            decision="deny",
        )
    argv = arguments.get("argv")
    if not isinstance(argv, list):
        return ToolResult(
            output='{"error":"argv_only_schema"}',
            is_error=True,
            decision="deny",
        )

    policy = evaluate_run_command(argv, tools)
    if policy.decision is PolicyDecision.DENY:
        return ToolResult(
            output=f'{{"error":"{policy.reason}"}}',
            is_error=True,
            risk=policy.risk,
            decision="deny",
        )
    if policy.decision is PolicyDecision.PROMPT and not approved:
        return ToolResult(
            output=f'{{"error":"approval_required","reason":"{policy.reason}"}}',
            is_error=True,
            risk=policy.risk,
            decision="prompt",
            meta={
                "needs_approval": True,
                "argv": policy.resolved_argv or argv,
                "arguments": arguments,
            },
        )

    return await run_argv(
        policy.resolved_argv or argv,
        tools,
        timeout_s=spec.timeout_s if spec else None,
        prechecked=True,
    )


def shell_tool_specs() -> list[ToolSpec]:
    return [
        ToolSpec(
            name="run_command",
            description=(
                "Run a local program as an argv array (no shell). "
                "Prefer structured tools when available."
            ),
            parameters=RUN_COMMAND_PARAMETERS,
            risk="exec",
            handler=handle_run_command,
        )
    ]
