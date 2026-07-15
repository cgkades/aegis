"""Always-on Aegis daemon: wake loop + IPC + session start."""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aegis.approval.broker import ApprovalBroker
from aegis.audio import AudioGraph, AudioGraphConfig, sounddevice_available
from aegis.audit import AuditLogger
from aegis.config import AegisConfig, ConfigError, default_paths, load_config
from aegis.config.paths import AegisPaths
from aegis.ipc import (
    IpcResponse,
    parse_request,
    pid_alive,
    read_pid,
    remove_stale_socket,
    write_pid,
)
from aegis.session.events import SessionState, Trigger
from aegis.session.machine import SessionMachine
from aegis.session.runner import TEXT_ONLY_BACKENDS, run_session_once
from aegis.util.logging import get_logger, setup_logging
from aegis.voice.gateway import default_gateway
from aegis.wake import ConfirmSpeechGate
from aegis.wake.base import WakeEngine
from aegis.wake.factory import create_wake_engine

log = get_logger("daemon")


@dataclass(slots=True)
class ConfigReloadResult:
    cfg: AegisConfig
    error: str | None = None
    restart_required: bool = False


class AegisDaemon:
    """Long-lived process: local KWS (or mock) + unix socket control."""

    def __init__(
        self,
        cfg: AegisConfig,
        paths: AegisPaths,
        *,
        config_path: Path | None = None,
        profile: str | None = None,
    ) -> None:
        self.cfg = cfg
        self.paths = paths
        self._config_path = config_path
        self._profile = profile
        # Busy/idle façade for wake + IPC; the session runner owns the real SM.
        self.machine = SessionMachine()
        self._stop = asyncio.Event()
        self._session_task: asyncio.Task[int] | None = None
        self._server: asyncio.Server | None = None
        self._graph: AudioGraph | None = None
        self._wake: WakeEngine | None = None
        self._wake_lock = threading.RLock()
        self._wake_generation = 0
        self._wake_config_restart_required = False
        self._confirm = ConfirmSpeechGate(
            timeout_s=cfg.wake.confirm_speech_timeout_s,
            sample_rate_hz=cfg.audio.wake_sample_rate_hz,
        )
        self.audit = AuditLogger(
            paths.audit_dir,
            redact=cfg.privacy.redact_secrets_in_audit,
        )
        self.approvals = ApprovalBroker(timeout_s=cfg.tools.approval.timeout_s)

    async def start(self) -> None:
        self.paths.ensure_dirs()
        remove_stale_socket(self.paths.socket_path)
        write_pid(self.paths.pid_file)

        if self.cfg.wake.enabled:
            try:
                self._wake = create_wake_engine(self.cfg.wake)
                self._wake.start()
            except Exception as exc:
                # Do NOT silently substitute an energy trigger: that would open a
                # billed cloud session on any loud noise. Disable wake instead and
                # tell the user how to start a session manually.
                log.error(
                    "wake engine %r failed to start (%s); wake DISABLED. "
                    "Install the engine (see docs) or start a session with "
                    "`aegis session start`.",
                    self.cfg.wake.engine.value,
                    exc,
                )
                print(
                    f"aegisd: wake engine unavailable ({exc}); wake disabled. "
                    "Use `aegis session start` to talk to Aegis.",
                    file=sys.stderr,
                )
                self._wake = None

        if sounddevice_available() and self.cfg.wake.enabled:
            try:
                self._graph = AudioGraph(AudioGraphConfig.from_audio_config(self.cfg.audio))
                # Capture-only: the wake loop never plays audio, and holding an
                # unused output stream open 24/7 blocks device power-down.
                self._graph.start(capture_only=True)
            except Exception as exc:
                log.warning("capture unavailable: %s", exc)
                self._graph = None

        # Restrict the socket's permissions from creation (not just after) so there
        # is no window where another local user could connect. The 0700 state dir
        # already blocks traversal; this is defense-in-depth.
        old_umask = os.umask(0o177)
        try:
            self._server = await asyncio.start_unix_server(
                self._handle_client,
                path=str(self.paths.socket_path),
            )
        finally:
            os.umask(old_umask)
        try:
            os.chmod(self.paths.socket_path, 0o600)
        except OSError:
            pass

        log.info(
            "aegisd listening on %s (wake=%s)",
            self.paths.socket_path,
            self.cfg.wake.enabled,
        )
        print(f"aegisd ready socket={self.paths.socket_path}", file=sys.stderr)

        wake_task = asyncio.create_task(self._wake_loop(), name="wake-loop")
        try:
            await self._stop.wait()
        finally:
            wake_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await wake_task
            await self._shutdown()

    async def _shutdown(self) -> None:
        if self._session_task and not self._session_task.done():
            self._session_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._session_task
        if self._server:
            self._server.close()
            await self._server.wait_closed()
        with self._wake_lock:
            if self._graph:
                self._graph.stop()
            if self._wake:
                with contextlib.suppress(Exception):
                    self._wake.stop()
        remove_stale_socket(self.paths.socket_path)
        if self.paths.pid_file.is_file():
            with contextlib.suppress(OSError):
                self.paths.pid_file.unlink()
        with contextlib.suppress(Exception):
            default_gateway.assert_idle_has_no_cloud()
        log.info("aegisd stopped")

    async def _wake_loop(self) -> None:
        if not self.cfg.wake.enabled or self._graph is None or self._wake is None:
            await self._stop.wait()
            return

        while not self._stop.is_set():
            if self.machine.state is not SessionState.IDLE:
                # A session is running — wait for it to finish instead of polling
                # machine.state 10×/sec for its whole (possibly 15-min) duration.
                task = self._session_task
                if task is not None and not task.done():
                    with contextlib.suppress(Exception):
                        await asyncio.wait({task}, timeout=1.0)
                else:
                    await asyncio.sleep(0.1)
                continue
            generation = self._wake_generation
            frame = await asyncio.to_thread(self._graph.capture.read, 0.2)
            if frame is None:
                # Still advance confirm deadline so a starved capture queue
                # cannot leave confirm.waiting stuck forever.
                with self._wake_lock:
                    if self._confirm.waiting and self._confirm.poll_timeout():
                        log.info("wake confirm timed out (no audio frames)")
                continue

            # Resample + KWS off the event loop so IPC stays responsive (F018).
            try:
                event, confirmed = await asyncio.to_thread(
                    self._wake_process_frame, frame
                )
            except Exception as exc:
                log.debug("wake process: %s", exc)
                continue

            # A manually started session can overlap a frame already handed to
            # the worker. Never act on a detection produced before that session.
            if generation != self._wake_generation or self.machine.state is not SessionState.IDLE:
                continue
            if confirmed is not None:
                await self._start_session(source="wake", skip_confirm=True)
                continue
            if event is None:
                continue

            if self.cfg.wake.confirm_speech_timeout_s > 0:
                self._confirm.on_wake(event)
                log.info("wake hit score=%.2f — waiting for speech confirm", event.score)
            else:
                await self._start_session(source="wake", skip_confirm=True)

    def _wake_process_frame(self, frame: object) -> tuple[object | None, object | None]:
        """CPU-heavy wake path (runs in a worker thread)."""
        with self._wake_lock:
            assert self._graph is not None and self._wake is not None
            wake_pcm = self._graph.to_wake_rate(frame)  # type: ignore[arg-type]
            if self._confirm.waiting:
                return None, self._confirm.process_audio(wake_pcm)
            return self._wake.process(wake_pcm), None

    def _reload_config(self) -> ConfigReloadResult:
        """Reload settings that can safely change without restarting the daemon."""
        try:
            cfg = load_config(
                self._config_path,
                paths=self.paths,
                profile=self._profile,
                missing_ok=True,
            )
            restart_required = cfg.wake != self.cfg.wake or cfg.audio != self.cfg.audio
            if restart_required:
                # Wake/audio resources are created once. Reporting a requested
                # value as live would violate the local-only wake contract, so
                # retain the running values until a restart applies the file.
                cfg = cfg.model_copy(
                    update={
                        "wake": self.cfg.wake.model_copy(deep=True),
                        "audio": self.cfg.audio.model_copy(deep=True),
                    }
                )
                log.warning("wake/audio settings changed; daemon restart required")
            self.cfg = cfg
            self._wake_config_restart_required = restart_required
            self.approvals.set_timeout(cfg.tools.approval.timeout_s)
            self.audit = AuditLogger(
                self.paths.audit_dir,
                redact=cfg.privacy.redact_secrets_in_audit,
            )
            log.info("config reloaded profile=%s", cfg.profile.name.value)
            return ConfigReloadResult(cfg, restart_required=restart_required)
        except ConfigError as exc:
            log.warning("config reload failed; keeping previous: %s", exc)
            return ConfigReloadResult(self.cfg, error=str(exc))

    async def _start_session(self, *, source: str, skip_confirm: bool = True) -> dict[str, Any]:
        if self.machine.state is not SessionState.IDLE:
            return {"started": False, "reason": f"busy:{self.machine.state.value}"}
        if self._session_task and not self._session_task.done():
            return {"started": False, "reason": "session_running"}

        reload_result = self._reload_config()
        if reload_result.error:
            return {
                "started": False,
                "reason": "config_reload_failed",
                "detail": reload_result.error,
            }
        cfg = reload_result.cfg
        provider = str(cfg.session.provider.value)
        if provider.lower().replace("-", "_") in TEXT_ONLY_BACKENDS:
            return {
                "started": False,
                "reason": f"text_only_provider:{provider}",
                "hint": "set session.provider to realtime, mock, or gpt_live",
            }

        # The runner owns its own state machine. The daemon advances its own
        # machine out of IDLE only so the wake loop and IPC status report "busy"
        # for the duration of the session task (reset to IDLE in _run's finally).
        self.machine.trigger(
            Trigger.CLI_START if source != "wake" else Trigger.WAKE_WORD,
            skip_confirm=skip_confirm,
        )
        self._wake_generation += 1
        self.audit.log("session_request", extra={"source": source})

        async def _run() -> int:
            try:
                if self.machine.state is SessionState.WAKING:
                    self.machine.trigger(Trigger.CAPTURE_READY)
                if self.machine.state is SessionState.CONNECTING:
                    self.machine.trigger(Trigger.SESSION_READY)
                if self._graph is not None:
                    with contextlib.suppress(Exception):
                        self._graph.playback.start()
                    # Reset VAD hangover so the next session does not inherit state.
                    with contextlib.suppress(Exception):
                        self._graph.vad.reset()
                code = await run_session_once(
                    cfg,
                    backend=cfg.session.provider.value,
                    paths=self.paths,
                    graph=self._graph,
                    install_signal_handlers=False,
                    interactive_approval=False,
                    approval_handler=self.approvals.request,
                )
                return code
            finally:
                self.approvals.cancel_all(reason="session_ended")
                if self.machine.state is SessionState.ACTIVE:
                    self.machine.trigger(Trigger.HOTKEY_END)
                if self.machine.state is SessionState.ENDING:
                    self.machine.trigger(Trigger.TEARDOWN_DONE)
                if self.machine.state is not SessionState.IDLE:
                    self.machine = SessionMachine()
                if self._graph is not None:
                    with contextlib.suppress(Exception):
                        self._graph.playback.stop()
                    with contextlib.suppress(Exception):
                        self._graph.vad.reset()
                with self._wake_lock:
                    if self._wake:
                        with contextlib.suppress(Exception):
                            self._wake.reset()
                    self._confirm.clear()

        self._session_task = asyncio.create_task(_run(), name="session")
        self._session_task.add_done_callback(self._on_session_done)
        return {
            "started": True,
            "session_id": self.machine.context.session_id,
            "source": source,
        }

    def _on_session_done(self, task: asyncio.Task[int]) -> None:
        """Surface session-task failures instead of losing them to GC."""
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            log.error("session task failed: %s", exc, exc_info=exc)
            with contextlib.suppress(Exception):
                self.audit.log("session_error", extra={"error": str(exc)})

    async def _handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        req_id = "0"
        try:
            line = await asyncio.wait_for(reader.readline(), timeout=10.0)
            if not line:
                return
            req = parse_request(line.decode("utf-8"))
            req_id = req.id
            resp = await self._dispatch_ipc(req.op, req.id, req.params or {})
            writer.write(resp.to_line().encode("utf-8"))
            await writer.drain()
        except Exception as exc:
            log.debug("ipc client error: %s", exc)
            try:
                err = IpcResponse(id=req_id, ok=False, error=str(exc))
                writer.write(err.to_line().encode("utf-8"))
                await writer.drain()
            except Exception:
                pass
        finally:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()

    async def _dispatch_ipc(
        self,
        op: str,
        req_id: str,
        params: dict[str, Any],
    ) -> IpcResponse:
        if op == "ping":
            return IpcResponse(req_id, True, {"pong": True})
        if op == "status":
            busy = self._session_task is not None and not self._session_task.done()
            return IpcResponse(
                req_id,
                True,
                {
                    "state": "active" if busy else self.machine.state.value,
                    "session_id": self.machine.context.session_id,
                    "wake_enabled": self._wake is not None and self._graph is not None,
                    "wake_restart_required": self._wake_config_restart_required,
                    "cloud_open": default_gateway.is_open,
                    "pending_approvals": self.approvals.list_pending(),
                    "pid": os.getpid(),
                },
            )
        if op == "session.start":
            result = await self._start_session(source=params.get("source", "ipc"))
            ok = bool(result.get("started"))
            return IpcResponse(req_id, ok, result, error=None if ok else result.get("reason"))
        if op in {"session.stop", "session.end"}:
            if self._session_task and not self._session_task.done():
                self.approvals.cancel_all(reason="session_stop")
                self._session_task.cancel()
                return IpcResponse(req_id, True, {"stopping": True})
            return IpcResponse(req_id, True, {"stopping": False, "reason": "no_session"})
        if op == "approval.list":
            return IpcResponse(req_id, True, {"pending": self.approvals.list_pending()})
        if op == "approval.respond":
            call_id_value = params.get("call_id")
            # Legacy clients used the top-level request id as the call id.
            call_id = str(call_id_value) if call_id_value is not None else req_id
            provided_allowed = [
                params[key] for key in ("allowed", "allow") if key in params
            ]
            if not provided_allowed:
                return IpcResponse(req_id, False, error="allowed_required")
            if len(provided_allowed) == 2 and provided_allowed[0] != provided_allowed[1]:
                return IpcResponse(req_id, False, error="conflicting_allowed")
            allowed = provided_allowed[0]
            if type(allowed) is not bool:
                return IpcResponse(req_id, False, error="allowed_must_be_boolean")
            raw_grant = params.get("grant_scope", params.get("scope", "once"))
            grant_map = {"once": "once", "same_tool": "same_tool", "tool": "same_tool"}
            if not isinstance(raw_grant, str) or raw_grant not in grant_map:
                return IpcResponse(req_id, False, error="invalid_grant_scope")
            grant = grant_map[raw_grant]
            if not call_id:
                return IpcResponse(req_id, False, error="call_id_required")
            ok = self.approvals.respond(
                call_id,
                allowed=allowed,
                grant_scope=grant,  # type: ignore[arg-type]
                reason=str(params.get("reason") or ""),
            )
            return IpcResponse(
                req_id,
                ok,
                {"resolved": ok, "call_id": call_id, "allowed": allowed},
                error=None if ok else "unknown_or_resolved_call_id",
            )
        if op == "config.reload":
            reload_result = self._reload_config()
            if reload_result.error:
                return IpcResponse(req_id, False, error=reload_result.error)
            cfg = reload_result.cfg
            return IpcResponse(
                req_id,
                True,
                {
                    "profile": cfg.profile.name.value,
                    "provider": cfg.session.provider.value,
                    "restart_required": reload_result.restart_required,
                },
            )
        if op == "shutdown":
            self.approvals.cancel_all(reason="shutdown")
            self._stop.set()
            return IpcResponse(req_id, True, {"shutdown": True})
        return IpcResponse(req_id, False, error=f"unknown_op:{op}")


