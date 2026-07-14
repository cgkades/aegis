"""Build a ToolRegistry from config (enabled packs)."""

from __future__ import annotations

from aegis.audit import AuditLogger
from aegis.config.schema import DEFAULT_READ_SHELL_RULES, AegisConfig, ShellRule
from aegis.tools.builtin.fs_tools import fs_tool_specs
from aegis.tools.builtin.git_tools import git_tool_specs
from aegis.tools.builtin.process_tools import process_tool_specs
from aegis.tools.builtin.shell_tools import shell_tool_specs
from aegis.tools.builtin.write_tools import write_tool_specs
from aegis.tools.oncall.kubectl_tools import kubectl_tool_specs
from aegis.tools.registry import ToolRegistry


def build_registry(
    cfg: AegisConfig,
    *,
    audit: AuditLogger | None = None,
) -> ToolRegistry:
    tools = cfg.tools
    if tools.shell.enabled and not tools.shell.rules:
        tools = tools.model_copy(
            update={
                "shell": tools.shell.model_copy(
                    update={"rules": list(DEFAULT_READ_SHELL_RULES)}
                )
            }
        )

    reg = ToolRegistry(tools, audit=audit)
    enabled = set(tools.enabled)

    if "fs" in enabled:
        for spec in fs_tool_specs():
            reg.register(spec)

    if "git" in enabled and tools.git.enabled:
        for spec in git_tool_specs():
            # Hide commit tool registration if commit disabled? still register with deny
            reg.register(spec)

    if "process" in enabled:
        for spec in process_tool_specs():
            reg.register(spec)

    if "write" in enabled:
        for spec in write_tool_specs():
            reg.register(spec)

    if "kubectl" in enabled and tools.kubectl.enabled:
        for spec in kubectl_tool_specs():
            reg.register(spec)

    if tools.shell.enabled:
        for spec in shell_tool_specs():
            reg.register(spec)

    return reg


def default_read_shell_rules() -> list[ShellRule]:
    return list(DEFAULT_READ_SHELL_RULES)
