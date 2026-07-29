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
from aegis.tools.sanitize import (
    escape_line_breaks,
    strip_control_sequences,
    wrap_untrusted,
)
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
# Target keys (path/argv/url/…) get their own budget on top of _SUMMARY_MAX so
# the operator always sees the full blast radius, not a prefix of it.
_TARGET_MAX = 400


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

        # Record the human decision. A denial never reaches registry.dispatch,
        # and an allow is indistinguishable there from an unattended auto-run,
        # so without this the audit trail cannot answer "who said yes?".
        if registry.audit is not None:
            registry.audit.log(
                "approval_resolved",
                session_id=session_id,
                tool_name=call.name,
                decision="allow" if resp.allowed else "deny",
                risk=result.risk or "unknown",
                args_summary=_approval_summary(call.arguments),
                call_id=call.call_id,
                reason=resp.reason or None,
                grant_scope=resp.grant_scope or None,
                source=(
                    "ipc"
                    if approval_handler is not None
                    else ("cli" if interactive_approval else "auto_deny")
                ),
            )

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
        text = _render_value(str(arguments))
        return text if len(text) <= _SUMMARY_MAX else text[: _SUMMARY_MAX - 1] + "…"

    target_parts: list[str] = []
    other_parts: list[str] = []
    seen: set[str] = set()

    for key in _TARGET_KEYS:
        if key not in arguments:
            continue
        seen.add(key)
        val = arguments[key]
        rendered = _render_value(
            json.dumps(val, ensure_ascii=False, default=str) if not isinstance(val, str) else val
        )
        # Target keys carry the blast radius, so they get their own generous
        # budget instead of competing with body fields for one 500-char cap —
        # a long path used to be cut mid-string, letting an operator approve on
        # a prefix.
        if len(rendered) > _TARGET_MAX:
            rendered = f"{rendered[:_TARGET_MAX]}…[+{len(rendered) - _TARGET_MAX} chars]"
        target_parts.append(f"{key}={rendered}")

    for key in _BODY_KEYS:
        if key not in arguments:
            continue
        seen.add(key)
        other_parts.append(f"{key}={_summarize_body(arguments[key])}")

    for key in sorted(arguments.keys()):
        if key in seen:
            continue
        val = arguments[key]
        if isinstance(val, str) and len(val) > 120:
            other_parts.append(f"{key}={_summarize_body(val)}")
        else:
            rendered = _render_value(json.dumps(val, ensure_ascii=False, default=str))
            if len(rendered) > 120:
                rendered = rendered[:119] + "…"
            other_parts.append(f"{key}={rendered}")

    if not target_parts and not other_parts:
        return "{}"

    targets = " ".join(target_parts)
    remaining = max(0, _SUMMARY_MAX - len(targets))
    tail = " ".join(other_parts)
    if len(tail) > remaining:
        tail = tail[: max(0, remaining - 1)] + "…" if remaining else ""
    return " ".join(part for part in (targets, tail) if part)


def _render_value(text: str) -> str:
    """Make a model-chosen value safe to print in the operator's prompt.

    Stripping control characters is not enough on its own: newline, carriage
    return and tab survive that pass, and the approval prompt is printed
    verbatim to the terminal. A value containing "\\n  Allow? [y]es: y\\n" can
    forge a second approval prompt, and a bare "\\r" rewrites the line the
    operator is reading.
    """
    return escape_line_breaks(strip_control_sequences(text))


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