def run_daemon(
    *,
    config_path: str | None = None,
    profile: str | None = None,
) -> int:
    """Entry point for `aegis daemon` / systemd."""
    setup_logging("info")
    paths = default_paths()
    try:
        cfg = load_config(
            Path(config_path) if config_path else None,
            paths=paths,
            profile=profile,
            missing_ok=True,
        )
    except ConfigError as exc:
        # Exit code 78 (EX_CONFIG). Paired with StartLimitBurst in the systemd
        # unit, this stops a bad config file from crash-looping every RestartSec.
        print(f"aegisd: configuration error: {exc}", file=sys.stderr)
        return 78
    setup_logging(cfg.app.log_level)

    # First-boot soft start: missing keys/wake models are OK, but log clearly so
    # operators are not surprised that cloud/wake paths need configuration.
    from aegis.util.secrets import resolve_api_key

    if not resolve_api_key(env_var=cfg.openai.api_key_env, secrets_file=paths.secrets_env):
        log.warning(
            "API key %s not set (env or %s); cloud voice sessions will fail until configured",
            cfg.openai.api_key_env,
            paths.secrets_env,
        )
    if cfg.wake.enabled:
        log.info(
            "wake enabled engine=%s phrase=%r — install engine deps and models as needed; "
            "daemon continues if wake fails to start",
            cfg.wake.engine.value,
            cfg.wake.phrase,
        )

    # Single instance check
    existing = read_pid(paths.pid_file)
    if existing and pid_alive(existing) and existing != os.getpid():
        print(f"aegisd already running pid={existing}", file=sys.stderr)
        return 1

    daemon = AegisDaemon(
        cfg,
        paths,
        config_path=Path(config_path).expanduser() if config_path else None,
        profile=profile,
    )

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    def _stop(*_args: object) -> None:
        daemon._stop.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, _stop)

    try:
        loop.run_until_complete(daemon.start())
    finally:
        loop.close()
    return 0
