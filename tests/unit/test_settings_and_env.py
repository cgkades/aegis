"""Settings save/load and dotenv helpers."""

from __future__ import annotations

import json
import os
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest
from click.testing import CliRunner

from aegis.cli import main
from aegis.config import build_config, load_config
from aegis.config.env import (
    env_status,
    load_dotenv,
    write_env_key,
)
from aegis.config.paths import AegisPaths
from aegis.config.save import apply_llm_settings, config_to_toml, save_config
from aegis.ui.settings_server import SettingsHandler, _settings_dict


def test_write_and_load_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env_file = tmp_path / ".env"
    write_env_key(env_file, "OPENAI_API_KEY", "sk-test-1234567890")
    write_env_key(env_file, "OPENAI_API_KEY", "sk-updated-key-abcdef")
    text = env_file.read_text(encoding="utf-8")
    assert text.count("OPENAI_API_KEY=") == 1
    assert "sk-updated-key-abcdef" in text

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    loaded = load_dotenv(extra=env_file, override=True)
    assert env_file in loaded
    assert os.environ.get("OPENAI_API_KEY") == "sk-updated-key-abcdef"
    st = env_status(["OPENAI_API_KEY"])
    assert st["OPENAI_API_KEY"]["set"] is True
    masked = str(st["OPENAI_API_KEY"]["masked"])
    assert masked.endswith("cdef") or "*" in masked
    assert "sk-updated-key-abcdef" not in masked


def test_apply_and_save_config(tmp_path: Path) -> None:
    cfg = build_config({})
    updated = apply_llm_settings(
        cfg,
        profile="oncall",
        provider="realtime",
        model="gpt-realtime-2.1",
        voice="coral",
        reasoning_effort="medium",
        max_session_cost_usd=5.5,
        max_duration_s=600,
        idle_timeout_s=30,
        api_key_env="OPENAI_API_KEY",
        realtime_url="wss://api.openai.com/v1/realtime",
        log_level="debug",
    )
    path = tmp_path / "config.toml"
    save_config(updated, path)
    text = path.read_text(encoding="utf-8")
    assert 'name = "oncall"' in text or "oncall" in text
    assert "gpt-realtime-2.1" in text
    assert "coral" in text

    reloaded = load_config(path, missing_ok=False)
    assert reloaded.session.model == "gpt-realtime-2.1"
    assert reloaded.session.voice == "coral"
    assert reloaded.session.max_session_cost_usd == 5.5


def test_config_to_toml_roundtrip_keys() -> None:
    cfg = build_config({"session": {"model": "gpt-realtime-2.1-mini"}})
    toml = config_to_toml(cfg)
    assert "[session]" in toml
    assert "[openai]" in toml
    assert "gpt-realtime-2.1-mini" in toml


def test_settings_dict() -> None:
    cfg = build_config({})
    d = _settings_dict(cfg)
    assert "model" in d
    assert "provider" in d


def test_settings_cli_help() -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["settings", "--help"])
    assert result.exit_code == 0
    out = result.output.lower()
    assert "settings" in out or "8765" in out


def test_settings_http_get_and_post(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    paths = AegisPaths(
        config_dir=tmp_path / "cfg",
        state_dir=tmp_path / "state",
        data_dir=tmp_path / "data",
        cache_dir=tmp_path / "cache",
    )
    paths.ensure_dirs()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    # default_paths uses XDG_CONFIG_HOME/aegis — set home layout
    # Our AegisPaths uses config_dir = XDG_CONFIG_HOME/aegis when using default_paths
    # So put config under tmp_path/aegis
    cfg_home = tmp_path / "aegis"
    cfg_home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    from aegis import config as config_mod
    from aegis.config import paths as paths_mod
    from aegis.ui import settings_server as ss

    fake_paths = AegisPaths(
        config_dir=tmp_path / "aegis",
        state_dir=tmp_path / "state",
        data_dir=tmp_path / "data",
        cache_dir=tmp_path / "cache",
    )
    fake_paths.ensure_dirs()
    monkeypatch.setattr(ss, "default_paths", lambda: fake_paths)
    monkeypatch.setattr(config_mod, "default_paths", lambda: fake_paths)
    monkeypatch.setattr(paths_mod, "default_paths", lambda: fake_paths)
    monkeypatch.setattr(ss, "project_root", lambda: tmp_path)

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), SettingsHandler)
    port = httpd.server_address[1]
    thread = Thread(target=httpd.serve_forever, daemon=True)
    thread.start()

    import re

    def post(path: str, payload: dict, *, csrf: str | None, host: str | None = None):
        headers = {"Content-Type": "application/json"}
        if csrf is not None:
            headers["X-Aegis-CSRF"] = csrf
        if host is not None:
            headers["Host"] = host
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}{path}",
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        return urllib.request.urlopen(req)

    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/") as resp:
            html = resp.read().decode("utf-8")
            assert "Aegis settings" in html
        # Extract the per-session CSRF token injected into the page.
        token = re.search(r'name="aegis-csrf" content="([^"]+)"', html).group(1)
        assert token and token != "__AEGIS_CSRF_TOKEN__"

        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/settings") as resp:
            data = json.loads(resp.read().decode("utf-8"))
            assert "settings" in data
            assert "env" in data

        settings_payload = {
            "profile": "standard",
            "provider": "realtime",
            "model": "gpt-realtime-2.1-mini",
            "voice": "alloy",
            "reasoning_effort": "low",
            "max_session_cost_usd": 3.0,
            "max_duration_s": 900,
            "idle_timeout_s": 45,
            "api_key_env": "OPENAI_API_KEY",
            "realtime_url": "wss://api.openai.com/v1/realtime",
            "log_level": "info",
        }

        # POST without CSRF token must be rejected (403).
        try:
            post("/api/settings", settings_payload, csrf=None)
            raise AssertionError("expected 403 without CSRF token")
        except urllib.error.HTTPError as exc:
            assert exc.code == 403

        # POST with a spoofed Host header must be rejected (DNS-rebinding guard).
        try:
            post("/api/settings", settings_payload, csrf=token, host="evil.example.com")
            raise AssertionError("expected 403 with bad Host")
        except urllib.error.HTTPError as exc:
            assert exc.code == 403

        with post("/api/settings", settings_payload, csrf=token) as resp:
            saved = json.loads(resp.read().decode("utf-8"))
            assert saved["ok"] is True
            assert saved["settings"]["profile"] == "standard"

        assert fake_paths.config_file.is_file()

        with post(
            "/api/env-key",
            {"key": "OPENAI_API_KEY", "value": "sk-test-settings-key-xyz"},
            csrf=token,
        ) as resp:
            key_data = json.loads(resp.read().decode("utf-8"))
            assert key_data["ok"] is True
        # Keys are written to the user's secrets file, not $CWD/.env.
        secrets_file = fake_paths.secrets_env
        assert secrets_file.is_file()
        assert "sk-test-settings-key-xyz" in secrets_file.read_text(encoding="utf-8")
    finally:
        httpd.shutdown()
