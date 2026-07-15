"""High-yield unit tests for previously under-covered paths."""

from __future__ import annotations

import asyncio
import inspect
import time
from pathlib import Path
from unittest.mock import AsyncMock, patch

import numpy as np
import pytest

from aegis.activation import (
    HotkeyListener,
    print_activation_help,
)
from aegis.approval.broker import ApprovalBroker
from aegis.approval.modes import ApprovalRequest, prompt_cli_approval
from aegis.audio.pipeline import AudioGraph, AudioGraphConfig
from aegis.audio.vad import EnergyVad, EnergyVadConfig
from aegis.config.schema import (
    SessionContextConfig,
    ToolsApprovalConfig,
    ToolsConfig,
    ToolsGitConfig,
    ToolsKubectlConfig,
    ToolsShellConfig,
)
from aegis.llm.chatgpt_oauth import (
    OAuthTokens,
    clear_tokens,
    load_tokens,
    save_manual_token,
    save_tokens,
    status_dict,
)
from aegis.session.context import ContextManager
from aegis.tools.builtin.git_tools import (
    handle_git_commit,
    handle_git_diff,
    handle_git_log,
    handle_git_status,
)
from aegis.tools.builtin.process_tools import handle_list_processes, handle_tail_log
from aegis.tools.builtin.write_tools import handle_apply_patch, handle_write_file
from aegis.tools.oncall.kubectl_tools import handle_kubectl
from aegis.tools.registry import ToolRegistry
from aegis.tools.types import ToolResult
from aegis.util.net import is_private_url

# ---------------------------------------------------------------------------
# util / context / activation
# ---------------------------------------------------------------------------


def test_is_private_url_edge_cases() -> None:
    assert is_private_url("http:///") is True  # empty host → private
    assert is_private_url("http://printer.local/mcp") is True
    assert is_private_url("http://foo.localhost/") is True
    assert is_private_url("https://example.com") is False
    assert is_private_url("http://[::1]/1/") is True


def test_context_manager_summary_and_snapshot() -> None:
    cfg = SessionContextConfig(
        max_transcript_turns=3,
        summarize_when_turns_exceed=2,
        max_tool_result_chars_retained=256,
        keep_last_n_tool_results=2,
    )
    cm = ContextManager(cfg)
    cm.add_transcript("user", "")  # no-op
    cm.add_transcript("user", "hello")
    cm.add_transcript("assistant", "world")
    assert cm.needs_summary is True
    cm.add_transcript("user", "again")
    cm.add_transcript("user", "fourth")  # trims to max 3
    assert len(cm.turns) == 3
    cm.add_tool_result("t", "x" * 50)
    cm.add_tool_result("t2", "short")
    cm.add_tool_result("t3", "third")
    assert len(cm.tool_results) == 2
    cm.summary = "prior"
    snap = cm.snapshot_for_prompt()
    assert "Prior summary" in snap
    assert "Recent transcript" in snap
    assert "Recent tool results" in snap
    rep = cm.pressure_report()
    assert rep["needs_summary"] is True


def test_activation_parse_hotkey_variants() -> None:
    assert HotkeyListener._parse_hotkey("") is None
    assert HotkeyListener._parse_hotkey("Super+Shift+Space") == "<cmd>+<shift>+<space>"
    assert HotkeyListener._parse_hotkey("Ctrl+Alt+a") == "<ctrl>+<alt>+a"
    assert HotkeyListener._parse_hotkey("meta-x") == "<cmd>+x"
    from aegis.config.schema import ActivationConfig

    print_activation_help(ActivationConfig())


def test_oauth_token_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "tok.json"
    assert load_tokens(path) is None
    tok = OAuthTokens(
        access_token="at",
        refresh_token="rt",
        expires_at=time.time() + 3600,
        email="u@example.com",
    )
    assert tok.signed_in is True
    save_tokens(path, tok)
    loaded = load_tokens(path)
    assert loaded is not None
    assert loaded.access_token == "at"
    st = status_dict(path)
    assert st["signed_in"] is True
    assert st["email"] == "u@example.com"
    # expired
    tok2 = OAuthTokens(access_token="x", expires_at=time.time() - 100)
    assert tok2.expired is True
    assert tok2.signed_in is False
    save_manual_token(path, "manual-token", email="m@x.com")
    loaded2 = load_tokens(path)
    assert loaded2 is not None
    assert loaded2.access_token == "manual-token"
    clear_tokens(path)
    assert not path.is_file()
    assert status_dict(path)["signed_in"] is False
    # corrupt file
    path.write_text("{not json", encoding="utf-8")
    assert load_tokens(path) is None


