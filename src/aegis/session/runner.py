"""Foreground session runners (dogfood / session once)."""

from __future__ import annotations

import asyncio
import contextlib
import signal
import sys
import time
from pathlib import Path
from typing import Literal

from aegis.audio import AudioGraph, AudioGraphConfig, sounddevice_available
from aegis.audit import AuditLogger
from aegis.config import AegisConfig, default_paths, load_config
from aegis.config.paths import AegisPaths
from aegis.mcp.remote_spec import build_remote_mcp_tools
from aegis.session.context import ContextManager
from aegis.session.events import SessionState, Trigger
from aegis.session.machine import SessionMachine
from aegis.session.tool_loop import handle_tool_call
from aegis.tools.factory import build_registry
from aegis.ui.status import Presence, StatusPresenter, format_session_banner
from aegis.util.logging import get_logger, setup_logging
from aegis.util.metrics import SessionMetrics
from aegis.voice.factory import create_voice_session
from aegis.voice.gateway import default_gateway
from aegis.voice.protocol import VoiceEventType, VoiceSession

log = get_logger("session.runner")

Backend = Literal["realtime", "mock", "gpt_live", "text_fallback"]


async def run_session_once(
    cfg: AegisConfig,
    *,
    backend: Backend | str = "realtime",
    paths: AegisPaths | None = None,
    max_seconds: float | None = None,
) -> int:
    """Connect voice, stream mic (if available), play agent audio, exit on end/SIGINT."""
    paths = paths or default_paths()
    duration = max_seconds if max_seconds is not None else float(cfg.session.max_duration_s)
    deadline = time.monotonic() + duration

    machine = SessionMachine()
    machine.trigger(Trigger.CLI_START, skip_confirm=True)
    machine.trigger(Trigger.CAPTURE_READY)
    assert machine.state is SessionState.CONNECTING

    status = StatusPresenter(
        chime_on_wake=cfg.activation.chime_on_wake,
        chime_on_connecting=cfg.activation.chime_on_connecting,
        chime_on_end=cfg.activation.chime_on_end,
    )
    status.set_presence(Presence.CONNECTING)

    audit = AuditLogger(
        paths.audit_dir,
        redact=cfg.privacy.redact_secrets_in_audit,
    )
    registry = build_registry(cfg, audit=audit)
    tool_schemas = registry.openai_function_schemas()
    # Merge remote MCP tools for Realtime API to execute
    remote_mcp = build_remote_mcp_tools(cfg)
    all_tools = [*tool_schemas, *remote_mcp]

    context = ContextManager(cfg.session.context)
    metrics = SessionMetrics(model=cfg.session.model)

    session = create_voice_session(
        cfg,
        backend=str(backend),
        paths=paths,
        tools=all_tools,
        instructions=_load_instructions(cfg, paths),
    )

    graph: AudioGraph | None = None
    if sounddevice_available():
        graph = AudioGraph(AudioGraphConfig.from_audio_config(cfg.audio))
        try:
            graph.start()
        except Exception as exc:
            log.warning("audio start failed, text-only session: %s", exc)
            graph = None

    try:
        print(
            format_session_banner(
                session_id=machine.context.session_id,
                model=cfg.session.model,
                backend=str(backend),
                tools=registry.names(),
            ),
            file=sys.stderr,
        )
        await session.connect(cfg.session)
        machine.trigger(Trigger.SESSION_READY)
        status.set_presence(
            Presence.ACTIVE,
            detail=f"id={machine.context.session_id}",
        )
    except Exception as exc:
        log.error("connect failed: %s", exc)
        print(f"connect failed: {exc}", file=sys.stderr)
        with contextlib.suppress(Exception):
            machine.trigger(Trigger.CONNECT_FAIL)
        if graph:
            graph.stop()
        with contextlib.suppress(Exception):
            await session.end()
        status.set_presence(Presence.IDLE)
        return 1

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)

    uplink_task: asyncio.Task[None] | None = None
    if graph is not None:
        uplink_task = asyncio.create_task(
            _uplink_loop(session, graph, machine, stop),
            name="uplink",
        )

    auto_end_mock = str(backend) == "mock"

    try:
        while not stop.is_set() and time.monotonic() < deadline:
            # Cost / duration caps
            if metrics.exceeds_cost_cap(cfg.session.max_session_cost_usd):
                log.warning(
                    "max_session_cost_usd exceeded: $%.4f",
                    metrics.estimated_cost_usd,
                )
                print(
                    f"Cost cap reached (${metrics.estimated_cost_usd:.4f}). Ending.",
                    file=sys.stderr,
                )
                with contextlib.suppress(Exception):
                    machine.trigger(Trigger.MAX_COST)
                break
            if metrics.duration_s >= cfg.session.max_duration_s:
                with contextlib.suppress(Exception):
                    machine.trigger(Trigger.MAX_DURATION)
                break

            try:
                event = await asyncio.wait_for(_next_event(session), timeout=0.25)
            except TimeoutError:
                if auto_end_mock and machine.state is SessionState.ACTIVE:
                    stop.set()
                continue
            if event is None:
                break

            if event.type is VoiceEventType.AGENT_AUDIO and event.pcm16:
                metrics.mark_first_audio()
                if graph:
                    import numpy as np

                    pcm = np.frombuffer(event.pcm16, dtype="<i2")
                    with contextlib.suppress(Exception):
                        graph.play_session_audio(pcm)
            elif event.type is VoiceEventType.AGENT_TRANSCRIPT and event.text:
                context.add_transcript("assistant", event.text)
                print(f"Aegis: {event.text}", flush=True)
            elif event.type is VoiceEventType.USER_TRANSCRIPT and event.text:
                context.add_transcript("user", event.text)
                print(f"You: {event.text}", flush=True)
            elif event.type is VoiceEventType.TOOL_CALL and event.tool_call:
                print(
                    f"[tool] {event.tool_call.name}({event.tool_call.arguments})",
                    file=sys.stderr,
                )
                if machine.state is SessionState.ACTIVE and event.tool_call:
                    # ApprovalPending handled inside tool_loop via machine triggers
                    pass
                registry.reset_turn()
                metrics.tool_calls += 1
                if event.tool_call and any(
                    # detect approval path for presence
                    True for _ in [0]
                ):
                    pass
                result = await handle_tool_call(
                    event.tool_call,
                    session=session,
                    registry=registry,
                    machine=machine,
                    cfg=cfg,
                    interactive_approval=True,
                )
                context.add_tool_result(event.tool_call.name, result.output)
                if machine.state is SessionState.APPROVAL_PENDING:
                    status.set_presence(Presence.APPROVAL)
                elif machine.state is SessionState.ACTIVE:
                    status.set_presence(Presence.ACTIVE)
            elif event.type is VoiceEventType.ERROR:
                metrics.errors += 1
                print(f"error: {event.message}", file=sys.stderr)
            elif event.type is VoiceEventType.USAGE and event.usage:
                cost = metrics.add_usage(event.usage)
                log.info(
                    "usage in_audio=%s out_audio=%s cached=%s cost~$%.5f",
                    event.usage.input_audio_tokens,
                    event.usage.output_audio_tokens,
                    event.usage.cached_input_tokens,
                    cost,
                )
            elif event.type is VoiceEventType.ENDED:
                break
    finally:
        stop.set()
        status.set_presence(Presence.ENDING)
        if uplink_task:
            uplink_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await uplink_task
        if machine.state is SessionState.ACTIVE:
            with contextlib.suppress(Exception):
                machine.trigger(Trigger.HOTKEY_END)
        with contextlib.suppress(Exception):
            await session.end()
        if machine.state is SessionState.ENDING:
            with contextlib.suppress(Exception):
                machine.trigger(Trigger.TEARDOWN_DONE)
        if graph:
            graph.stop()
        with contextlib.suppress(Exception):
            default_gateway.assert_idle_has_no_cloud()
        report = metrics.report()
        print(
            f"Session ended. duration={report['duration_s']}s "
            f"ttfa={report['ttfa_s']} cost~${report['estimated_cost_usd']:.5f}",
            file=sys.stderr,
        )
        status.set_presence(Presence.IDLE)
        audit.log(
            "session_end",
            session_id=machine.context.metadata.get("last_session_id")
            or machine.context.session_id,
            extra=report,
        )

    return 0


