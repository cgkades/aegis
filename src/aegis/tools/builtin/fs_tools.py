"""Structured filesystem tools (Phase 0 / MVP)."""

from __future__ import annotations

import json
from typing import Any

from aegis.config.schema import ToolsConfig
from aegis.tools.policy import (
    evaluate_list_dir,
    evaluate_read_file,
    gate,
    path_inside_workdir,
    resolve_tool_path,
)
from aegis.tools.types import ToolResult, ToolSpec, err_json


async def handle_list_dir(
    arguments: dict[str, Any],
    *,
    tools: ToolsConfig,
    approved: bool = False,
    spec: ToolSpec | None = None,
) -> ToolResult:
    # Default to configured workdir (not process CWD).
    path = arguments.get("path") or tools.working_directory
    if not isinstance(path, str):
        return ToolResult(output=err_json("invalid_path"), is_error=True)
    policy = evaluate_list_dir(path, tools)
    if (gated := gate(policy, arguments=arguments, approved=approved)) is not None:
        return gated

    target = resolve_tool_path(path, tools)
    if not target.exists():
        return ToolResult(output=err_json("not_found", path=path), is_error=True)
    if not target.is_dir():
        return ToolResult(output=err_json("not_a_directory", path=path), is_error=True)

    entries = []
    try:
        for child in sorted(target.iterdir(), key=lambda p: p.name.lower()):
            kind = "dir" if child.is_dir() else "file"
            entries.append({"name": child.name, "type": kind})
    except OSError as exc:
        return ToolResult(output=err_json("list_failed", detail=str(exc)), is_error=True)

    text = json.dumps({"path": str(target), "entries": entries[:500]}, indent=2)
    if len(entries) > 500:
        text += "\n…[truncated listing]"
    return ToolResult(output=text, risk=policy.risk, decision="auto")


async def handle_read_file(
    arguments: dict[str, Any],
    *,
    tools: ToolsConfig,
    approved: bool = False,
    spec: ToolSpec | None = None,
) -> ToolResult:
    path = arguments.get("path")
    if not isinstance(path, str) or not path:
        return ToolResult(output=err_json("invalid_path"), is_error=True)
    requested = int(arguments.get("max_bytes") or min(tools.max_output_bytes, 50_000))
    max_bytes = max(1, min(requested, tools.max_output_bytes))

    policy = evaluate_read_file(path, tools)
    if (gated := gate(policy, arguments=arguments, approved=approved)) is not None:
        return gated

    target = resolve_tool_path(path, tools)
    if not target.is_file():
        return ToolResult(output=err_json("not_a_file", path=path), is_error=True)

    try:
        marker = "\n…[truncated]"
        file_size = target.stat().st_size
        read_limit = max_bytes if file_size <= max_bytes else max_bytes - len(marker.encode())
        with target.open("rb") as stream:
            data = stream.read(max(0, read_limit))
        text = data.decode("utf-8", errors="replace")
        if file_size > max_bytes:
            text += marker
    except OSError as exc:
        return ToolResult(output=err_json("read_failed", detail=str(exc)), is_error=True)

    return ToolResult(output=text, risk=policy.risk, decision="auto")


async def handle_search_files(
    arguments: dict[str, Any],
    *,
    tools: ToolsConfig,
    approved: bool = False,
    spec: ToolSpec | None = None,
) -> ToolResult:
    """Simple recursive filename glob under workdir (not full content search)."""
    pattern = arguments.get("pattern") or "*"
    root = arguments.get("path") or tools.working_directory
    if not isinstance(pattern, str) or not isinstance(root, str):
        return ToolResult(output=err_json("invalid_args"), is_error=True)

    policy = evaluate_list_dir(root, tools)
    if (gated := gate(policy, arguments=arguments, approved=approved)) is not None:
        return gated

    base = resolve_tool_path(root, tools)
    matches: list[str] = []
    try:
        for p in base.rglob(pattern):
            # Skip directory-symlink escapes: only report files whose resolved
            # path remains inside the sandbox workdir.
            try:
                if not p.is_file():
                    continue
                resolved = p.resolve()
            except OSError:
                continue
            if not path_inside_workdir(resolved, tools):
                continue
            matches.append(str(resolved))
            if len(matches) >= 200:
                break
    except OSError as exc:
        return ToolResult(output=err_json("search_failed", detail=str(exc)), is_error=True)

    return ToolResult(
        output=json.dumps({"matches": matches}, indent=2),
        risk="read",
        decision="auto",
    )


def fs_tool_specs() -> list[ToolSpec]:
    return [
        ToolSpec(
            name="list_dir",
            description="List files and directories at a path (relative to working directory).",
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Directory path (default: working directory).",
                    }
                },
                "additionalProperties": False,
            },
            risk="read",
            handler=handle_list_dir,
        ),
        ToolSpec(
            name="read_file",
            description="Read a text file (truncated). Secrets paths require approval.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path to read."},
                    "max_bytes": {
                        "type": "integer",
                        "description": "Max bytes to return (default capped).",
                    },
                },
                "required": ["path"],
                "additionalProperties": False,
            },
            risk="read",
            handler=handle_read_file,
        ),
        ToolSpec(
            name="search_files",
            description="Find files by glob pattern under a root directory.",
            parameters={
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Glob pattern, e.g. '*.py' or '**/config.toml'.",
                    },
                    "path": {
                        "type": "string",
                        "description": "Root directory (default: working directory).",
                    },
                },
                "required": ["pattern"],
                "additionalProperties": False,
            },
            risk="read",
            handler=handle_search_files,
        ),
    ]
