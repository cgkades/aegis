"""File write / patch tools (always require approval)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from aegis.config.schema import ToolsConfig
from aegis.tools.policy import evaluate_read_file, matches_secrets_globs
from aegis.tools.types import PolicyDecision, ToolResult, ToolSpec, err_json


async def handle_write_file(
    arguments: dict[str, Any],
    *,
    tools: ToolsConfig,
    approved: bool = False,
    spec: ToolSpec | None = None,
) -> ToolResult:
    path = arguments.get("path")
    content = arguments.get("content")
    if not isinstance(path, str) or not isinstance(content, str):
        return ToolResult(output=err_json("path_and_content_required"), is_error=True)

    # Reuse path sandbox rules — honor ANY deny reason, not just "sandbox".
    policy = evaluate_read_file(path, tools)
    if policy.decision is PolicyDecision.DENY:
        return ToolResult(
            output=err_json(policy.reason or "denied"),
            is_error=True,
            risk="write",
            decision="deny",
        )
    if matches_secrets_globs(
        str(Path(path).expanduser().resolve()),
        tools.secrets.path_globs,
    ):
        return ToolResult(
            output='{"error":"secrets_path_write_denied"}',
            is_error=True,
            risk="secrets",
            decision="deny",
        )

    if not approved:
        return ToolResult(
            output='{"error":"approval_required","reason":"write_file"}',
            is_error=True,
            risk="write",
            decision="prompt",
            meta={"needs_approval": True, "arguments": {"path": path, "bytes": len(content)}},
        )

    target = Path(path).expanduser()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    except OSError as exc:
        return ToolResult(
            output=err_json("write_failed", detail=str(exc)), is_error=True, risk="write"
        )
    return ToolResult(
        output=f"wrote {len(content)} bytes to {target}",
        risk="write",
        decision="auto",
    )


async def handle_apply_patch(
    arguments: dict[str, Any],
    *,
    tools: ToolsConfig,
    approved: bool = False,
    spec: ToolSpec | None = None,
) -> ToolResult:
    """Simple search-replace patch on a file."""
    path = arguments.get("path")
    old = arguments.get("old")
    new = arguments.get("new")
    if not all(isinstance(x, str) for x in (path, old, new)):
        return ToolResult(output='{"error":"path_old_new_required"}', is_error=True)
    assert isinstance(path, str) and isinstance(old, str) and isinstance(new, str)

    policy = evaluate_read_file(path, tools)
    if policy.decision is PolicyDecision.DENY:
        return ToolResult(
            output=err_json(policy.reason or "denied"),
            is_error=True,
            risk="write",
            decision="deny",
        )
    if not approved:
        return ToolResult(
            output='{"error":"approval_required","reason":"apply_patch"}',
            is_error=True,
            risk="write",
            decision="prompt",
            meta={"needs_approval": True, "arguments": {"path": path}},
        )

    target = Path(path).expanduser()
    try:
        text = target.read_text(encoding="utf-8")
    except OSError as exc:
        return ToolResult(
            output=err_json("read_failed", detail=str(exc)), is_error=True, risk="write"
        )
    if old not in text:
        return ToolResult(output='{"error":"old_string_not_found"}', is_error=True, risk="write")
    count = text.count(old)
    if count > 1 and not arguments.get("replace_all"):
        return ToolResult(
            output='{"error":"multiple_matches","count":' + str(count) + "}",
            is_error=True,
            risk="write",
        )
    updated = text.replace(old, new) if arguments.get("replace_all") else text.replace(old, new, 1)
    try:
        target.write_text(updated, encoding="utf-8")
    except OSError as exc:
        return ToolResult(
            output=err_json("write_failed", detail=str(exc)), is_error=True, risk="write"
        )
    return ToolResult(output=f"patched {target}", risk="write", decision="auto")


def write_tool_specs() -> list[ToolSpec]:
    return [
        ToolSpec(
            name="write_file",
            description=(
                "Write full file contents (always requires approval; "
                "secrets paths denied)."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
                "additionalProperties": False,
            },
            risk="write",
            handler=handle_write_file,
        ),
        ToolSpec(
            name="apply_patch",
            description="Replace old string with new in a file (approval required).",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "old": {"type": "string"},
                    "new": {"type": "string"},
                    "replace_all": {"type": "boolean"},
                },
                "required": ["path", "old", "new"],
                "additionalProperties": False,
            },
            risk="write",
            handler=handle_apply_patch,
        ),
    ]
