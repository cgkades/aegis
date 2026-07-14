"""Local settings web UI for LLM / Realtime configuration."""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import webbrowser
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from aegis.config import default_paths, load_config
from aegis.config.env import (
    env_status,
    load_dotenv,
    project_root,
    write_env_key,
)
from aegis.config.save import apply_llm_settings, save_config
from aegis.util.logging import get_logger
from aegis.util.secrets import resolve_api_key

log = get_logger("ui.settings")

_HTML_PATH = Path(__file__).with_name("settings_page.html")


def _settings_dict(cfg) -> dict[str, Any]:
    return {
        "profile": cfg.profile.name.value
        if hasattr(cfg.profile.name, "value")
        else str(cfg.profile.name),
        "provider": cfg.session.provider.value
        if hasattr(cfg.session.provider, "value")
        else str(cfg.session.provider),
        "model": cfg.session.model,
        "voice": cfg.session.voice,
        "reasoning_effort": cfg.session.reasoning_effort,
        "max_session_cost_usd": cfg.session.max_session_cost_usd,
        "max_duration_s": cfg.session.max_duration_s,
        "idle_timeout_s": cfg.session.idle_timeout_s,
        "log_level": cfg.app.log_level,
        "api_key_env": cfg.openai.api_key_env,
        "realtime_url": cfg.openai.realtime_url,
    }


def _paths_info() -> dict[str, str]:
    paths = default_paths()
    return {
        "config_file": str(paths.config_file),
        "config_dir": str(paths.config_dir),
        "project_env": str(project_root() / ".env"),
        "user_env": str(paths.config_dir / ".env"),
        "secrets_env": str(paths.secrets_env),
    }


class SettingsHandler(BaseHTTPRequestHandler):
    server_version = "AegisSettings/0.1"

    def log_message(self, fmt: str, *args: object) -> None:
        log.debug("%s - %s", self.address_string(), fmt % args)

    def _send(self, code: int, body: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, payload: dict[str, Any]) -> None:
        data = json.dumps(payload).encode("utf-8")
        self._send(code, data, "application/json; charset=utf-8")

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            data = json.loads(raw.decode("utf-8") or "{}")
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid json: {exc}") from exc
        if not isinstance(data, dict):
            raise ValueError("json body must be an object")
        return data

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path in {"/", "/settings", "/index.html"}:
            html = _HTML_PATH.read_bytes()
            self._send(200, html, "text/html; charset=utf-8")
            return
        if path == "/api/settings":
            load_dotenv()
            cfg = load_config(missing_ok=True)
            self._json(
                200,
                {
                    "settings": _settings_dict(cfg),
                    "env": env_status(),
                    "paths": _paths_info(),
                    "api_key_present": bool(
                        resolve_api_key(
                            env_var=cfg.openai.api_key_env,
                            secrets_file=default_paths().secrets_env,
                        )
                    ),
                },
            )
            return
        if path == "/api/doctor":
            out = subprocess.run(
                [sys.executable, "-m", "aegis", "doctor"],
                capture_output=True,
                text=True,
                check=False,
            )
            self._json(
                200,
                {
                    "ok": out.returncode == 0,
                    "output": (out.stdout or "") + (out.stderr or ""),
                },
            )
            return
        self._json(404, {"error": "not_found"})

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        try:
            if path == "/api/settings":
                body = self._read_json()
                load_dotenv()
                paths = default_paths()
                cfg = load_config(paths=paths, missing_ok=True)
                updated = apply_llm_settings(
                    cfg,
                    profile=body.get("profile"),
                    provider=body.get("provider"),
                    model=body.get("model"),
                    voice=body.get("voice"),
                    reasoning_effort=body.get("reasoning_effort"),
                    max_session_cost_usd=body.get("max_session_cost_usd"),
                    max_duration_s=body.get("max_duration_s"),
                    idle_timeout_s=body.get("idle_timeout_s"),
                    api_key_env=body.get("api_key_env"),
                    realtime_url=body.get("realtime_url"),
                    log_level=body.get("log_level"),
                )
                save_config(updated, paths.config_file)
                self._json(
                    200,
                    {
                        "ok": True,
                        "settings": _settings_dict(updated),
                        "env": env_status(),
                        "paths": _paths_info(),
                        "config_path": str(paths.config_file),
                    },
                )
                return

            if path == "/api/env-key":
                body = self._read_json()
                key = str(body.get("key") or "OPENAI_API_KEY").strip()
                value = str(body.get("value") or "").strip()
                if not key or not value:
                    raise ValueError("key and value required")
                if any(c in key for c in " =\n\r"):
                    raise ValueError("invalid key name")
                env_path = project_root() / ".env"
                write_env_key(env_path, key, value)
                load_dotenv(override=True)
                self._json(
                    200,
                    {
                        "ok": True,
                        "env_path": str(env_path),
                        "env": env_status(),
                        "paths": _paths_info(),
                    },
                )
                return

            if path == "/api/test-mock":
                out = subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "aegis",
                        "session",
                        "once",
                        "--backend",
                        "mock",
                        "--max-seconds",
                        "3",
                    ],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self._json(
                    200,
                    {
                        "ok": out.returncode == 0,
                        "output": (out.stdout or "") + (out.stderr or ""),
                    },
                )
                return

            self._json(404, {"error": "not_found"})
        except Exception as exc:
            log.exception("settings api error")
            self._json(400, {"error": str(exc)})


def run_settings_server(
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    open_browser: bool = True,
) -> int:
    """Start the settings HTTP server (blocking)."""
    load_dotenv()
    httpd = ThreadingHTTPServer((host, port), SettingsHandler)
    url = f"http://{host}:{port}/"
    print(f"Aegis settings: {url}", file=sys.stderr)
    print("Press Ctrl+C to stop.", file=sys.stderr)
    if open_browser:
        threading.Timer(0.4, partial(webbrowser.open, url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping settings server.", file=sys.stderr)
    finally:
        httpd.server_close()
    return 0