# ---------------------------------------------------------------------------
# approval modes / broker edge cases
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_prompt_timeout_non_tty() -> None:
    cfg = ToolsApprovalConfig(timeout_s=5)
    req = ApprovalRequest("t", "s", "write", "c1")

    class SlowStdin:
        def isatty(self) -> bool:
            return False

        def fileno(self) -> int:
            raise OSError("no fd")

        def readline(self) -> str:
            time.sleep(5.0)
            return "y\n"

    async def timeout_without_starting(awaitable, timeout=None):
        if inspect.iscoroutine(awaitable):
            awaitable.close()
        raise TimeoutError()

    with patch("sys.stdin", SlowStdin()):
        with patch(
            "aegis.approval.modes.asyncio.wait_for",
            side_effect=timeout_without_starting,
        ):
            resp = await prompt_cli_approval(req, cfg)
    assert resp.allowed is False
    assert resp.reason == "timeout"


@pytest.mark.asyncio
async def test_broker_respond_unknown_and_double() -> None:
    broker = ApprovalBroker(timeout_s=2)
    assert broker.respond("nope", allowed=True) is False
    req = ApprovalRequest("t", "s", "write", "d1")

    async def approve_twice() -> None:
        await asyncio.sleep(0.05)
        assert broker.respond("d1", allowed=True) is True
        assert broker.respond("d1", allowed=True) is False  # already done

    t = asyncio.create_task(approve_twice())
    resp = await broker.request(req)
    await t
    assert resp.allowed is True


# ---------------------------------------------------------------------------
# write / process / git / kubectl
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_write_content_too_large(tmp_path: Path) -> None:
    tools = ToolsConfig(
        working_directory=str(tmp_path),
        max_write_bytes=1024,
        sandbox_to_workdir=True,
    )
    r = await handle_write_file(
        {"path": "big.txt", "content": "x" * 5000},
        tools=tools,
    )
    assert r.is_error
    assert "content_too_large" in r.output


@pytest.mark.asyncio
async def test_write_and_patch_happy(tmp_path: Path) -> None:
    tools = ToolsConfig(working_directory=str(tmp_path), sandbox_to_workdir=True)
    r = await handle_write_file(
        {"path": "a.txt", "content": "hello"},
        tools=tools,
        approved=True,
    )
    assert not r.is_error
    assert (tmp_path / "a.txt").read_text() == "hello"
    r2 = await handle_apply_patch(
        {"path": "a.txt", "old": "hello", "new": "hi"},
        tools=tools,
        approved=True,
    )
    assert not r2.is_error
    assert (tmp_path / "a.txt").read_text() == "hi"
    r3 = await handle_apply_patch(
        {"path": "a.txt", "old": "missing", "new": "x"},
        tools=tools,
        approved=True,
    )
    assert r3.is_error
    assert "old_string_not_found" in r3.output


@pytest.mark.asyncio
async def test_list_processes_and_tail(tmp_path: Path) -> None:
    tools = ToolsConfig(working_directory=str(tmp_path), sandbox_to_workdir=True)
    r = await handle_list_processes({"filter": "python"}, tools=tools)
    assert not r.is_error
    logf = tmp_path / "app.log"
    logf.write_text("line1\nline2\nline3\n", encoding="utf-8")
    t = await handle_tail_log({"path": "app.log", "lines": 2}, tools=tools)
    assert not t.is_error
    assert "line3" in t.output


@pytest.mark.asyncio
async def test_list_processes_timeout(tmp_path: Path) -> None:
    tools = ToolsConfig(working_directory=str(tmp_path))

    class SlowProc:
        pid = 12345
        returncode = None

        async def communicate(self):
            raise TimeoutError()

        async def wait(self):
            return 0

    async def wait_for(coro, timeout=None):
        # Consume the coroutine to avoid "never awaited" warnings, then timeout.
        if asyncio.iscoroutine(coro):
            try:
                await coro
            except TimeoutError:
                pass
        raise TimeoutError()

    with (
        patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=SlowProc())),
        patch("aegis.tools.executor._kill_process_group") as kill,
        patch("asyncio.wait_for", side_effect=wait_for),
    ):
        r = await handle_list_processes({}, tools=tools)
    assert r.is_error
    assert "ps_failed" in r.output or "timeout" in r.output
    kill.assert_called()


@pytest.mark.asyncio
async def test_git_tools_mocked(tmp_path: Path) -> None:
    tools = ToolsConfig(
        working_directory=str(tmp_path),
        sandbox_to_workdir=True,
        git=ToolsGitConfig(enabled=True, allow_commit=True),
    )

    async def fake_git(args, cwd, timeout=30):
        return ToolResult(output=f"ok:{' '.join(args)}", risk="read", decision="auto")

    with patch("aegis.tools.builtin.git_tools._git", side_effect=fake_git):
        s = await handle_git_status({}, tools=tools)
        assert "status" in s.output
        d = await handle_git_diff({"staged": True, "full": False}, tools=tools)
        assert "diff" in d.output
        lg = await handle_git_log({"n": 5}, tools=tools)
        assert "log" in lg.output
        c = await handle_git_commit(
            {"message": "msg", "add_all": True},
            tools=tools,
            approved=True,
        )
        assert not c.is_error


