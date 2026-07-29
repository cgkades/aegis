"""Foreground session runners (dogfood / session once)."""

from __future__ import annotations

import asyncio
import contextlib
import signal
import sys
import time
from pathlib import Path

import numpy as np

from aegis.approval.modes import ApprovalHandler
from aegis.audio import AudioGraph, AudioGraphConfig, sounddevice_available
from aegis.audit import AuditLogger
from aegis.config import AegisConfig, default_paths, load_config
from aegis.config.paths import AegisPaths
from aegis.mcp.bridge import LocalMcpBridge
from aegis.mcp.remote_spec import build_remote_mcp_tools
from aegis.session.context import ContextManager
from aegis.session.events import SessionState, Trigger
from aegis.session.machine import SessionMachine
from aegis.session.tool_loop import handle_tool_call
from aegis.tools.factory import build_registry
from aegis.tools.registry import ToolRegistry
from aegis.tools.sanitize import escape_line_breaks, strip_control_sequences
from aegis.ui.status import Presence, StatusPresenter, format_session_banner
from aegis.util.instructions import with_security_block
from aegis.util.logging import get_logger, setup_logging
from aegis.util.metrics import SessionMetrics
from aegis.voice.capabilities import (
    TEXT_ONLY_PROVIDERS,
    UNIMPLEMENTED_PROVIDERS,
    is_text_only,
)
from aegis.voice.factory import create_voice_session
from aegis.voice.gateway import default_gateway
from aegis.voice.protocol import (
    ToolCallRequest,
    VoiceEvent,
    VoiceEventType,
    VoiceSession,
)

log = get_logger("session.runner")

# Poll interval for cost/duration caps while waiting for the next voice event.
_EVENT_POLL_INTERVAL_S = 0.25
# Provider capabilities live in voice.capabilities so the runner, the daemon
# and the session factory cannot disagree about what a backend can do.
TEXT_ONLY_BACKENDS = TEXT_ONLY_PROVIDERS
_TEXT_ONLY_BACKENDS = TEXT_ONLY_BACKENDS  # back-compat alias
UNIMPLEMENTED_BACKENDS = UNIMPLEMENTED_PROVIDERS


