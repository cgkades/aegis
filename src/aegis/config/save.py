"""Persist Aegis config to TOML on disk."""

from __future__ import annotations

import contextlib
import os
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

import tomli_w

from aegis.config.schema import AegisConfig
from aegis.util.logging import get_logger

log = get_logger("config.save")

_HEADER = (
    "# Aegis configuration — managed by `aegis settings` / settings page\n"
    "# See DESIGN.md and configs/aegis.example.toml for full schema.\n\n"
)


def _drop_none(value: Any) -> Any:
    """Recursively strip None values — TOML has no null; a missing key means default.

    Applied to the whole config tree so optional fields (e.g. ShellRule.allowed_flags,
    McpLocalServer.cwd) round-trip as "absent" rather than crashing the serializer.
    """
    if isinstance(value, dict):
        return {k: _drop_none(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [_drop_none(v) for v in value]
    return value


def config_to_toml(cfg: AegisConfig) -> str:
    """Serialize the full config to TOML.

    Uses a real TOML writer so nested tables, arrays-of-tables (``tools.shell.rules``,
    ``mcp.local.servers``, ``mcp.remote.servers``) and dict fields (``*.env``) survive a
    round-trip. The previous hand-rolled writer silently dropped them, so saving from
    the settings page wiped MCP servers and custom shell rules.
    """
    data = _drop_none(cfg.model_dump(mode="json"))
    return _HEADER + tomli_w.dumps(data)


def save_config(cfg: AegisConfig, path: Path) -> Path:
    """Write config TOML to path atomically (creates parent dirs).

    Writes a sibling temp file and renames it into place. A truncated config —
    from a crash mid-write or two writers racing (settings server and CLI) —
    would make the daemon exit 78 at startup and stay down, turning a "Save"
    click into an outage.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    text = config_to_toml(cfg)
    tmp_fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp"
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        # Permissions before the rename so the file is never briefly world-readable.
        with contextlib.suppress(OSError):
            tmp_path.chmod(0o600)
        os.replace(tmp_path, path)
    except BaseException:
        with contextlib.suppress(OSError):
            tmp_path.unlink()
        raise
    return path


_MISSING = object()


def _leaf_paths(value: object, prefix: tuple[str, ...] = ()) -> set[tuple[str, ...]]:
    if not isinstance(value, dict):
        return {prefix}
    paths: set[tuple[str, ...]] = set()
    for key, child in value.items():
        paths.update(_leaf_paths(child, (*prefix, key)))
    return paths


def _get_path(data: dict[str, Any], path: tuple[str, ...]) -> object:
    current: object = data
    for key in path:
        if not isinstance(current, dict) or key not in current:
            return _MISSING
        current = current[key]
    return current


def _set_path(data: dict[str, Any], path: tuple[str, ...], value: object) -> None:
    current = data
    for key in path[:-1]:
        child = current.get(key)
        if not isinstance(child, dict):
            child = {}
            current[key] = child
        current = child
    current[path[-1]] = value


def _switch_profile_defaults(data: dict[str, Any], cfg: AegisConfig, profile: str) -> None:
    """Apply only profile-owned defaults that the user has not overridden."""
    from aegis.config.load import build_config
    from aegis.config.profiles import profile_overlay
    from aegis.config.schema import ProfileName

    target = ProfileName(profile)
    if target is cfg.profile.name:
        return

    current_baseline = build_config({}, profile=cfg.profile.name).model_dump(mode="json")
    target_baseline = build_config({}, profile=target).model_dump(mode="json")
    managed_paths = _leaf_paths(profile_overlay(cfg.profile.name)) | _leaf_paths(
        profile_overlay(target)
    )
    managed_paths.discard(("profile", "name"))

    for path in managed_paths:
        current = _get_path(data, path)
        old_default = _get_path(current_baseline, path)
        if current is not _MISSING and current == old_default:
            new_default = _get_path(target_baseline, path)
            if new_default is not _MISSING:
                _set_path(data, path, new_default)

    data.setdefault("profile", {})["name"] = target.value


@dataclass(frozen=True, slots=True)
class SettingSpec:
    """One user-facing setting: where it is read from and where it is written."""

    name: str
    # Attribute path on AegisConfig, for building the GET payload.
    source: tuple[str, ...]
    # Dict paths in the dumped config. More than one when a value is mirrored
    # (the legacy top-level [openai] block and its [llm.openai] equivalent).
    targets: tuple[tuple[str, ...], ...]
    coerce: Callable[[Any], Any] | None = None


# The single source of truth for the settings surface. The GET payload, the
# POST handler and the settings page all derive from this: previously each
# maintained its own hand-written copy of the field list, and a field added to
# three of the four was silently dropped on save.
#
# `profile` is deliberately absent — it does not map to a path, it rewrites
# profile-owned defaults. See apply_settings.
SETTINGS: tuple[SettingSpec, ...] = (
    SettingSpec("provider", ("session", "provider"),
                (("session", "provider"), ("llm", "chat_provider"))),
    SettingSpec("model", ("session", "model"), (("session", "model"),)),
    SettingSpec("voice", ("session", "voice"), (("session", "voice"),)),
    # session.reasoning_effort is intentionally absent: no code path sends it to
    # a provider, so exposing it as a savable setting advertises an effect that
    # does not exist. Re-add here (and add an input to the page) when it is
    # actually put on the wire.
    SettingSpec("max_session_cost_usd", ("session", "max_session_cost_usd"),
                (("session", "max_session_cost_usd"),), float),
    SettingSpec("max_duration_s", ("session", "max_duration_s"),
                (("session", "max_duration_s"),), int),
    SettingSpec("idle_timeout_s", ("session", "idle_timeout_s"),
                (("session", "idle_timeout_s"),), int),
    SettingSpec("log_level", ("app", "log_level"), (("app", "log_level"),)),
    SettingSpec("api_key_env", ("openai", "api_key_env"),
                (("openai", "api_key_env"), ("llm", "openai", "api_key_env"))),
    SettingSpec("realtime_url", ("openai", "realtime_url"),
                (("openai", "realtime_url"), ("llm", "openai", "realtime_url"))),
    SettingSpec("openai_chat_base_url", ("openai", "chat_base_url"),
                (("openai", "chat_base_url"), ("llm", "openai", "chat_base_url"))),
    SettingSpec("temperature", ("llm", "temperature"), (("llm", "temperature"),), float),
    SettingSpec("max_tokens", ("llm", "max_tokens"), (("llm", "max_tokens"),), int),
    SettingSpec("litellm_base_url", ("llm", "litellm", "base_url"),
                (("llm", "litellm", "base_url"),)),
    SettingSpec("litellm_api_key_env", ("llm", "litellm", "api_key_env"),
                (("llm", "litellm", "api_key_env"),)),
    SettingSpec("litellm_model", ("llm", "litellm", "model"),
                (("llm", "litellm", "model"),)),
    SettingSpec("ollama_base_url", ("llm", "ollama", "base_url"),
                (("llm", "ollama", "base_url"),)),
    SettingSpec("ollama_native_base_url", ("llm", "ollama", "native_base_url"),
                (("llm", "ollama", "native_base_url"),)),
    SettingSpec("ollama_model", ("llm", "ollama", "model"),
                (("llm", "ollama", "model"),)),
    SettingSpec("chatgpt_token_path", ("llm", "chatgpt_oauth", "token_path"),
                (("llm", "chatgpt_oauth", "token_path"),)),
    SettingSpec("azure_endpoint", ("llm", "azure_openai", "endpoint"),
                (("llm", "azure_openai", "endpoint"),)),
    SettingSpec("azure_api_key_env", ("llm", "azure_openai", "api_key_env"),
                (("llm", "azure_openai", "api_key_env"),)),
    SettingSpec("azure_api_version", ("llm", "azure_openai", "api_version"),
                (("llm", "azure_openai", "api_version"),)),
    SettingSpec("azure_deployment", ("llm", "azure_openai", "deployment"),
                (("llm", "azure_openai", "deployment"),)),
    SettingSpec("azure_api_style", ("llm", "azure_openai", "api_style"),
                (("llm", "azure_openai", "api_style"),)),
    SettingSpec("azure_auth_mode", ("llm", "azure_openai", "auth_mode"),
                (("llm", "azure_openai", "auth_mode"),)),
    SettingSpec("bedrock_region", ("llm", "bedrock", "region"),
                (("llm", "bedrock", "region"),)),
    SettingSpec("bedrock_model_id", ("llm", "bedrock", "model_id"),
                (("llm", "bedrock", "model_id"),)),
    SettingSpec("bedrock_profile", ("llm", "bedrock", "profile"),
                (("llm", "bedrock", "profile"),)),
    SettingSpec("bedrock_endpoint_url", ("llm", "bedrock", "endpoint_url"),
                (("llm", "bedrock", "endpoint_url"),)),
)

SETTING_NAMES: tuple[str, ...] = ("profile", *(spec.name for spec in SETTINGS))

# Selecting a deployment or a Bedrock model id also sets session.model, unless
# the caller named a model explicitly in the same update.
_MODEL_ALIASES = ("azure_deployment", "bedrock_model_id")


def _enum_value(value: Any) -> Any:
    return value.value if isinstance(value, Enum) else value


def _read_attr(cfg: AegisConfig, path: tuple[str, ...]) -> Any:
    current: Any = cfg
    for key in path:
        current = getattr(current, key)
    return _enum_value(current)


def settings_payload(cfg: AegisConfig) -> dict[str, Any]:
    """The settings surface as the UI sees it."""
    payload: dict[str, Any] = {"profile": _read_attr(cfg, ("profile", "name"))}
    for spec in SETTINGS:
        payload[spec.name] = _read_attr(cfg, spec.source)
    return payload


def apply_settings(cfg: AegisConfig, updates: Mapping[str, Any]) -> AegisConfig:
    """Return a copy of cfg with the named settings applied.

    Keys absent from ``updates`` (or set to None) are left untouched, so a
    partial payload is a partial update.
    """
    for name in updates:
        if name not in SETTING_NAMES:
            log.warning("ignoring unknown setting %r", name)

    data = cfg.model_dump(mode="json")
    profile = updates.get("profile")
    if profile is not None:
        _switch_profile_defaults(data, cfg, profile)

    for spec in SETTINGS:
        value = updates.get(spec.name)
        if value is None:
            continue
        if spec.coerce is not None:
            value = spec.coerce(value)
        for target in spec.targets:
            _set_path(data, target, value)

    if updates.get("model") is None:
        for alias in _MODEL_ALIASES:
            value = updates.get(alias)
            if value is not None:
                data["session"]["model"] = value

    return AegisConfig.model_validate(data)


def apply_llm_settings(cfg: AegisConfig, **updates: Any) -> AegisConfig:
    """Keyword-argument wrapper around :func:`apply_settings` (back-compat)."""
    return apply_settings(cfg, updates)
