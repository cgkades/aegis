"""Settings server body size and env-key allowlist."""

from __future__ import annotations

from aegis.ui import settings_server as ss


def test_allowed_env_keys_cover_openai() -> None:
    assert "OPENAI_API_KEY" in ss._ALLOWED_ENV_KEYS


def test_max_body_constants_sane() -> None:
    assert ss._MAX_JSON_BODY_BYTES >= 1024
    assert ss._MAX_ENV_KEY_BODY_BYTES <= ss._MAX_JSON_BODY_BYTES
