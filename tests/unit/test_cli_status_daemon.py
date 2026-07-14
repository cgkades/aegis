"""CLI status/session.start against a live temp daemon."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from click.testing import CliRunner

from aegis.cli import main
from aegis.config import build_config
from aegis.config.paths import AegisPaths
from aegis.daemon import AegisDaemon


@pytest.mark.asyncio
async def test_cli_status_via_socket(tmp_path: Path, monkeypatch) -> None:
    paths = AegisPaths(
        config_dir=tmp_path / "c",
        state_dir=tmp_path / "s",
        data_dir=tmp_path / "d",
        cache_dir=tmp_path / "k",
    )
    paths.ensure_dirs()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    cfg = build_config({"wake": {"enabled": False}})
    daemon = AegisDaemon(cfg, paths)
    task = asyncio.create_task(daemon.start())
    for _ in range(50):
        if paths.socket_path.exists():
            break
        await asyncio.sleep(0.05)

    import aegis.cli as cli_mod

    monkeypatch.setattr(cli_mod, "default_paths", lambda: paths)
    monkeypatch.setattr("aegis.config.default_paths", lambda: paths)
    monkeypatch.setattr("aegis.cli.default_paths", lambda: paths)
    monkeypatch.setattr("aegis.config.paths.default_paths", lambda: paths)
    from aegis import config as cfg_pkg

    monkeypatch.setattr(cfg_pkg, "default_paths", lambda: paths)

    runner = CliRunner()
    # The Click command calls asyncio.run(), so run it in a worker thread while
    # this test's event loop continues serving the temporary daemon.
    result = await asyncio.to_thread(runner.invoke, main, ["status"])
    assert result.exit_code == 0, result.output
    assert "daemon: running" in result.output

    await send_request_shutdown(paths)
    await asyncio.wait_for(task, timeout=3)


async def send_request_shutdown(paths: AegisPaths) -> None:
    from aegis.ipc import send_request

    await send_request(paths.socket_path, "shutdown")