async def run_session_once(
    cfg: AegisConfig,
    *,
    backend: str = "realtime",
    paths: AegisPaths | None = None,
    max_seconds: float | None = None,
    graph: AudioGraph | None = None,
    install_signal_handlers: bool = True,
    interactive_approval: bool | None = None,
    approval_handler: ApprovalHandler | None = None,
    session_id: str | None = None,
) -> int:
    """Connect voice, stream mic (if available), play agent audio, exit on end/SIGINT.

    ``graph`` lets a caller (the daemon) pass its already-running AudioGraph so we
    don't open a second set of streams on the same device. When provided, we do not
    stop it on exit — the owner does. ``install_signal_handlers`` should be False
    when running inside a process (the daemon) that owns the loop's signal handlers.

    ``interactive_approval``: when None, auto-detect via stdin TTY. Daemon hosts
    should pass ``approval_handler`` (IPC broker) instead of relying on stdin.

    ``session_id`` lets the daemon reuse the id it already handed to its IPC
    caller, so status output and audit records refer to the same session.
    """
    paths = paths or default_paths()
    paths.ensure_dirs()
    if interactive_approval is None:
        interactive_approval = approval_handler is None and bool(
            getattr(sys.stdin, "isatty", lambda: False)()
        )
    if is_text_only(backend):
        print(
            f"{backend} is a text-only provider and cannot be used by the voice session CLI yet. "
            "Use --backend realtime or mock; cascaded STT/TTS is not implemented.",
            file=sys.stderr,
        )
        return 2
    duration = max_seconds if max_seconds is not None else float(cfg.session.max_duration_s)
    deadline = time.monotonic() + duration

    machine = SessionMachine()
    machine.trigger(Trigger.CLI_START, skip_confirm=True, session_id=session_id)
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
        retention_days=cfg.privacy.audit_retention_days,
    )
    registry = build_registry(cfg, audit=audit)
    # Start configured local MCP stdio servers and register their tools before we
    # snapshot the schema list, so the model can actually call them.
    mcp_bridge: LocalMcpBridge | None = None
    if cfg.mcp.local.servers:
        mcp_bridge = LocalMcpBridge(cfg, registry, audit=audit)
        try:
            registered = await mcp_bridge.start()
            if registered:
                log.info("registered %d local MCP tools", len(registered))
        except Exception as exc:
            log.error("local MCP bridge failed to start: %s", exc)
            with contextlib.suppress(Exception):
                await mcp_bridge.close()
            mcp_bridge = None

    tool_schemas = registry.openai_function_schemas()
    # Merge remote MCP tools for Realtime API to execute
    remote_mcp = build_remote_mcp_tools(
        cfg, audit=audit, session_id=machine.context.session_id
    )
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

    # Use a caller-supplied graph (daemon) if present; otherwise open our own and
    # take responsibility for stopping it.
    owns_graph = graph is None
    if graph is None and sounddevice_available():
        graph = AudioGraph(AudioGraphConfig.from_audio_config(cfg.audio))
        try:
            graph.start()
        except Exception as exc:
            log.warning("audio start failed, text-only session: %s", exc)
            graph = None

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    # Save and restore any existing handlers so we don't permanently hijack the
    # daemon's SIGINT/SIGTERM handling when run in-process.
    installed_signals: list[signal.Signals] = []
    uplink_task: asyncio.Task[None] | None = None
    auto_end_mock = str(backend) == "mock"
    exit_code = 0
    connected = False

    # One teardown path for every exit: connect failure, cancellation, cap trip,
    # or natural end. Previously the connect-phase arms each carried their own
    # copy and had already drifted apart from this one.
    try:
        try:
            # Realtime is a voice-only transport. Opening a billable cloud
            # session without a capture device leaves the user unable to send a
            # turn.
            if graph is None and str(backend).lower() == "realtime":
                raise RuntimeError("audio capture unavailable for realtime voice session")
            print(
                format_session_banner(
                    session_id=machine.context.session_id,
                    model=cfg.session.model,
                    backend=str(backend),
                    tools=registry.names(),
                ),
                file=sys.stderr,
            )
            await asyncio.wait_for(
                session.connect(cfg.session), timeout=cfg.session.connect_timeout_s
            )
            machine.trigger(Trigger.SESSION_READY)
            status.set_presence(
                Presence.ACTIVE,
                detail=f"id={machine.context.session_id}",
            )
            connected = True
        except TimeoutError:
            log.error("connect timed out after %ss", cfg.session.connect_timeout_s)
            print(
                f"connect timed out after {cfg.session.connect_timeout_s}s",
                file=sys.stderr,
            )
            with contextlib.suppress(Exception):
                machine.trigger(Trigger.CONNECT_TIMEOUT)
            exit_code = 1
        except Exception as exc:
            log.error("connect failed: %s", exc)
            print(f"connect failed: {exc}", file=sys.stderr)
            with contextlib.suppress(Exception):
                machine.trigger(Trigger.CONNECT_FAIL)
            exit_code = 1
        # A BaseException here (cancellation) propagates to the teardown below,
        # which still balances CloudAudioGateway open/close accounting.

        if connected:
            if install_signal_handlers:
                for sig in (signal.SIGINT, signal.SIGTERM):
                    with contextlib.suppress(NotImplementedError):
                        loop.add_signal_handler(sig, stop.set)
                        installed_signals.append(sig)

            if graph is not None and not auto_end_mock:
                # The mock backend scripts and auto-ends its whole reply inside
                # connect(), before this task would get a chance to run — it
                # never consumes uplink audio, so starting the loop only races a
                # real capture frame against an already-disconnected
                # mock.send_audio().
                uplink_task = asyncio.create_task(
                    _uplink_loop(session, graph, machine, stop),
                    name="uplink",
                )

            await _SessionLoop(
                session=session,
                graph=graph,
                machine=machine,
                registry=registry,
                cfg=cfg,
                context=context,
                metrics=metrics,
                status=status,
                audit=audit,
                stop=stop,
                deadline=deadline,
                uplink_task=uplink_task,
                interactive_approval=interactive_approval,
                approval_handler=approval_handler,
                auto_end_mock=auto_end_mock,
            ).run()
    finally:
        stop.set()
        status.set_presence(Presence.ENDING)
        for sig in installed_signals:
            with contextlib.suppress(NotImplementedError, ValueError):
                loop.remove_signal_handler(sig)
        if uplink_task:
            uplink_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await uplink_task
        # APPROVAL_PENDING has a legal HOTKEY_END too; teardown can land there
        # when the tool worker is cancelled mid-approval.
        if machine.state in _UPLINK_ACTIVE_STATES:
            with contextlib.suppress(Exception):
                machine.trigger(Trigger.HOTKEY_END)
        # End the voice session even when teardown is cancelled: gateway
        # accounting lives in session.end()'s finally. Finish local cleanup
        # before re-raising CancelledError.
        teardown_cancelled = False
        try:
            await session.end()
        except asyncio.CancelledError:
            teardown_cancelled = True
        except Exception as exc:
            log.warning("session.end during teardown: %s", exc)
        if machine.state is SessionState.ENDING:
            with contextlib.suppress(Exception):
                machine.trigger(Trigger.TEARDOWN_DONE)
        if graph and owns_graph:
            graph.stop()
        if mcp_bridge is not None:
            with contextlib.suppress(Exception):
                await mcp_bridge.close()
        try:
            default_gateway.assert_idle_has_no_cloud()
        except Exception as exc:
            log.error("idle cloud invariant failed after session: %s", exc)
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
        if teardown_cancelled:
            raise asyncio.CancelledError

    return exit_code


