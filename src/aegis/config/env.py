"""Load dotenv-style env files for local testing (never commit secrets)."""

from __future__ import annotations

import os
from pathlib import Path

from aegis.config.paths import default_paths
from aegis.util.secrets import load_env_file


def project_root() -> Path:
    """Repo root when running from a checkout; else CWD."""
    # src/aegis/config/env.py → parents[3] = repo root in editable install layout
    here = Path(__file__).resolve()
    candidates = [
        here.parents[3],  # repo root in editable-install layout
        Path.cwd(),
    ]
    for c in candidates:
        if (c / "pyproject.toml").is_file() and (c / "src" / "aegis").is_dir():
            return c
    return Path.cwd()


def env_file_candidates() -> list[Path]:
    """Ordered list of env files (later files do not override already-set keys)."""
    paths = default_paths()
    return [
        project_root() / ".env",
        paths.config_dir / ".env",
        paths.secrets_env,
    ]


def load_dotenv(
    *,
    override: bool = False,
    extra: Path | None = None,
) -> list[Path]:
    """Load env vars from candidate files into os.environ.

    By default does **not** override variables already set in the process
    environment (shell wins). Returns list of files that were loaded.
    """
    loaded: list[Path] = []
    files = env_file_candidates()
    if extra is not None:
        files = [extra, *files]
    for path in files:
        if not path.is_file():
            continue
        data = load_env_file(path)
        if not data:
            continue
        for key, value in data.items():
            if not value:
                # Never clobber with empty placeholders from .env.example copies
                continue
            if override or key not in os.environ or os.environ.get(key, "") == "":
                os.environ[key] = value
        loaded.append(path)
    return loaded


def write_env_key(path: Path, key: str, value: str) -> None:
    """Upsert KEY=value in a dotenv file (creates file if missing)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    if path.is_file():
        lines = path.read_text(encoding="utf-8").splitlines()
    out: list[str] = []
    found = False
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            out.append(line)
            continue
        k, _, _ = line.partition("=")
        if k.strip() == key:
            out.append(f"{key}={value}")
            found = True
        else:
            out.append(line)
    if not found:
        if out and out[-1].strip():
            out.append("")
        out.append(f"{key}={value}")
    path.write_text("\n".join(out) + "\n", encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass


def env_status(keys: list[str] | None = None) -> dict[str, dict[str, object]]:
    """Return presence/mask info for known secret keys (never full values)."""
    from aegis.util.secrets import mask_secret

    keys = keys or [
        "OPENAI_API_KEY",
        "LITELLM_API_KEY",
        "OLLAMA_API_KEY",
        "AZURE_OPENAI_API_KEY",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
        "AWS_REGION",
        "AWS_PROFILE",
        "PICOVOICE_ACCESS_KEY",
        "OPENAI_REALTIME_URL",
        "AEGIS_PROFILE",
    ]
    result: dict[str, dict[str, object]] = {}
    for key in keys:
        val = os.environ.get(key)
        result[key] = {
            "set": bool(val),
            "masked": mask_secret(val) if val else "",
            "length": len(val) if val else 0,
        }
    return result
