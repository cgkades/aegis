"""Configuration loading, schema, profiles, and XDG paths."""

from __future__ import annotations

from aegis.config.load import (
    ConfigError,
    build_config,
    config_to_display_dict,
    load_config,
    validate_config_file,
)
from aegis.config.paths import AegisPaths, default_paths
from aegis.config.schema import AegisConfig, ProfileName

__all__ = [
    "AegisConfig",
    "AegisPaths",
    "ConfigError",
    "ProfileName",
    "build_config",
    "config_to_display_dict",
    "default_paths",
    "load_config",
    "validate_config_file",
]
