"""Additional CLI coverage."""

from __future__ import annotations

from click.testing import CliRunner

from aegis.cli import main


def test_status_not_running() -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["status"])
    # may be 1 if no daemon
    assert result.exit_code in {0, 1}


def test_activation_cmd() -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["activation"])
    assert result.exit_code == 0
    assert "session start" in result.output


def test_doctor_full() -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["doctor"])
    assert result.exit_code == 0
    assert "aegis doctor" in result.output
    assert "tools:" in result.output


def test_doctor_idle_profile() -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["doctor", "--idle-profile", "--seconds", "0.2"])
    assert result.exit_code == 0


def test_config_show_json() -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["config", "show", "--format", "json"])
    assert result.exit_code == 0
    assert "profile" in result.output


def test_session_start_no_daemon() -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["session", "start"])
    assert result.exit_code == 1


def test_daemon_help() -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["daemon", "--help"])
    assert result.exit_code == 0