_UPLINK_ACTIVE_STATES = {SessionState.ACTIVE, SessionState.APPROVAL_PENDING}


def _safe_console(text: str) -> str:
    """Escape-strip text before printing it to the operator's terminal.

    Tool *output* is sanitized on the way to the model, but model-authored
    transcripts and tool-call echoes were printed raw — so an injection could
    make the model emit ANSI/OSC sequences that spoof prompts, hide text, or
    rewrite the user's scrollback.
    """
    return escape_line_breaks(strip_control_sequences(text))


class _SessionLoop:
    """Run one connected voice session until it ends or trips a cap.

    Three concurrent pieces:

    * **pump** — owns the event iterator and routes each event immediately.
      Agent audio goes straight to playback.
    * **tool worker** — executes tool calls one at a time, in arrival order.
    * **supervisor** (:meth:`run`) — enforces the cost, duration and idle caps.

    The split exists because the pump must never wait on a tool. A tool call
    takes seconds (an approval takes as long as the human does) and the model
    keeps speaking while it runs; with a single loop the adapter's event queue
    filled and it started evicting agent audio that had already been generated
    and paid for.

    Tools stay serial: the Realtime protocol wants ``function_call_output`` in
    order, and the approval state machine assumes one pending approval. This
    keeps the drain alive, it does not run tools concurrently.
    """

    def __init__(
        self,
        *,
        session: VoiceSession,
        graph: AudioGraph | None,
        machine: SessionMachine,
        registry: ToolRegistry,
        cfg: AegisConfig,
        context: ContextManager,
        metrics: SessionMetrics,
        status: StatusPresenter,
        audit: AuditLogger | None,
        stop: asyncio.Event,
        deadline: float,
        uplink_task: asyncio.Task[None] | None,
        interactive_approval: bool,
        approval_handler: ApprovalHandler | None,
        auto_end_mock: bool,
    ) -> None:
        self._session = session
        self._graph = graph
        self._machine = machine
        self._registry = registry
        self._cfg = cfg
        self._context = context
        self._metrics = metrics
        self._status = status
        self._audit = audit
        self._stop = stop
        self._deadline = deadline
        self._uplink_task = uplink_task
        self._interactive_approval = interactive_approval
        self._approval_handler = approval_handler
        self._auto_end_mock = auto_end_mock

        self._tool_calls: asyncio.Queue[ToolCallRequest] = asyncio.Queue()
        now = time.monotonic()
        self._last_activity = now
        self._last_event_at = now
        self._tools_in_flight = 0
        self._failure: BaseException | None = None

    async def run(self) -> None:
        pump = asyncio.create_task(self._pump(), name="voice-events")
        worker = asyncio.create_task(self._tool_worker(), name="tool-worker")
        try:
            await self._supervise(pump, worker)
        finally:
            for task in (worker, pump):
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
        if self._failure is not None:
            # Preserve the old behaviour of a tool-loop error ending the session
            # by propagating, rather than quietly continuing.
            raise self._failure

    async def _supervise(self, pump: asyncio.Task[None], worker: asyncio.Task[None]) -> None:
        cfg = self._cfg
        while not self._stop.is_set() and time.monotonic() < self._deadline:
            if self._uplink_task is not None and self._uplink_task.done():
                if (
                    not self._uplink_task.cancelled()
                    and (exc := self._uplink_task.exception()) is not None
                ):
                    log.error("uplink failed: %s", exc, exc_info=exc)
                    with contextlib.suppress(Exception):
                        self._machine.trigger(Trigger.ERROR)
                    return
            for task, label in ((pump, "event pump"), (worker, "tool worker")):
                if task.done() and not task.cancelled():
                    if (exc := task.exception()) is not None:
                        log.error("%s failed: %s", label, exc, exc_info=exc)
                        self._failure = exc
                        return
            if pump.done():
                # Iterator finished: the session is over.
                return

            # Cost / duration caps. These are checked even while a tool runs —
            # previously a long tool blocked the loop and the caps went blind.
            if self._metrics.exceeds_cost_cap(cfg.session.max_session_cost_usd):
                log.warning(
                    "max_session_cost_usd exceeded: $%.4f",
                    self._metrics.estimated_cost_usd,
                )
                print(
                    f"Cost cap reached (${self._metrics.estimated_cost_usd:.4f}). Ending.",
                    file=sys.stderr,
                )
                with contextlib.suppress(Exception):
                    self._machine.trigger(Trigger.MAX_COST)
                return
            if self._metrics.duration_s >= cfg.session.max_duration_s:
                with contextlib.suppress(Exception):
                    self._machine.trigger(Trigger.MAX_DURATION)
                return
            # The idle timeout must pause while a tool runs or an approval is
            # pending: the user is not idle, they are waiting on us.
            if (
                self._machine.state is SessionState.ACTIVE
                and self._tools_in_flight == 0
                and self._tool_calls.empty()
                and time.monotonic() - self._last_activity >= cfg.session.idle_timeout_s
            ):
                log.info("session idle timeout after %ss", cfg.session.idle_timeout_s)
                with contextlib.suppress(Exception):
                    self._machine.trigger(Trigger.SILENCE_TIMEOUT)
                return

            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._stop.wait(), timeout=_EVENT_POLL_INTERVAL_S)

            if (
                self._auto_end_mock
                and self._machine.state is SessionState.ACTIVE
                and self._tools_in_flight == 0
                and self._tool_calls.empty()
                and time.monotonic() - self._last_event_at >= _EVENT_POLL_INTERVAL_S
            ):
                # The mock backend emits no terminal event when it has a tool
                # call to serve; a quiet window is how that session ends.
                self._stop.set()

    async def _pump(self) -> None:
        """Drain voice events. Every branch here must stay non-blocking."""
        async for event in self._session.events():
            self._last_event_at = time.monotonic()
            if event.type is VoiceEventType.ENDED:
                self._stop.set()
                return
            self._handle_event(event)
        self._stop.set()

    def _handle_event(self, event: VoiceEvent) -> None:
        if event.type is VoiceEventType.AGENT_AUDIO and event.pcm16:
            self._metrics.mark_first_audio()
            self._last_activity = time.monotonic()
            if self._graph:
                pcm = np.frombuffer(event.pcm16, dtype="<i2")
                with contextlib.suppress(Exception):
                    self._graph.play_session_audio(pcm)
        elif event.type is VoiceEventType.AGENT_TRANSCRIPT and event.text:
            self._last_activity = time.monotonic()
            self._context.add_transcript("assistant", event.text)
            # Model-authored text is escape-stripped before it reaches the
            # terminal: an injection can steer the model into emitting OSC/CSI
            # sequences that spoof prompts or rewrite scrollback.
            print(f"Aegis: {_safe_console(event.text)}", flush=True)
        elif event.type is VoiceEventType.USER_TURN_STARTED:
            # Primary turn boundary. Resetting only on USER_TRANSCRIPT meant a
            # failing transcription pass permanently exhausted the per-turn
            # budget and every later tool call was refused.
            self._registry.reset_turn()
            self._last_activity = time.monotonic()
        elif event.type is VoiceEventType.USER_TRANSCRIPT and event.text:
            self._registry.reset_turn()
            self._last_activity = time.monotonic()
            self._context.add_transcript("user", event.text)
            print(f"You: {_safe_console(event.text)}", flush=True)
        elif event.type is VoiceEventType.TOOL_CALL and event.tool_call:
            # Arguments are model-chosen; escape them before echoing so a call
            # cannot forge extra console lines.
            print(
                f"[tool] {_safe_console(event.tool_call.name)}"
                f"({_safe_console(str(event.tool_call.arguments))})",
                file=sys.stderr,
            )
            self._metrics.tool_calls += 1
            self._last_activity = time.monotonic()
            # Hand off rather than await: this is the whole point of the split.
            self._tool_calls.put_nowait(event.tool_call)
        elif event.type is VoiceEventType.ERROR:
            self._metrics.errors += 1
            print(f"error: {_safe_console(str(event.message))}", file=sys.stderr)
        elif event.type is VoiceEventType.REMOTE_TOOL_ACTIVITY:
            # Remote MCP runs provider-side, so its results never pass through
            # our sanitizer. We cannot fence them; the least we can do is leave
            # a record that the untrusted surface was active.
            self._last_activity = time.monotonic()
            if self._audit is not None:
                self._audit.log(
                    "remote_mcp.activity",
                    session_id=self._machine.context.session_id,
                    tool_name=_safe_console(str(event.message or "mcp")),
                    risk="network",
                    decision="remote",
                )
        elif event.type is VoiceEventType.USAGE and event.usage:
            cost = self._metrics.add_usage(event.usage)
            log.info(
                "usage in_audio=%s out_audio=%s cached=%s cost~$%.5f",
                event.usage.input_audio_tokens,
                event.usage.output_audio_tokens,
                event.usage.cached_input_tokens,
                cost,
            )

    async def _tool_worker(self) -> None:
        while True:
            call = await self._tool_calls.get()
            self._tools_in_flight += 1
            try:
                result = await handle_tool_call(
                    call,
                    session=self._session,
                    registry=self._registry,
                    machine=self._machine,
                    cfg=self._cfg,
                    interactive_approval=self._interactive_approval,
                    approval_handler=self._approval_handler,
                )
                self._context.add_tool_result(call.name, result.output)
            finally:
                self._tools_in_flight -= 1
                self._last_activity = time.monotonic()
            if self._machine.state is SessionState.APPROVAL_PENDING:
                self._status.set_presence(Presence.APPROVAL)
            elif self._machine.state is SessionState.ACTIVE:
                self._status.set_presence(Presence.ACTIVE)


async def _uplink_loop(
    session: VoiceSession,
    graph: AudioGraph,
    machine: SessionMachine,
    stop: asyncio.Event,
) -> None:
    # Keep running through APPROVAL_PENDING (not just ACTIVE) so the mic isn't
    # permanently dead after the first approval prompt; frames are gated by the
    # mute_uplink flag below.
    while not stop.is_set() and machine.state in _UPLINK_ACTIVE_STATES:
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
    """Load user instructions, always with the security block appended.

    A custom instructions file replaces the persona, never the safety rules.
    """
    path = paths.instructions_file
    if path.is_file():
        return with_security_block(path.read_text(encoding="utf-8"))
    alt = Path(cfg.session.instructions_file)
    if alt.is_file():
        return with_security_block(alt.read_text(encoding="utf-8"))
    return with_security_block(None)


def run_session_once_sync(
    *,
    config_path: str | None = None,
    profile: str | None = None,
    backend: str = "realtime",
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
