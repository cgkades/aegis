"""Load, merge, and validate Aegis configuration."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from aegis.config.paths import AegisPaths, default_paths
from aegis.config.profiles import deep_merge, profile_overlay
from aegis.config.schema import AegisConfig, ProfileName


class ConfigError(Exception):
    """Raised when configuration cannot be loaded or is invalid."""


def load_toml_file(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ConfigError(f"config file not found: {path}")
    try:
        text = path.read_text(encoding="utf-8")
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"invalid TOML in {path}: {exc}") from exc
    except OSError as exc:
        raise ConfigError(f"cannot read {path}: {exc}") from exc


def resolve_profile_name(raw: dict[str, Any]) -> ProfileName:
    profile_block = raw.get("profile") or {}
    name = profile_block.get("name", ProfileName.MVP.value)
    try:
        return ProfileName(name)
    except ValueError as exc:
        raise ConfigError(
            f"unknown profile {name!r}; expected one of "
            f"{', '.join(p.value for p in ProfileName)}"
        ) from exc


def build_config(
    user_dict: dict[str, Any] | None = None,
    *,
    profile: ProfileName | str | None = None,
) -> AegisConfig:
    """Merge defaults ← profile overlay ← user dict and validate."""
    user_dict = dict(user_dict or {})
    if profile is not None:
        user_dict = deep_merge(user_dict, {"profile": {"name": str(profile)}})

    profile_name = resolve_profile_name(user_dict)
    # Order: empty base → profile defaults → user overrides (user wins)
    merged = deep_merge({}, profile_overlay(profile_name))
    merged = deep_merge(merged, user_dict)
    # Ensure profile name stays consistent after merge
    merged.setdefault("profile", {})["name"] = profile_name.value

    try:
        return AegisConfig.model_validate(merged)
    except ValidationError as exc:
        raise ConfigError(format_validation_error(exc)) from exc


def load_config(
    path: Path | None = None,
    *,
    paths: AegisPaths | None = None,
    profile: ProfileName | str | None = None,
    missing_ok: bool = True,
) -> AegisConfig:
    """Load config from disk, applying profile expansion.

    If the file is missing and ``missing_ok`` is True, return profile defaults
    (mvp unless ``profile`` is set).
    """
    paths = paths or default_paths()
    config_path = path or paths.config_file

    if not config_path.is_file():
        if not missing_ok:
            raise ConfigError(f"config file not found: {config_path}")
        return build_config({}, profile=profile or ProfileName.MVP)

    user_dict = load_toml_file(config_path)
    return build_config(user_dict, profile=profile)


def validate_config_file(path: Path) -> AegisConfig:
    """Load and validate; always errors if missing or invalid."""
    return load_config(path, missing_ok=False)


def format_validation_error(exc: ValidationError) -> str:
    lines = ["configuration validation failed:"]
    for err in exc.errors():
        loc = ".".join(str(part) for part in err.get("loc", ()))
        msg = err.get("msg", "invalid")
        lines.append(f"  - {loc}: {msg}")
    return "\n".join(lines)


def config_to_display_dict(cfg: AegisConfig) -> dict[str, Any]:
    return cfg.model_dump(mode="json")
