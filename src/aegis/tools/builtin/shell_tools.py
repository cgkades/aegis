"""run_command structured registration (argv-only)."""

from __future__ import annotations

from typing import Any

from aegis.config.schema import ToolsConfig
from aegis.tools.executor import run_argv
from aegis.tools.policy import evaluate_run_command, gate
from aegis.tools.types import (
    RUN_COMMAND_PARAMETERS,
    ToolResult,
    ToolSpec,
    err_json,
)


async def handle_run_command(
    arguments: dict[str, Any],
    *,
    tools: ToolsConfig,
    approved: bool = False,
    spec: ToolSpec | None = None,
) -> ToolResult:
    if set(arguments.keys()) - {"argv"}:
        return ToolResult(output=err_json("argv_only_schema"), is_error=True, decision="deny")
    argv = arguments.get("argv")
    if not isinstance(argv, list):
        return ToolResult(output=err_json("argv_only_schema"), is_error=True, decision="deny")

    policy = evaluate_run_command(argv, tools)
    gated = gate(
        policy,
        arguments=arguments,
        approved=approved,
        extra_meta={"argv": policy.resolved_argv or argv},
    )
    if gated is not None:
        return gated

    return await run_argv(
        policy.resolved_argv or argv,
        tools,
        timeout_s=spec.timeout_s if spec else None,
        prechecked=True,
    )


def validate_run_command_args(arguments: dict[str, Any]) -> str | None:
    """run_command takes argv and nothing else — reject other shapes early."""
    if set(arguments.keys()) - {"argv"} or "argv" not in arguments:
        return err_json("argv_only_schema")
    return None


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
            # Real risk comes from the argv policy: allowlisted read-only
            # commands are auto, everything else prompts or denies.
            dynamic_risk=True,
            validate_args=validate_run_command_args,
            handler=handle_run_command,
        )
    ]
