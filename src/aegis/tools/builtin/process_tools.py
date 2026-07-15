"""Process and log inspection tools."""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any

from aegis.config.schema import ToolsConfig
from aegis.tools.policy import evaluate_read_file, gate, resolve_tool_path
from aegis.tools.types import ToolResult, ToolSpec, err_json
from aegis.util.secrets import redact_secrets


async def handle_list_processes(
    arguments: dict[str, Any],
    *,
    tools: ToolsConfig,
    approved: bool = False,
    spec: ToolSpec | None = None,
) -> ToolResult:
    from aegis.tools.executor import terminate_process

    pattern = arguments.get("filter") or ""
    proc: asyncio.subprocess.Process | None = None
    try:
        proc = await asyncio.create_subprocess_exec(
            "ps",
            "ax",
            "-o",
            "pid,user,%cpu,%mem,cmd",
            "--sort=-%cpu",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
    except TimeoutError:
        if proc is not None:
            await terminate_process(proc)
        return ToolResult(
            output=err_json("ps_failed", detail="timeout"),
            is_error=True,
            risk="read",
        )
    except asyncio.CancelledError:
        if proc is not None:
            await terminate_process(proc)
        raise
    except Exception as exc:
        if proc is not None and proc.returncode is None:
            await terminate_process(proc)
        return ToolResult(output=err_json("ps_failed", detail=str(exc)), is_error=True, risk="read")
    lines = stdout.decode("utf-8", errors="replace").splitlines()
    if not lines:
        return ToolResult(output="(no processes)", risk="read", decision="auto")
    header = lines[0]
    body = lines[1:]
    if pattern:
        body = [ln for ln in body if pattern.lower() in ln.lower()]
    text = redact_secrets("\n".join([header, *body[:80]]))
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

    target = resolve_tool_path(path, tools)
    if not target.is_file():
        return ToolResult(output=err_json("not_a_file", path=path), is_error=True)
    try:
        # Read only a bounded suffix. A model-controlled request must not turn a
        # multi-gigabyte log into a transient in-process allocation.
        size = target.stat().st_size
        offset = max(0, size - tools.max_output_bytes)
        with target.open("rb") as stream:
            stream.seek(offset)
            data = stream.read(tools.max_output_bytes)
        if offset:
            _, separator, data = data.partition(b"\n")
            if not separator:
                data = b""
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
