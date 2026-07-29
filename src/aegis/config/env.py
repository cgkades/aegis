"""Load dotenv-style env files for local testing (never commit secrets)."""

from __future__ import annotations

import os
from pathlib import Path

from aegis.config.paths import default_paths
from aegis.util.logging import get_logger
from aegis.util.secrets import load_env_file

log = get_logger("config.env")

# secrets.env / .env carry provider credentials — never arbitrary process env.
# Loading unrestricted keys would let a dotenv set XDG_CONFIG_HOME (redirecting
# the whole config/secrets/instructions tree), BROWSER (executed by
# webbrowser.open), PATH, or LD_*. Keys are matched exactly or by credential
# suffix; everything else is ignored with a warning.
ALLOWED_ENV_KEYS = frozenset(
    {
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
        "LITELLM_API_KEY",
        "LITELLM_BASE_URL",
        "AZURE_OPENAI_API_KEY",
        "AZURE_OPENAI_ENDPOINT",
        "AZURE_OPENAI_API_VERSION",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
        "AWS_REGION",
        "AWS_DEFAULT_REGION",
        "BEDROCK_API_KEY",
        "PICOVOICE_ACCESS_KEY",
        "ANTHROPIC_API_KEY",
        "GOOGLE_API_KEY",
        "GROQ_API_KEY",
        "TOGETHER_API_KEY",
        "MISTRAL_API_KEY",
        "COHERE_API_KEY",
        "DEEPSEEK_API_KEY",
        "XAI_API_KEY",
        "OLLAMA_API_KEY",
        "OLLAMA_HOST",
    }
)

# Readable from a dotenv but not writable through the settings UI.
_EXTRA_LOADABLE_KEYS = frozenset(
    {
        "AWS_PROFILE",
        "KUBECONFIG",
        "OPENAI_REALTIME_URL",
        "AEGIS_PROFILE",
    }
)


def is_loadable_env_key(key: str) -> bool:
    """Whether a dotenv key may be copied into os.environ."""
    if key in ALLOWED_ENV_KEYS or key in _EXTRA_LOADABLE_KEYS:
        return True
    return key.endswith(("_API_KEY", "_ACCESS_KEY"))


def checkout_root() -> Path | None:
    """Repo root when the running code lives in a source checkout, else None.

    Deliberately does not consider CWD: a dotenv found next to whatever
    directory the user happened to run ``aegis`` from is attacker-controlled in
    any shared or cloned tree.
    """
    # src/aegis/config/env.py → parents[3] = repo root in editable install layout
    root = Path(__file__).resolve().parents[3]
    if (root / "pyproject.toml").is_file() and (root / "src" / "aegis").is_dir():
        return root
    return None


def project_root() -> Path:
    """Repo root when running from a checkout; else CWD (display only)."""
    return checkout_root() or Path.cwd()


def env_file_candidates() -> list[Path]:
    """Ordered list of env files (later files do not override already-set keys)."""
    paths = default_paths()
    candidates: list[Path] = []
    root = checkout_root()
    if root is not None:
        candidates.append(root / ".env")
    candidates.append(paths.config_dir / ".env")
    candidates.append(paths.secrets_env)
    return candidates


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
            if not is_loadable_env_key(key):
                log.warning("ignoring non-credential key %r in %s", key, path)
                continue
            if override or key not in os.environ or os.environ.get(key, "") == "":
                os.environ[key] = value
        loaded.append(path)
    return loaded


def write_env_key(path: Path, key: str, value: str) -> None:
    """Upsert KEY=value in a dotenv file (creates file if missing)."""
    # A newline in the value would append extra KEY=VALUE lines that load_dotenv
    # then imports as real env vars — env injection from a pasted "API key".
    for field, text in (("key", key), ("value", value)):
        if any(ch in text for ch in ("\n", "\r", "\x00")):
            raise ValueError(f"{field} must not contain newlines or null bytes")
    if not key or any(ch in key for ch in (" ", "=")):
        raise ValueError("key must not be empty or contain spaces or '='")
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
        # Note: AEGIS_PROFILE is display-only today; load_config uses config.toml
        # or CLI --profile. Listed so operators see it if they set it by habit.
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
