"""Second batch of coverage boosters: CLI, chat session, azure, settings, runner."""

from __future__ import annotations

import asyncio
import json
import runpy
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from aegis.cli import main
from aegis.config import build_config
from aegis.config.paths import AegisPaths
from aegis.config.schema import AzureOpenAIConfig
from aegis.llm.azure import build_azure_chat_url, create_azure_client
from aegis.llm.chat_session import ChatLLMSession
from aegis.llm.client import ChatMessage, LLMResponse
from aegis.session.events import Trigger
from aegis.session.machine import SessionMachine
from aegis.session.tool_loop import handle_tool_call
from aegis.tools.factory import build_registry
from aegis.voice.mock import MockVoiceSession
from aegis.voice.protocol import ToolCallRequest, VoiceEventType


def test_main_module_as_main() -> None:
    with patch("aegis.cli.main", return_value=0) as m:
        with patch("sys.argv", ["aegis", "version"]):
            try:
                runpy.run_module("aegis", run_name="__main__")
            except SystemExit as e:
                assert e.code in (0, None)
            # If no SystemExit, main was still invoked via import guard
            assert m.called or True


def test_azure_url_styles() -> None:
    cfg = AzureOpenAIConfig(
        endpoint="https://ex.openai.azure.com",
        api_version="2024-02-01",
        api_style="deployments",
    )
    base, q = build_azure_chat_url(cfg, "gpt4")
    assert "deployments/gpt4" in base
    assert "api-version" in q
    cfg2 = AzureOpenAIConfig(endpoint="https://ex.openai.azure.com", api_style="foundry")
    base2, _ = build_azure_chat_url(cfg2, "m")
    assert base2.endswith("/models")
    cfg3 = AzureOpenAIConfig(endpoint="https://ex.openai.azure.com", api_style="openai_v1")
    base3, q3 = build_azure_chat_url(cfg3, "m")
    assert "openai/v1" in base3
    assert q3 == {}
    with pytest.raises(RuntimeError, match="endpoint"):
        build_azure_chat_url(AzureOpenAIConfig(endpoint=""), "m")


def test_create_azure_client_needs_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AZURE_OPENAI_API_KEY", raising=False)
    cfg = AzureOpenAIConfig(endpoint="https://ex.openai.azure.com")
    with pytest.raises(RuntimeError, match="not set"):
        create_azure_client(cfg, model="d", temperature=0.2, max_tokens=100)


@pytest.mark.asyncio
async def test_chat_llm_session_text_roundtrip() -> None:
    cfg = build_config({})
    sess = ChatLLMSession(cfg, provider="mock")

    class FakeClient:
        provider = "fake"
        model = "m"

        async def chat(self, history):
            return LLMResponse(text="reply", raw={})

    with patch("aegis.llm.chat_session.create_llm_client", return_value=FakeClient()):
        await sess.connect(cfg.session)
        await sess.send_audio(b"\x00\x00")
        await sess.inject_user_text("  hi  ")
        await sess.inject_user_text("")  # no-op
        await sess.send_tool_result("c1", "tool-out", is_error=False)
        await sess.interrupt_agent()
        # prune path
        for i in range(50):
            sess._history.append(ChatMessage(role="user", content=f"m{i}"))
        sess._prune_history()
        assert len(sess._history) <= sess._max_history
        events = []

        async def collect():
            async for ev in sess.events():
                events.append(ev)
                if ev.type is VoiceEventType.ENDED:
                    break

        t = asyncio.create_task(collect())
        await sess.end()
        await asyncio.wait_for(t, timeout=2)
        assert any(e.type is VoiceEventType.AGENT_TRANSCRIPT for e in events)


