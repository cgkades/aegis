"""Shared tool types."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Literal

RiskClass = Literal["read", "exec", "write", "network", "destroy", "secrets"]


def err_json(error: str, **fields: Any) -> str:
    """Build a valid JSON error payload for tool results.

    Using json.dumps (not f-strings) so exception text containing quotes,
    backslashes, or newlines can't corrupt the JSON returned to the model.
    """
    return json.dumps({"error": error, **fields})


class PolicyDecision(StrEnum):
    AUTO = "auto"
    PROMPT = "prompt"
    DENY = "deny"


@dataclass(slots=True)
class PolicyResult:
    decision: PolicyDecision
    risk: RiskClass
    reason: str = ""
    resolved_argv: list[str] | None = None


@dataclass(slots=True)
class ToolResult:
    output: str
    is_error: bool = False
    risk: RiskClass | None = None
    decision: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)


ToolHandler = Callable[..., Awaitable[ToolResult]]


@dataclass(slots=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]
    risk: RiskClass
    handler: ToolHandler
    timeout_s: int | None = None
    source: str = "builtin"
    env_allowlist: tuple[str, ...] = ()


# Normative OpenAI schema for run_command
RUN_COMMAND_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "argv": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 1,
            "description": (
                "Executable and arguments, e.g. [\"ls\", \"-la\", \"src\"]. "
                "Not a shell string."
            ),
        }
    },
    "required": ["argv"],
    "additionalProperties": False,
}
