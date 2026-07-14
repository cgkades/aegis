"""Profile presets that expand into concrete config defaults."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from aegis.config.schema import (
    DEFAULT_READ_SHELL_RULES,
    ProfileName,
    ShellRule,
)


def profile_overlay(name: ProfileName | str) -> dict[str, Any]:
    """Return a nested dict of defaults for the named profile.

    Explicit user keys always win over these overlays (deep-merge: user last).
    """
    profile = ProfileName(name)
    if profile is ProfileName.MVP:
        return {
            "profile": {"name": "mvp"},
            "session": {
                "model": "gpt-realtime-2.1-mini",
                "max_session_cost_usd": 2.0,
                "reasoning_effort": "minimal",
            },
            "tools": {
                "enabled": ["fs"],
                "shell": {"enabled": False, "rules": []},
                "git": {"enabled": False},
                "kubectl": {"enabled": False},
            },
        }
    if profile is ProfileName.STANDARD:
        return {
            "profile": {"name": "standard"},
            "session": {
                "model": "gpt-realtime-2.1-mini",
                "max_session_cost_usd": 3.0,
                "reasoning_effort": "low",
            },
            "tools": {
                "enabled": ["fs", "git", "process", "write"],
                "shell": {
                    "enabled": False,  # still off by default; user opts in
                    "rules": [r.model_dump(mode="json") for r in DEFAULT_READ_SHELL_RULES],
                },
                "git": {"enabled": True, "deny_via_shell": True},
                "kubectl": {"enabled": False},
            },
        }
    if profile is ProfileName.ONCALL:
        return {
            "profile": {"name": "oncall"},
            "session": {
                "model": "gpt-realtime-2.1",
                "max_session_cost_usd": 8.0,
                "reasoning_effort": "medium",
                "max_duration_s": 1800,
            },
            "tools": {
                "enabled": ["fs", "git", "process", "write", "kubectl"],
                "shell": {
                    "enabled": False,
                    "rules": [r.model_dump(mode="json") for r in DEFAULT_READ_SHELL_RULES],
                },
                "git": {"enabled": True, "deny_via_shell": True},
                "kubectl": {
                    "enabled": True,
                    "deny_via_shell": True,
                    "allowed_verbs": ["get", "describe", "logs", "top"],
                },
            },
        }
    raise ValueError(f"unknown profile: {name}")


def deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge overlay onto base (overlay wins on leaf conflicts)."""
    result = deepcopy(base)
    for key, value in overlay.items():
        if (
            key in result
            and isinstance(result[key], dict)
            and isinstance(value, dict)
        ):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result


def shell_rules_from_dicts(items: list[dict[str, Any]]) -> list[ShellRule]:
    return [ShellRule.model_validate(item) for item in items]
