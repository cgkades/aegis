"""Tool registry and dispatch."""

from __future__ import annotations

from typing import Any

from aegis.audit import AuditLogger
from aegis.config.schema import ToolsConfig
from aegis.tools.types import (
    ToolResult,
    ToolSpec,
)
from aegis.util.logging import get_logger

log = get_logger("tools.registry")


class ToolRegistry:
    def __init__(
        self,
        tools_config: ToolsConfig,
        *,
        audit: AuditLogger | None = None,
    ) -> None:
        self.tools_config = tools_config
        self.audit = audit
        self._specs: dict[str, ToolSpec] = {}
        self._session_calls = 0
        self._turn_calls = 0
        self._session_grants: set[str] = set()  # tool names granted for session

    def register(self, spec: ToolSpec) -> None:
        self._specs[spec.name] = spec

    def get(self, name: str) -> ToolSpec | None:
        return self._specs.get(name)

    def names(self) -> list[str]:
        return sorted(self._specs)

    def openai_function_schemas(self) -> list[dict[str, Any]]:
        out = []
        for spec in self._specs.values():
            out.append(
                {
                    "type": "function",
                    "name": spec.name,
                    "description": spec.description,
                    "parameters": spec.parameters,
                }
            )
        return out

    def reset_turn(self) -> None:
        self._turn_calls = 0

    def grant_session(self, tool_name: str) -> None:
        self._session_grants.add(tool_name)

    async def dispatch(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        session_id: str | None = None,
        approved: bool = False,
    ) -> ToolResult:
        """Dispatch a tool call. Returns needs_approval via meta if policy says prompt."""
        if self._turn_calls >= self.tools_config.max_tool_calls_per_turn:
            return ToolResult(
                output='{"error":"max_tool_calls_per_turn"}',
                is_error=True,
            )
        if self._session_calls >= self.tools_config.max_tool_calls_per_session:
            return ToolResult(
                output='{"error":"max_tool_calls_per_session"}',
                is_error=True,
            )

        spec = self._specs.get(name)
        if spec is None:
            return ToolResult(
                output=f'{{"error":"unknown_tool","name":"{name}"}}',
                is_error=True,
            )

        # run_command: reject non-argv shapes early
        if name == "run_command":
            if set(arguments.keys()) - {"argv"}:
                return ToolResult(
                    output='{"error":"argv_only_schema"}',
                    is_error=True,
                    decision="deny",
                )
            if "argv" not in arguments:
                return ToolResult(
                    output='{"error":"argv_only_schema"}',
                    is_error=True,
                    decision="deny",
                )

        self._turn_calls += 1
        self._session_calls += 1

        try:
            result = await spec.handler(
                arguments,
                tools=self.tools_config,
                approved=approved or name in self._session_grants,
                spec=spec,
            )
        except Exception as exc:
            log.exception("tool %s failed", name)
            result = ToolResult(
                output=f'{{"error":"handler_exception","detail":"{exc}"}}',
                is_error=True,
                risk=spec.risk,
            )

        if self.audit:
            self.audit.log(
                "tool_call",
                session_id=session_id,
                tool_name=name,
                decision=result.decision,
                risk=result.risk or spec.risk,
                args_summary=str(arguments)[:500],
                result_summary=result.output[:500],
                error="error" if result.is_error else None,
            )
        return result