@pytest.mark.asyncio
async def test_git_commit_disabled_and_needs_approval(tmp_path: Path) -> None:
    tools = ToolsConfig(
        working_directory=str(tmp_path),
        git=ToolsGitConfig(enabled=True, allow_commit=False),
    )
    r = await handle_git_commit({"message": "x"}, tools=tools)
    assert r.is_error
    assert "disabled" in r.output
    tools2 = ToolsConfig(
        working_directory=str(tmp_path),
        git=ToolsGitConfig(enabled=True, allow_commit=True),
    )
    r2 = await handle_git_commit({"message": "x"}, tools=tools2, approved=False)
    assert r2.decision == "prompt"


@pytest.mark.asyncio
async def test_kubectl_not_found_and_extra_args(tmp_path: Path) -> None:
    tools = ToolsConfig(
        kubectl=ToolsKubectlConfig(
            enabled=True,
            allowed_verbs=["get"],
            allowed_namespaces=["staging"],
        )
    )
    with patch("aegis.tools.oncall.kubectl_tools.shutil.which", return_value=None):
        r = await handle_kubectl(
            {"verb": "get", "resource": "pods", "namespace": "staging"},
            tools=tools,
        )
    assert "kubectl_not_found" in r.output

    class Proc:
        returncode = 0

        async def communicate(self):
            return b"ok\n", b""

    with (
        patch("aegis.tools.oncall.kubectl_tools.shutil.which", return_value="/bin/kubectl"),
        patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=Proc())),
    ):
        r2 = await handle_kubectl(
            {
                "verb": "get",
                "resource": "pods",
                "name": "p1",
                "namespace": "staging",
                "extra_args": ["-o", "name"],
            },
            tools=tools,
        )
    assert not r2.is_error


# ---------------------------------------------------------------------------
# registry / audio graph / vad
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_registry_unknown_and_argv_schema() -> None:
    tools = ToolsConfig(shell=ToolsShellConfig(enabled=True), enabled=["shell"])
    reg = ToolRegistry(tools)
    from aegis.tools.builtin.shell_tools import shell_tool_specs

    for s in shell_tool_specs():
        reg.register(s)
    u = await reg.dispatch("nope", {})
    assert u.is_error and "unknown_tool" in u.output
    bad = await reg.dispatch("run_command", {"command": "ls"})
    assert bad.is_error and "argv_only" in bad.output
    reg.grant_session("run_command", {"argv": ["ls"]})
    assert reg._is_session_granted("run_command", {"argv": ["ls"]}) is True
    assert reg._is_session_granted("run_command", {"argv": ["pwd"]}) is False


def test_energy_vad_and_graph_stop() -> None:
    vad = EnergyVad(EnergyVadConfig(sample_rate_hz=16000, energy_threshold=100, hangover_ms=50))
    silence = np.zeros(320, dtype=np.int16)
    loud = np.full(320, 5000, dtype=np.int16)
    assert vad.should_uplink(silence) is False
    assert vad.should_uplink(loud) is True
    vad.reset()
    # Graph stop without start should not explode
    g = AudioGraph(AudioGraphConfig())
    g.stop()


# ---------------------------------------------------------------------------
# realtime put_event backpressure / arg cap
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_realtime_put_event_drops_audio_when_full() -> None:
    from aegis.voice.gateway import CloudAudioGateway
    from aegis.voice.protocol import VoiceEvent, VoiceEventType
    from aegis.voice.realtime import RealtimeVoiceSession

    gw = CloudAudioGateway()
    s = RealtimeVoiceSession(api_key="sk", gateway=gw)
    # Fill queue
    for _ in range(300):
        await s._put_event(VoiceEvent(type=VoiceEventType.AGENT_AUDIO, pcm16=b"\x00\x00"))
    # control event should still land
    await s._put_event(VoiceEvent(type=VoiceEventType.ERROR, message="x"))
    # drain a bit
    got_error = False
    while not s._events.empty():
        ev = s._events.get_nowait()
        if ev and ev.type is VoiceEventType.ERROR:
            got_error = True
    assert got_error


@pytest.mark.asyncio
async def test_realtime_function_arg_size_cap() -> None:
    from aegis.voice.gateway import CloudAudioGateway
    from aegis.voice.realtime import RealtimeVoiceSession

    s = RealtimeVoiceSession(api_key="sk", gateway=CloudAudioGateway())
    call_id = "big"
    # stream oversize deltas
    chunk = "a" * 10_000
    while True:
        await s._handle_server_event(
            {
                "type": "response.function_call_arguments.delta",
                "call_id": call_id,
                "delta": chunk,
            }
        )
        if call_id in s._function_arg_overflows:
            break
    assert call_id in s._function_arg_overflows
    assert call_id not in s._function_arg_buffers
    await s._handle_server_event(
        {
            "type": "response.function_call_arguments.done",
            "call_id": call_id,
            "name": "write_file",
            "arguments": "",
        }
    )
