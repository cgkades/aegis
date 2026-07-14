"""Secret loading and redaction helpers."""

from __future__ import annotations

import os
import re
from pathlib import Path

# Patterns that look like secrets in logs / audit payloads.
_REDACT_PATTERNS: list[re.Pattern[str]] = [
    # Prefer specific token shapes first so partial line rewrites don't leave leftovers.
    re.compile(r"(?i)Bearer\s+[A-Za-z0-9._\-]+"),
    re.compile(r"sk-[A-Za-z0-9_-]{10,}"),
    re.compile(
        r"(?i)(api[_-]?key|token|secret|password|authorization)\s*[=:]\s*\S+"
    ),
]


def redact_secrets(text: str) -> str:
    """Best-effort redaction of common secret shapes in free text."""
    out = text
    for pattern in _REDACT_PATTERNS:
        out = pattern.sub("[REDACTED]", out)
    return out


def load_env_file(path: Path) -> dict[str, str]:
    """Load KEY=VALUE pairs from a dotenv-style file (no export syntax)."""
    if not path.is_file():
        return {}
    result: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if key:
            result[key] = value
    return result


def resolve_api_key(
    *,
    env_var: str = "OPENAI_API_KEY",
    secrets_file: Path | None = None,
) -> str | None:
    """Resolve an API key from the environment or optional secrets.env file.

    Does not read OS keyring yet (Phase 1+); env / file only for PR 3.
    """
    value = os.environ.get(env_var)
    if value:
        return value.strip() or None
    if secrets_file is not None:
        data = load_env_file(secrets_file)
        value = data.get(env_var)
        if value:
            return value.strip() or None
    return None


def mask_secret(value: str | None, visible: int = 4) -> str:
    """Return a display-safe mask of a secret."""
    if not value:
        return ""
    if len(value) <= visible:
        return "*" * len(value)
    return "*" * (len(value) - visible) + value[-visible:]
