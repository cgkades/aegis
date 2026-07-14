"""Persist Aegis config to TOML on disk."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from aegis.config.schema import AegisConfig


def _toml_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _format_value(value: Any, indent: int = 0) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, float):
        return str(value)
    if isinstance(value, str):
        return f'"{_toml_escape(value)}"'
    if value is None:
        return '""'
    if isinstance(value, list):
        if not value:
            return "[]"
        if all(isinstance(x, str) for x in value):
            inner = ", ".join(f'"{_toml_escape(x)}"' for x in value)
            return f"[{inner}]"
        if all(isinstance(x, (int, float, bool)) for x in value):
            return "[" + ", ".join(_format_value(x) for x in value) + "]"
        # complex arrays not used in settings page
        return "[]"
    return f'"{_toml_escape(str(value))}"'


def config_to_toml(cfg: AegisConfig) -> str:
    """Serialize the settings-relevant portions of config to TOML."""
    d = cfg.model_dump(mode="json")
    lines: list[str] = [
        "# Aegis configuration — managed by `aegis settings` / settings page",
        "# See DESIGN.md and configs/aegis.example.toml for full schema.",
        "",
    ]

    def emit_table(path: str, table: dict[str, Any]) -> None:
        lines.append(f"[{path}]")
        for key, value in table.items():
            if isinstance(value, dict):
                continue
            lines.append(f"{key} = {_format_value(value)}")
        lines.append("")
        for key, value in table.items():
            if isinstance(value, dict):
                emit_table(f"{path}.{key}", value)

    # Top-level tables in stable order
    for name in (
        "app",
        "profile",
        "audio",
        "wake",
        "activation",
        "session",
        "openai",
        "tools",
        "privacy",
        "observability",
    ):
        if name in d and isinstance(d[name], dict):
            emit_table(name, d[name])

    return "\n".join(lines).rstrip() + "\n"


def save_config(cfg: AegisConfig, path: Path) -> Path:
    """Write config TOML to path (creates parent dirs)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = config_to_toml(cfg)
    path.write_text(text, encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return path


def apply_llm_settings(
    cfg: AegisConfig,
    *,
    profile: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    voice: str | None = None,
    reasoning_effort: str | None = None,
    max_session_cost_usd: float | None = None,
    max_duration_s: int | None = None,
    idle_timeout_s: int | None = None,
    api_key_env: str | None = None,
    realtime_url: str | None = None,
    log_level: str | None = None,
) -> AegisConfig:
    """Return a copy of cfg with LLM-related fields updated."""
    data = cfg.model_dump(mode="json")
    if profile is not None:
        data["profile"]["name"] = profile
    if provider is not None:
        data["session"]["provider"] = provider
    if model is not None:
        data["session"]["model"] = model
    if voice is not None:
        data["session"]["voice"] = voice
    if reasoning_effort is not None:
        data["session"]["reasoning_effort"] = reasoning_effort
    if max_session_cost_usd is not None:
        data["session"]["max_session_cost_usd"] = float(max_session_cost_usd)
    if max_duration_s is not None:
        data["session"]["max_duration_s"] = int(max_duration_s)
    if idle_timeout_s is not None:
        data["session"]["idle_timeout_s"] = int(idle_timeout_s)
    if api_key_env is not None:
        data["openai"]["api_key_env"] = api_key_env
    if realtime_url is not None:
        data["openai"]["realtime_url"] = realtime_url
    if log_level is not None:
        data["app"]["log_level"] = log_level
    return AegisConfig.model_validate(data)