async def _next_event(session: VoiceSession):
    aiter = getattr(session, "_aegis_aiter", None)
    if aiter is None:
        aiter = session.events().__aiter__()
        session._aegis_aiter = aiter
    try:
        return await aiter.__anext__()
    except StopAsyncIteration:
        return None


async def _uplink_loop(
    session: VoiceSession,
    graph: AudioGraph,
    machine: SessionMachine,
    stop: asyncio.Event,
) -> None:
    while not stop.is_set() and machine.state is SessionState.ACTIVE:
        frame = await asyncio.to_thread(graph.capture.read, 0.2)
        if frame is None:
            continue
        if machine.context.mute_uplink:
            continue
        uplink = graph.uplink_frame(frame)
        if uplink is None:
            continue
        await session.send_audio(uplink.tobytes())


def _load_instructions(cfg: AegisConfig, paths: AegisPaths) -> str:
    path = paths.instructions_file
    if path.is_file():
        return path.read_text(encoding="utf-8")
    alt = Path(cfg.session.instructions_file)
    if alt.is_file():
        return alt.read_text(encoding="utf-8")
    return (
        "You are Aegis, a local-first ops pair for a Linux workstation. "
        "Be concise and practical. Prefer structured tools over shell. "
        "Never claim to have run a command without a tool result."
    )


def run_session_once_sync(
    *,
    config_path: str | None = None,
    profile: str | None = None,
    backend: Backend | str = "realtime",
    max_seconds: float | None = None,
) -> int:
    setup_logging("info")
    paths = default_paths()
    cfg = load_config(
        Path(config_path) if config_path else None,
        paths=paths,
        profile=profile,
        missing_ok=True,
    )
    return asyncio.run(
        run_session_once(cfg, backend=backend, paths=paths, max_seconds=max_seconds)
    )
