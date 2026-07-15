"""ToolRegistry audit + hard-deny after approved=True."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from aegis.audit import AuditLogger
from aegis.config import build_config
from aegis.config.schema import ToolsConfig
from aegis.tools.builtin.shell_tools import handle_run_command
from aegis.tools.factory import build_registry
from aegis.tools.registry import ToolRegistry
from aegis.tools.types import ToolResult, ToolSpec


@pytest.mark.asyncio
async def test_sandbox_deny_is_audited(tmp_path: Path) -> None:
    audit_dir = tmp_path / "audit"
    audit = AuditLogger(audit_dir, redact=False)
    cfg = build_config(
        {
            "tools": {
                "working_directory": str(tmp_path / "work"),
                "sandbox_to_workdir": True,
                "enabled": ["fs"],
            }
        }
    )
    (tmp_path / "work").mkdir()
    reg = build_registry(cfg, audit=audit)
    result = await reg.dispatch("read_file", {"path": "/etc/passwd"})
    assert result.is_error
    assert result.decision == "deny"

    files = list(audit_dir.glob("*.jsonl"))
    assert files
    lines = files[0].read_text(encoding="utf-8").strip().splitlines()
    assert lines
    rec = json.loads(lines[-1])
    assert rec.get("event_type") == "tool_call"
    assert rec.get("tool_name") == "read_file"
    assert rec.get("decision") == "deny"


@pytest.mark.asyncio
async def test_shell_hard_deny_even_when_approved(tmp_path: Path) -> None:
    tools = ToolsConfig(
        working_directory=str(tmp_path),
        sandbox_to_workdir=True,
        shell={"enabled": True},  # type: ignore[arg-type]
    )
    # pydantic may need proper nested
    from aegis.config.schema import ToolsShellConfig

    tools = ToolsConfig(
        working_directory=str(tmp_path),
        sandbox_to_workdir=True,
        shell=ToolsShellConfig(enabled=True),
        enabled=["fs", "shell"],
    )
    with patch("aegis.tools.executor.asyncio.create_subprocess_exec") as spawn:
        r = await handle_run_command(
            {"argv": ["cat", "/etc/passwd"]},
            tools=tools,
            approved=True,
        )
    assert r.is_error
    assert r.decision == "deny"
    spawn.assert_not_called()


@pytest.mark.asyncio
async def test_reserved_kubectl_via_absolute_path_denied(tmp_path: Path) -> None:
    from aegis.config.schema import ToolsShellConfig
    from aegis.tools.policy import evaluate_run_command

    tools = ToolsConfig(
        working_directory=str(tmp_path),
        shell=ToolsShellConfig(enabled=True),
    )
    # Even with absolute path, basename after resolve should deny reserved.
    with patch("aegis.tools.policy._resolve_executable", return_value="/usr/bin/kubectl"):
        r = evaluate_run_command(["/usr/bin/kubectl", "get", "pods"], tools)
    assert r.decision.value == "deny"
    assert "kubectl" in r.reason or "reserved" in r.reason


@pytest.mark.asyncio
async def test_max_tool_calls_early_return() -> None:
    from aegis.config.schema import ToolsConfig

    tools = ToolsConfig(max_tool_calls_per_turn=1)
    reg = ToolRegistry(tools)

    async def handler(arguments, **kwargs):
        return ToolResult(output="ok")

    reg.register(
        ToolSpec(
            name="noop",
            description="n",
            parameters={"type": "object", "properties": {}},
            risk="read",
            handler=handler,
        )
    )
    assert not (await reg.dispatch("noop", {})).is_error
    second = await reg.dispatch("noop", {})
    assert second.is_error
    assert "max_tool_calls" in second.output
