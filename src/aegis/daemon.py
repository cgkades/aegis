"""Always-on Aegis daemon: wake loop + IPC + session start."""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import sys
from pathlib import Path
from typing import Any

from aegis.audio import AudioGraph, AudioGraphConfig, sounddevice_available
from aegis.audit import AuditLogger
from aegis.config import AegisConfig, default_paths, load_config
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
from aegis.session.runner import run_session_once
from aegis.util.logging import get_logger, setup_logging
from aegis.voice.gateway import default_gateway
from aegis.wake import ConfirmSpeechGate, MockWakeEngine
from aegis.wake.factory import create_wake_engine

log = get_logger("daemon")


class AegisDaemon:
    """Long-lived process: local KWS (or mock) + unix socket control."""

    def __init__(self, cfg: AegisConfig, paths: AegisPaths) -> None:
        self.cfg = cfg
        self.paths = paths
        self.machine = SessionMachine()
        self._stop = asyncio.Event()
        self._session_task: asyncio.Task[int] | None = None
        self._server: asyncio.Server | None = None
        self._graph: AudioGraph | None = None
        self._wake = None
        self._confirm = ConfirmSpeechGate(
            timeout_s=cfg.wake.confirm_speech_timeout_s,
            sample_rate_hz=cfg.audio.wake_sample_rate_hz,
        )
        self.audit = AuditLogger(
            paths.audit_dir,
            redact=cfg.privacy.redact_secrets_in_audit,
        )

    async def start(self) -> None:
        self.paths.ensure_dirs()
        remove_stale_socket(self.paths.socket_path)
        write_pid(self.paths.pid_file)

        if self.cfg.wake.enabled:
            try:
                self._wake = create_wake_engine(self.cfg.wake)
                self._wake.start()
            except Exception as exc:
                log.warning("wake engine failed (%s); using mock energy trigger", exc)
                self._wake = MockWakeEngine(
                    phrase=self.cfg.wake.phrase,
                    energy_threshold=8000.0,
                )
                self._wake.start()

        if sounddevice_available() and self.cfg.wake.enabled:
            try:
                self._graph = AudioGraph(AudioGraphConfig.from_audio_config(self.cfg.audio))
                self._graph.start()
            except Exception as exc:
                log.warning("capture unavailable: %s", exc)
                self._graph = None

        self._server = await asyncio.start_unix_server(
            self._handle_client,
            path=str(self.paths.socket_path),
        )
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
                await asyncio.sleep(0.1)
                continue
            frame = await asyncio.to_thread(self._graph.capture.read, 0.2)
            if frame is None:
                continue
            wake_pcm = self._graph.to_wake_rate(frame)
            # Confirm path
            if self._confirm.waiting:
                confirmed = self._confirm.process_audio(wake_pcm)
                if confirmed is not None:
                    await self._start_session(source="wake", skip_confirm=True)
                continue

            try:
                event = self._wake.process(wake_pcm)
            except Exception as exc:
                log.debug("wake process: %s", exc)
                continue
            if event is None:
                continue

            if self.cfg.wake.confirm_speech_timeout_s > 0:
                self._confirm.on_wake(event)
                log.info("wake hit score=%.2f — waiting for speech confirm", event.score)
            else:
                await self._start_session(source="wake", skip_confirm=True)

    async def _start_session(self, *, source: str, skip_confirm: bool = True) -> dict[str, Any]:
        if self.machine.state is not SessionState.IDLE:
            return {"started": False, "reason": f"busy:{self.machine.state.value}"}
        if self._session_task and not self._session_task.done():
            return {"started": False, "reason": "session_running"}

        # Foreground runner owns its own machine; daemon marks busy via flag
        self.machine.trigger(
            Trigger.CLI_START if source != "wake" else Trigger.WAKE_WORD,
            skip_confirm=skip_confirm,
        )
        # Immediately move to connecting-ish busy state then reset after task
        # Use a simplified approach: set a "running" task and stay non-IDLE via ACTIVE
        # by faking connect path for IPC status only
        self.audit.log("session_request", extra={"source": source})

        async def _run() -> int:
            try:
                # Reset machine to idle for runner which creates its own machine
                # Keep daemon machine in ACTIVE-like busy: use CONNECTING+SESSION_READY
                if self.machine.state is SessionState.WAKING:
                    self.machine.trigger(Trigger.CAPTURE_READY)
                if self.machine.state is SessionState.CONNECTING:
                    self.machine.trigger(Trigger.SESSION_READY)
                code = await run_session_once(
                    self.cfg,
                    backend="realtime",
                    paths=self.paths,
                )
                return code
            finally:
                # Return daemon machine to idle
                if self.machine.state is SessionState.ACTIVE:
                    self.machine.trigger(Trigger.HOTKEY_END)
                if self.machine.state is SessionState.ENDING:
                    self.machine.trigger(Trigger.TEARDOWN_DONE)
                if self.machine.state is not SessionState.IDLE:
                    # Force idle by re-creating
                    self.machine = SessionMachine()
                if self._wake:
                    with contextlib.suppress(Exception):
                        self._wake.reset()
                self._confirm.clear()

        self._session_task = asyncio.create_task(_run(), name="session")
        return {
            "started": True,
            "session_id": self.machine.context.session_id,
            "source": source,
        }

    async def _handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        try:
            line = await asyncio.wait_for(reader.readline(), timeout=10.0)
            if not line:
                return
            req = parse_request(line.decode("utf-8"))
            resp = await self._dispatch_ipc(req.op, req.id, req.params or {})
            writer.write(resp.to_line().encode("utf-8"))
            await writer.drain()
        except Exception as exc:
            log.debug("ipc client error: %s", exc)
            try:
                err = IpcResponse(id="1", ok=False, error=str(exc))
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
            return IpcResponse(
                req_id,
                True,
                {
                    "state": self.machine.state.value,
                    "session_id": self.machine.context.session_id,
                    "wake_enabled": self.cfg.wake.enabled,
                    "cloud_open": default_gateway.is_open,
                    "pid": os.getpid(),
                },
            )
        if op == "session.start":
            result = await self._start_session(source=params.get("source", "ipc"))
            return IpcResponse(req_id, True, result)
        if op == "session.stop":
            if self._session_task and not self._session_task.done():
                self._session_task.cancel()
                return IpcResponse(req_id, True, {"stopping": True})
            return IpcResponse(req_id, True, {"stopping": False, "reason": "no_session"})
        if op == "shutdown":
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
    cfg = load_config(
        Path(config_path) if config_path else None,
        paths=paths,
        profile=profile,
        missing_ok=True,
    )
    setup_logging(cfg.app.log_level)

    # Single instance check
    existing = read_pid(paths.pid_file)
    if existing and pid_alive(existing) and existing != os.getpid():
        print(f"aegisd already running pid={existing}", file=sys.stderr)
        return 1

    daemon = AegisDaemon(cfg, paths)

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
