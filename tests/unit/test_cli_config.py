"""CLI config command tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from aegis.cli import main


def test_config_path() -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["config", "path"])
    assert result.exit_code == 0
    assert "config_dir=" in result.output
    assert "config_file=" in result.output


def test_config_show_defaults() -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["config", "show"])
    assert result.exit_code == 0
    assert "profile:" in result.output
    assert "mvp" in result.output
    assert "gpt-realtime-2.1-mini" in result.output


def test_config_show_json() -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["config", "show", "--format", "json"])
    assert result.exit_code == 0
    assert '"profile"' in result.output


def test_config_validate_defaults() -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["config", "validate"])
    assert result.exit_code == 0
    assert "ok:" in result.output


def test_config_validate_file(tmp_path: Path) -> None:
    cfg = tmp_path / "config.toml"
    cfg.write_text('[profile]\nname = "oncall"\n', encoding="utf-8")
    runner = CliRunner()
    result = runner.invoke(main, ["--config", str(cfg), "config", "validate"])
    assert result.exit_code == 0
    assert "oncall" in result.output


def test_config_init(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state_home"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data_home"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache_home"))
    runner = CliRunner()
    result = runner.invoke(main, ["config", "init"])
    assert result.exit_code == 0, result.output
    assert (tmp_path / "aegis" / "config.toml").is_file()
    # second init without --force fails
    result2 = runner.invoke(main, ["config", "init"])
    assert result2.exit_code == 1


def test_profile_override_flag() -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["--profile", "oncall", "config", "show"])
    assert result.exit_code == 0
    assert "oncall" in result.output
    assert "gpt-realtime-2.1" in result.output
