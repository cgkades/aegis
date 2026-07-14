"""CLI scaffold smoke tests."""

from __future__ import annotations

from click.testing import CliRunner

from aegis import __version__
from aegis.cli import main


def test_version_option() -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.output


def test_version_command() -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["version"])
    assert result.exit_code == 0
    assert f"aegis {__version__}" in result.output


def test_help() -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["--help"])
    assert result.exit_code == 0
    assert "Aegis" in result.output


def test_config_path() -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["config", "path"])
    assert result.exit_code == 0
    assert "config_dir=" in result.output
    assert "aegis" in result.output


def test_session_once_mock() -> None:
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["session", "once", "--backend", "mock", "--max-seconds", "2"],
    )
    # Mock should complete cleanly (0) even without sounddevice
    assert result.exit_code == 0, result.output + (result.stderr or "")
