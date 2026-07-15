"""Tool registry and dispatch."""

from __future__ import annotations

import json
from typing import Any

from aegis.audit import AuditLogger
from aegis.config.schema import ApprovalDefault, ToolsConfig
from aegis.tools.types import (
    ToolResult,
    ToolSpec,
    err_json,
)
from aegis.util.logging import get_logger
from aegis.util.secrets import redact_secrets

log = get_logger("tools.registry")


def _summarize_args(arguments: dict[str, Any]) -> str:
    """Audit-safe summary: argument keys + redacted argv/path, never raw bodies.

    We keep the executable/path visible (useful for audit) but never log free-text
    fields like write_file.content, which could carry secrets that redaction misses.
    """
    keys = sorted(arguments)
    parts = [f"keys={keys}"]
    argv = arguments.get("argv")
    if isinstance(argv, list):
        parts.append("argv=" + redact_secrets(" ".join(str(a) for a in argv))[:200])
    path = arguments.get("path")
    if isinstance(path, str):
        parts.append(f"path={path[:200]}")
    return " ".join(parts)


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
        # A session grant is intentionally scoped to the exact approved request,
        # never merely to a tool name. A broad tool-name grant would let a later,
        # prompt-injected invocation swap a safe path or command for a secret or
        # destructive one.
        self._session_grants: dict[str, set[str]] = {}

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

    def grant_session(self, tool_name: str, arguments: dict[str, Any]) -> None:
        fingerprint = _arguments_fingerprint(arguments)
        if fingerprint is not None:
            self._session_grants.setdefault(tool_name, set()).add(fingerprint)

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
                output=err_json("unknown_tool", name=name),
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

        approval_mode = self.tools_config.approval.default
        session_granted = self._is_session_granted(name, arguments)
        effective_approved = approved or session_granted

        # Global approval mode (schema tools.approval.default) — enforced here so
        # every tool pack shares one choke point. Handlers still apply path/argv
        # policy when the call is allowed through.
        if approval_mode is ApprovalDefault.DENY_ALL and not approved:
            # deny_all is absolute: session grants do not bypass it.
            return ToolResult(
                output=err_json("denied", reason="approval_default_deny_all"),
                is_error=True,
                risk=spec.risk,
                decision="deny",
            )
        if (
            approval_mode is ApprovalDefault.PROMPT_ALL
            and not effective_approved
        ):
            return ToolResult(
                output=err_json("approval_required", reason="approval_default_prompt_all"),
                is_error=True,
                risk=spec.risk,
                decision="prompt",
                meta={"needs_approval": True, "arguments": arguments},
            )
        # auto_readonly: per-handler / policy risk rules (unchanged).

        self._turn_calls += 1
        self._session_calls += 1

        try:
            result = await spec.handler(
                arguments,
                tools=self.tools_config,
                approved=effective_approved,
                spec=spec,
            )
        except Exception as exc:
            log.exception("tool %s failed", name)
            result = ToolResult(
                output=err_json("handler_exception", detail=str(exc)),
                is_error=True,
                risk=spec.risk,
            )

        # Approval probe (needs_approval) is not a completed tool use — do not
        # charge turn/session budgets until the approved re-dispatch runs.
        if result.meta.get("needs_approval"):
            self._turn_calls = max(0, self._turn_calls - 1)
            self._session_calls = max(0, self._session_calls - 1)

        if self.audit:
            self.audit.log(
                "tool_call",
                session_id=session_id,
                tool_name=name,
                decision=result.decision,
                risk=result.risk or spec.risk,
                # Log the shape of the call, not raw bodies: tool arguments and
                # outputs can contain file contents, secrets-file reads, or key
                # material that regex redaction won't reliably catch.
                args_summary=_summarize_args(arguments),
                result_summary=f"len={len(result.output)}",
                error="error" if result.is_error else None,
            )
        return result

    def _is_session_granted(self, tool_name: str, arguments: dict[str, Any]) -> bool:
        fingerprint = _arguments_fingerprint(arguments)
        return fingerprint is not None and fingerprint in self._session_grants.get(tool_name, set())


def _arguments_fingerprint(arguments: dict[str, Any]) -> str | None:
    try:
        return json.dumps(arguments, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError):
        return None
