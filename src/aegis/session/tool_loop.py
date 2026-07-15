"""Serial tool loop for voice sessions (with optional CLI approval)."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from aegis.approval import (
    ApprovalRequest,
    ApprovalResponse,
    prompt_cli_approval,
    result_from_denial,
)
from aegis.approval.modes import ApprovalHandler
from aegis.config.schema import AegisConfig
from aegis.session.events import Trigger
from aegis.session.machine import SessionMachine
from aegis.tools.registry import ToolRegistry
from aegis.tools.sanitize import strip_control_sequences, wrap_untrusted
from aegis.tools.types import ToolResult
from aegis.util.logging import get_logger
from aegis.voice.protocol import ToolCallRequest, VoiceSession

log = get_logger("session.tool_loop")

# Keys that identify the target of a tool call — always show first and never
# truncate away. Content/body keys are summarized separately.
_TARGET_KEYS = (
    "path",
    "file",
    "filepath",
    "target",
    "dest",
    "destination",
    "cwd",
    "workdir",
    "working_directory",
    "namespace",
    "context",
    "verb",
    "resource",
    "name",
    "command",
    "argv",
    "url",
    "repo",
    "ref",
)
_BODY_KEYS = ("content", "patch", "body", "data", "text", "input", "yaml", "json")
_SUMMARY_MAX = 500


async def handle_tool_call(
    call: ToolCallRequest,
    *,
    session: VoiceSession,
    registry: ToolRegistry,
    machine: SessionMachine,
    cfg: AegisConfig,
    interactive_approval: bool = True,
    approval_handler: ApprovalHandler | None = None,
) -> ToolResult:
    """Dispatch one tool call; prompt if needed; send result back to voice session.

    Approval resolution order:
    1. ``approval_handler`` (daemon IPC broker)
    2. CLI stdin prompt when ``interactive_approval``
    3. Deny with ``non_interactive_no_approval_ui``
    """
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
            mute_uplink=cfg.tools.approval.mute_uplink_during_approval,
        )
        req = ApprovalRequest(
            tool_name=call.name,
            summary=_approval_summary(call.arguments),
            risk=result.risk or "unknown",
            call_id=call.call_id,
        )
        if approval_handler is not None:
            resp = await approval_handler(req)
        elif interactive_approval:
            resp = await prompt_cli_approval(req, cfg.tools.approval)
        else:
            log.warning(
                "tool %s needs approval but host is non-interactive; denying",
                call.name,
            )
            resp = ApprovalResponse(False, reason="non_interactive_no_approval_ui")

        if not resp.allowed:
            result = result_from_denial(resp.reason or "denied")
            machine.trigger(Trigger.APPROVAL_DENY, tool=call.name)
        else:
            if (
                resp.grant_scope == "same_tool"
                and cfg.tools.approval.session_grant_applies_to.value == "same_tool"
                and result.risk == "read"
            ):
                registry.grant_session(call.name, call.arguments)
            result = await registry.dispatch(
                call.name,
                call.arguments,
                session_id=session_id,
                approved=True,
            )
            machine.trigger(Trigger.APPROVAL_ALLOW, tool=call.name)

    # Wrap every result in untrusted-content delimiters and strip control/ANSI
    # escapes before it goes back to the model. Error outputs can contain stderr,
    # server responses, and filesystem-controlled text just as successful ones can.
    max_bytes = cfg.tools.max_output_bytes
    wire_output = wrap_untrusted(result.output, max_bytes=max_bytes)
    await session.send_tool_result(
        call.call_id,
        wire_output,
        is_error=result.is_error,
    )
    return result


def _approval_summary(arguments: dict[str, Any]) -> str:
    """Render a bounded, path-first summary for the human approver.

    Never bury ``path`` (or other target keys) behind a long ``content`` prefix.
    Body fields are reduced to length + short hash + head snippet.
    """
    if not isinstance(arguments, dict):
        text = strip_control_sequences(str(arguments))
        return text if len(text) <= _SUMMARY_MAX else text[: _SUMMARY_MAX - 1] + "…"

    parts: list[str] = []
    seen: set[str] = set()

    for key in _TARGET_KEYS:
        if key not in arguments:
            continue
        seen.add(key)
        val = arguments[key]
        rendered = strip_control_sequences(
            json.dumps(val, ensure_ascii=False, default=str) if not isinstance(val, str) else val
        )
        # Target keys are never truncated — operator must see the full path/argv.
        parts.append(f"{key}={rendered}")

    for key in _BODY_KEYS:
        if key not in arguments:
            continue
        seen.add(key)
        parts.append(f"{key}={_summarize_body(arguments[key])}")

    for key in sorted(arguments.keys()):
        if key in seen:
            continue
        val = arguments[key]
        if isinstance(val, str) and len(val) > 120:
            parts.append(f"{key}={_summarize_body(val)}")
        else:
            rendered = strip_control_sequences(
                json.dumps(val, ensure_ascii=False, default=str)
            )
            if len(rendered) > 120:
                rendered = rendered[:119] + "…"
            parts.append(f"{key}={rendered}")

    text = strip_control_sequences(" ".join(parts) if parts else "{}")
    if len(text) <= _SUMMARY_MAX:
        return text
    # Prefer keeping target prefixes; trim from the end.
    return text[: _SUMMARY_MAX - 1] + "…"


def _summarize_body(value: object) -> str:
    if value is None:
        return "null"
    if not isinstance(value, str):
        try:
            raw = json.dumps(value, ensure_ascii=False, default=str)
        except TypeError:
            raw = str(value)
    else:
        raw = value
    raw_bytes = raw.encode("utf-8", errors="replace")
    digest = hashlib.sha256(raw_bytes).hexdigest()[:12]
    head = strip_control_sequences(raw[:80].replace("\n", "\\n"))
    if len(raw) > 80:
        head += "…"
    return f"<{len(raw_bytes)}B sha256={digest} head={head!r}>"
