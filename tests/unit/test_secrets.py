"""Secrets helper tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from aegis.util.secrets import (
    load_env_file,
    mask_secret,
    redact_secrets,
    resolve_api_key,
)


def test_redact_api_key() -> None:
    text = "using api_key=sk-abcdefghijklmnopqrstuv"
    redacted = redact_secrets(text)
    assert "sk-abcdefghijklmnop" not in redacted
    assert "REDACTED" in redacted


def test_redact_bearer() -> None:
    assert "secret-token" not in redact_secrets("Authorization: Bearer secret-token")


def test_load_env_file(tmp_path: Path) -> None:
    path = tmp_path / "secrets.env"
    path.write_text(
        "# comment\nOPENAI_API_KEY=sk-test123\nEMPTY=\n",
        encoding="utf-8",
    )
    data = load_env_file(path)
    assert data["OPENAI_API_KEY"] == "sk-test123"


def test_resolve_api_key_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "from-env")
    assert resolve_api_key() == "from-env"


def test_resolve_api_key_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    path = tmp_path / "secrets.env"
    path.write_text("OPENAI_API_KEY=from-file\n", encoding="utf-8")
    assert resolve_api_key(secrets_file=path) == "from-file"


def test_mask_secret() -> None:
    masked = mask_secret("abcdefghij", visible=4)
    assert masked.endswith("ghij")
    assert set(masked[:-4]) == {"*"}


def test_redact_secrets_catches_command_line_flags() -> None:
    assert "supersecret" not in redact_secrets("cmd --token supersecret")
    assert "othersecret" not in redact_secrets("cmd --api-key=othersecret")
