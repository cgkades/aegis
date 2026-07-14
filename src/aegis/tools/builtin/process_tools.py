"""Process and log inspection tools."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

from aegis.config.schema import ToolsConfig
from aegis.tools.policy import evaluate_read_file, gate
from aegis.tools.types import ToolResult, ToolSpec, err_json


async def handle_list_processes(
    arguments: dict[str, Any],
    *,
    tools: ToolsConfig,
    approved: bool = False,
    spec: ToolSpec | None = None,
) -> ToolResult:
    pattern = arguments.get("filter") or ""
    try:
        proc = await asyncio.create_subprocess_exec(
            "ps",
            "ax",
            "-o",
            "pid,user,%cpu,%mem,cmd",
            "--sort=-%cpu",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
    except Exception as exc:
        return ToolResult(output=err_json("ps_failed", detail=str(exc)), is_error=True, risk="read")
    lines = stdout.decode("utf-8", errors="replace").splitlines()
    if not lines:
        return ToolResult(output="(no processes)", risk="read", decision="auto")
    header = lines[0]
    body = lines[1:]
    if pattern:
        body = [ln for ln in body if pattern.lower() in ln.lower()]
    text = "\n".join([header, *body[:80]])
    return ToolResult(output=text or "(no matches)", risk="read", decision="auto")


async def handle_tail_log(
    arguments: dict[str, Any],
    *,
    tools: ToolsConfig,
    approved: bool = False,
    spec: ToolSpec | None = None,
) -> ToolResult:
    path = arguments.get("path")
    if not isinstance(path, str):
        return ToolResult(output=err_json("path_required"), is_error=True)
    n = int(arguments.get("lines") or 50)
    n = max(1, min(n, 500))

    policy = evaluate_read_file(path, tools)
    if (gated := gate(policy, arguments=arguments, approved=approved)) is not None:
        return gated

    target = Path(path).expanduser()
    if not target.is_file():
        return ToolResult(output=err_json("not_a_file", path=path), is_error=True)
    try:
        # Efficient-ish tail for moderate files
        data = target.read_bytes()
        text = data.decode("utf-8", errors="replace")
        lines = text.splitlines()[-n:]
        return ToolResult(output="\n".join(lines), risk=policy.risk, decision="auto")
    except OSError as exc:
        return ToolResult(
            output=err_json("tail_failed", detail=str(exc)), is_error=True, risk="read"
        )


async def handle_env_info(
    arguments: dict[str, Any],
    *,
    tools: ToolsConfig,
    approved: bool = False,
    spec: ToolSpec | None = None,
) -> ToolResult:
    """Safe environment summary — never dumps secrets."""
    info = {
        "cwd": os.getcwd(),
        "user": os.environ.get("USER"),
        "home": os.environ.get("HOME"),
        "shell": os.environ.get("SHELL"),
        "path_entries": len(os.environ.get("PATH", "").split(":")),
        "has_openai_key": bool(os.environ.get("OPENAI_API_KEY")),
        "has_kubeconfig": bool(os.environ.get("KUBECONFIG")),
    }
    return ToolResult(output=json.dumps(info, indent=2), risk="read", decision="auto")


def process_tool_specs() -> list[ToolSpec]:
    return [
        ToolSpec(
            name="list_processes",
            description="List top processes (ps), optionally filtered by substring.",
            parameters={
                "type": "object",
                "properties": {
                    "filter": {
                        "type": "string",
                        "description": "Case-insensitive substring filter.",
                    }
                },
                "additionalProperties": False,
            },
            risk="read",
            handler=handle_list_processes,
        ),
        ToolSpec(
            name="tail_log",
            description="Read the last N lines of a log/text file (sandboxed).",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "lines": {"type": "integer"},
                },
                "required": ["path"],
                "additionalProperties": False,
            },
            risk="read",
            handler=handle_tail_log,
        ),
        ToolSpec(
            name="env_info",
            description="Safe summary of local environment (no secret values).",
            parameters={"type": "object", "properties": {}, "additionalProperties": False},
            risk="read",
            handler=handle_env_info,
        ),
    ]
