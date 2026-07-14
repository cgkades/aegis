"""Serial tool loop for voice sessions (with optional CLI approval)."""

from __future__ import annotations

from aegis.approval import (
    ApprovalRequest,
    prompt_cli_approval,
    result_from_denial,
)
from aegis.config.schema import AegisConfig
from aegis.session.events import Trigger
from aegis.session.machine import SessionMachine
from aegis.tools.registry import ToolRegistry
from aegis.tools.sanitize import wrap_untrusted
from aegis.tools.types import ToolResult
from aegis.util.logging import get_logger
from aegis.voice.protocol import ToolCallRequest, VoiceSession

log = get_logger("session.tool_loop")


async def handle_tool_call(
    call: ToolCallRequest,
    *,
    session: VoiceSession,
    registry: ToolRegistry,
    machine: SessionMachine,
    cfg: AegisConfig,
    interactive_approval: bool = True,
) -> ToolResult:
    """Dispatch one tool call; prompt if needed; send result back to voice session."""
    session_id = machine.context.session_id
    result = await registry.dispatch(
        call.name,
        call.arguments,
        session_id=session_id,
        approved=False,
    )

    if result.meta.get("needs_approval"):
        machine.trigger(
            Trigger.TOOL_NEEDS_APPROVAL,
            tool=call.name,
            call_id=call.call_id,
        )
        if interactive_approval:
            resp = await prompt_cli_approval(
                ApprovalRequest(
                    tool_name=call.name,
                    summary=str(call.arguments)[:300],
                    risk=result.risk or "unknown",
                    call_id=call.call_id,
                ),
                cfg.tools.approval,
            )
        else:
            from aegis.approval.modes import ApprovalResponse

            resp = ApprovalResponse(False, reason="non_interactive")

        if not resp.allowed:
            result = result_from_denial(resp.reason or "denied")
            machine.trigger(Trigger.APPROVAL_DENY, tool=call.name)
        else:
            if resp.grant_scope == "same_tool":
                registry.grant_session(call.name)
            result = await registry.dispatch(
                call.name,
                call.arguments,
                session_id=session_id,
                approved=True,
            )
            machine.trigger(Trigger.APPROVAL_ALLOW, tool=call.name)

    # Wrap the result in untrusted-content delimiters and strip control/ANSI
    # escapes before it goes back to the model — tool output (shell stdout, file
    # contents, MCP responses) is attacker-influenced and may carry injected
    # instructions. Error payloads we construct ourselves are already trusted JSON,
    # so only data outputs are wrapped.
    max_bytes = cfg.tools.max_output_bytes
    wire_output = result.output if result.is_error else wrap_untrusted(
        result.output, max_bytes=max_bytes
    )
    await session.send_tool_result(
        call.call_id,
        wire_output,
        is_error=result.is_error,
    )
    return result
