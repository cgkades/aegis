"""XDG Base Directory paths for Aegis."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _xdg_home(env_var: str, default_subdir: str) -> Path:
    raw = os.environ.get(env_var)
    if raw:
        return Path(raw).expanduser()
    return Path.home() / default_subdir


@dataclass(frozen=True, slots=True)
class AegisPaths:
    """Resolved filesystem locations for config, state, data, and cache."""

    config_dir: Path
    state_dir: Path
    data_dir: Path
    cache_dir: Path

    @property
    def config_file(self) -> Path:
        return self.config_dir / "config.toml"

    @property
    def secrets_env(self) -> Path:
        return self.config_dir / "secrets.env"

    @property
    def instructions_file(self) -> Path:
        return self.config_dir / "instructions.md"

    @property
    def socket_path(self) -> Path:
        return self.state_dir / "aegis.sock"

    @property
    def pid_file(self) -> Path:
        return self.state_dir / "daemon.pid"

    @property
    def audit_dir(self) -> Path:
        return self.data_dir / "audit"

    @property
    def sessions_dir(self) -> Path:
        return self.data_dir / "sessions"

    @property
    def models_dir(self) -> Path:
        return self.data_dir / "models"

    @property
    def workspace_dir(self) -> Path:
        """Default tools.working_directory sandbox root."""
        return self.data_dir / "workspace"

    def ensure_dirs(self) -> None:
        """Create standard directories with restrictive permissions where appropriate."""
        for path in (
            self.config_dir,
            self.state_dir,
            self.data_dir,
            self.cache_dir,
            self.audit_dir,
            self.sessions_dir,
            self.models_dir,
            self.workspace_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)
            try:
                path.chmod(0o700)
            except OSError:
                pass


def default_paths() -> AegisPaths:
    """Return XDG-respecting paths for the current user."""
    return AegisPaths(
        config_dir=_xdg_home("XDG_CONFIG_HOME", ".config") / "aegis",
        state_dir=_xdg_home("XDG_STATE_HOME", ".local/state") / "aegis",
        data_dir=_xdg_home("XDG_DATA_HOME", ".local/share") / "aegis",
        cache_dir=_xdg_home("XDG_CACHE_HOME", ".cache") / "aegis",
    )