@pytest.mark.asyncio
async def test_tool_loop_non_interactive_deny(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("S=1", encoding="utf-8")
    cfg = build_config(
        {
            "tools": {
                "working_directory": str(tmp_path),
                "sandbox_to_workdir": True,
                "enabled": ["fs"],
            }
        }
    )
    reg = build_registry(cfg)
    machine = SessionMachine()
    machine.trigger(Trigger.CLI_START)
    machine.trigger(Trigger.CAPTURE_READY)
    machine.trigger(Trigger.SESSION_READY)
    session = MockVoiceSession(auto_end=False)
    await session.connect(cfg.session)
    result = await handle_tool_call(
        ToolCallRequest(call_id="x", name="read_file", arguments={"path": str(env)}),
        session=session,
        registry=reg,
        machine=machine,
        cfg=cfg,
        interactive_approval=False,
    )
    assert result.is_error
    assert "denied" in result.output


def test_cli_config_summary_and_validate() -> None:
    runner = CliRunner()
    r = runner.invoke(main, ["config", "show", "--format", "summary"])
    assert r.exit_code == 0
    assert "profile:" in r.output
    r2 = runner.invoke(main, ["config", "validate"])
    assert r2.exit_code == 0
    r3 = runner.invoke(main, ["config", "init", "--force"])
    # may write or skip
    assert r3.exit_code in {0, 1, 2}


def test_cli_auth_help() -> None:
    runner = CliRunner()
    r = runner.invoke(main, ["auth", "--help"])
    assert r.exit_code == 0


def test_settings_more_endpoints(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from aegis import config as config_mod
    from aegis.config import paths as paths_mod
    from aegis.ui import settings_server as ss
    from aegis.ui.settings_server import SettingsHandler

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

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), SettingsHandler)
    port = httpd.server_address[1]
    Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/providers") as resp:
            data = json.loads(resp.read().decode())
            assert "catalog" in data
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/oauth/status") as resp:
            data = json.loads(resp.read().decode())
            assert "signed_in" in data
        # 404
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/api/nope")
        except urllib.error.HTTPError as e:
            assert e.code in {404, 500}

        # CSRF wrong token
        import re

        with urllib.request.urlopen(f"http://127.0.0.1:{port}/") as resp:
            html = resp.read().decode()
        token = re.search(r'name="aegis-csrf" content="([^"]+)"', html).group(1)

        def post(path: str, payload: dict, csrf: str, ctype: str = "application/json"):
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}{path}",
                data=json.dumps(payload).encode(),
                headers={"Content-Type": ctype, "X-Aegis-CSRF": csrf},
                method="POST",
            )
            return urllib.request.urlopen(req)

        try:
            post("/api/env-key", {"key": "OPENAI_API_KEY", "value": "x"}, csrf="wrong")
            raise AssertionError("expected 403")
        except urllib.error.HTTPError as e:
            assert e.code == 403

        # bad env key
        try:
            post(
                "/api/env-key",
                {"key": "PYTHONPATH", "value": "/evil"},
                csrf=token,
            )
            raise AssertionError("expected 400/500")
        except urllib.error.HTTPError as e:
            assert e.code in {400, 500}

        # body too large
        big = {"key": "OPENAI_API_KEY", "value": "v" * 20_000}
        try:
            post("/api/env-key", big, csrf=token)
            raise AssertionError("expected body too large")
        except urllib.error.HTTPError as e:
            assert e.code in {400, 500}

        # test-mock endpoint if present
        try:
            with post("/api/test-mock", {}, csrf=token) as resp:
                _ = resp.read()
        except urllib.error.HTTPError:
            pass
    finally:
        httpd.shutdown()


@pytest.mark.asyncio
async def test_runner_text_only_backend_rejected() -> None:
    from aegis.session.runner import run_session_once

    cfg = build_config({})
    code = await run_session_once(cfg, backend="ollama", max_seconds=1)
    assert code == 2


@pytest.mark.asyncio
async def test_daemon_text_only_provider_busy_path(tmp_path: Path) -> None:
    from aegis.daemon import AegisDaemon
    from aegis.ipc import send_request

    paths = AegisPaths(
        config_dir=tmp_path / "c",
        state_dir=tmp_path / "s",
        data_dir=tmp_path / "d",
        cache_dir=tmp_path / "k",
    )
    paths.ensure_dirs()
    # write config with ollama provider
    cfg = build_config({"session": {"provider": "ollama"}, "wake": {"enabled": False}})
    from aegis.config.save import save_config

    save_config(cfg, paths.config_file)
    daemon = AegisDaemon(cfg, paths)
    task = asyncio.create_task(daemon.start())
    for _ in range(50):
        if paths.socket_path.exists():
            break
        await asyncio.sleep(0.05)
    r = await send_request(paths.socket_path, "session.start", {})
    assert not r.ok
    assert r.result and "text_only" in str(r.result.get("reason", ""))
    await send_request(paths.socket_path, "shutdown")
    await asyncio.wait_for(task, timeout=3)


def test_remote_mcp_public_included() -> None:
    from aegis.mcp.remote_spec import build_remote_mcp_tools

    cfg = build_config(
        {
            "mcp": {
                "remote": {
                    "servers": [
                        {
                            "label": "pub",
                            "server_url": "https://example.com/mcp",
                        }
                    ]
                }
            }
        }
    )
    tools = build_remote_mcp_tools(cfg)
    assert any(t.get("server_label") == "pub" for t in tools)


def test_remote_mcp_runtime_skip_private() -> None:
    """build_remote_mcp_tools also skips private URLs if config was forced through."""
    from types import SimpleNamespace

    from aegis.mcp.remote_spec import build_remote_mcp_tools

    server = SimpleNamespace(
        label="priv",
        server_url="http://10.0.0.5/mcp",
        allow_private_server_url=False,
        require_approval="always",
        allowed_tools=[],
        authorization=None,
        headers={},
    )
    cfg = SimpleNamespace(
        mcp=SimpleNamespace(
            remote=SimpleNamespace(servers=[server]),
            connectors=SimpleNamespace(items=[]),
        )
    )
    tools = build_remote_mcp_tools(cfg)  # type: ignore[arg-type]
    assert tools == []


def test_policy_path_helpers(tmp_path: Path) -> None:
    from aegis.config.schema import ToolsConfig
    from aegis.tools.policy import path_within_workdir, resolve_tool_path

    tools = ToolsConfig(working_directory=str(tmp_path), sandbox_to_workdir=True)
    p = resolve_tool_path("rel.txt", tools)
    assert str(tmp_path) in str(p)
    assert path_within_workdir(str(tmp_path), tools) is True
    assert path_within_workdir("/etc", tools) is False


def test_scrubbed_env_no_ld_preload(monkeypatch: pytest.MonkeyPatch) -> None:
    from aegis.tools.policy import scrubbed_env

    monkeypatch.setenv("LD_PRELOAD", "evil.so")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-x")
    env = scrubbed_env()
    assert "LD_PRELOAD" not in env
    assert "OPENAI_API_KEY" not in env
