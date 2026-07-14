"""Local settings web UI for multi-provider LLM configuration."""

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
from urllib.parse import parse_qs, urlparse

from aegis.config import default_paths, load_config
from aegis.config.env import env_status, load_dotenv, project_root, write_env_key
from aegis.config.save import apply_llm_settings, save_config
from aegis.llm import chatgpt_oauth as chatgpt_oauth_mod
from aegis.llm.chatgpt_oauth import clear_tokens, login_with_device_code, save_manual_token
from aegis.llm.registry import list_provider_catalog, probe_provider
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
        "openai_chat_base_url": cfg.openai.chat_base_url,
        "temperature": cfg.llm.temperature,
        "max_tokens": cfg.llm.max_tokens,
        "litellm_base_url": cfg.llm.litellm.base_url,
        "litellm_api_key_env": cfg.llm.litellm.api_key_env,
        "litellm_model": cfg.llm.litellm.model,
        "ollama_base_url": cfg.llm.ollama.base_url,
        "ollama_native_base_url": cfg.llm.ollama.native_base_url,
        "ollama_model": cfg.llm.ollama.model,
        "chatgpt_token_path": cfg.llm.chatgpt_oauth.token_path,
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


def _full_settings_payload() -> dict[str, Any]:
    load_dotenv()
    cfg = load_config(missing_ok=True)
    return {
        "settings": _settings_dict(cfg),
        "env": env_status(
            [
                "OPENAI_API_KEY",
                "LITELLM_API_KEY",
                "OLLAMA_API_KEY",
                "PICOVOICE_ACCESS_KEY",
                "OPENAI_REALTIME_URL",
                "AEGIS_PROFILE",
            ]
        ),
        "paths": _paths_info(),
        "catalog": list_provider_catalog(),
        "oauth": chatgpt_oauth_mod.status_dict(cfg.llm.chatgpt_oauth.token_path),
        "api_key_present": bool(
            resolve_api_key(
                env_var=cfg.openai.api_key_env,
                secrets_file=default_paths().secrets_env,
            )
        ),
    }


class SettingsHandler(BaseHTTPRequestHandler):
    server_version = "AegisSettings/0.2"

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
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)

        if path in {"/", "/settings", "/index.html"}:
            html = _HTML_PATH.read_bytes()
            self._send(200, html, "text/html; charset=utf-8")
            return

        if path == "/api/settings":
            self._json(200, _full_settings_payload())
            return

        if path == "/api/providers":
            self._json(200, {"catalog": list_provider_catalog()})
            return

        if path == "/api/probe":
            load_dotenv()
            cfg = load_config(missing_ok=True)
            provider = (qs.get("provider") or ["openai_api"])[0]
            self._json(200, probe_provider(cfg, provider))
            return

        if path == "/api/oauth/status":
            load_dotenv()
            cfg = load_config(missing_ok=True)
            self._json(200, chatgpt_oauth_mod.status_dict(cfg.llm.chatgpt_oauth.token_path))
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
                    openai_chat_base_url=body.get("openai_chat_base_url"),
                    litellm_base_url=body.get("litellm_base_url"),
                    litellm_api_key_env=body.get("litellm_api_key_env"),
                    litellm_model=body.get("litellm_model"),
                    ollama_base_url=body.get("ollama_base_url"),
                    ollama_native_base_url=body.get("ollama_native_base_url"),
                    ollama_model=body.get("ollama_model"),
                    chatgpt_token_path=body.get("chatgpt_token_path"),
                    temperature=body.get("temperature"),
                    max_tokens=body.get("max_tokens"),
                )
                save_config(updated, paths.config_file)
                payload = _full_settings_payload()
                payload["ok"] = True
                payload["config_path"] = str(paths.config_file)
                self._json(200, payload)
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

            if path == "/api/oauth/login":
                # Device-code login can take minutes — run and return result
                body = self._read_json()
                load_dotenv()
                cfg = load_config(missing_ok=True)
                open_browser = body.get("open_browser", True)
                result = login_with_device_code(
                    cfg.llm.chatgpt_oauth.token_path,
                    auth_base=cfg.llm.chatgpt_oauth.auth_base_url,
                    client_id=cfg.llm.chatgpt_oauth.client_id,
                    open_browser=bool(open_browser),
                )
                self._json(200, result)
                return

            if path == "/api/oauth/manual":
                body = self._read_json()
                load_dotenv()
                cfg = load_config(missing_ok=True)
                token = str(body.get("access_token") or "").strip()
                if not token:
                    raise ValueError("access_token required")
                result = save_manual_token(
                    cfg.llm.chatgpt_oauth.token_path,
                    token,
                    refresh_token=str(body.get("refresh_token") or ""),
                    email=str(body.get("email") or ""),
                )
                self._json(200, result)
                return

            if path == "/api/oauth/logout":
                load_dotenv()
                cfg = load_config(missing_ok=True)
                clear_tokens(cfg.llm.chatgpt_oauth.token_path)
                self._json(
                    200,
                    {
                        "ok": True,
                        "status": chatgpt_oauth_mod.status_dict(cfg.llm.chatgpt_oauth.token_path),
                    },
                )
                return

            if path == "/api/test-chat":
                body = self._read_json()
                load_dotenv()
                cfg = load_config(missing_ok=True)
                provider = str(body.get("provider") or cfg.session.provider.value)
                prompt = str(body.get("prompt") or "Say hello in one short sentence.")
                from aegis.llm.client import ChatMessage, create_llm_client

                client = create_llm_client(cfg, provider=provider)
                resp = client.chat_sync(
                    [
                        ChatMessage(role="system", content="You are Aegis."),
                        ChatMessage(role="user", content=prompt),
                    ]
                )
                self._json(
                    200,
                    {
                        "ok": True,
                        "provider": client.provider,
                        "model": client.model,
                        "text": resp.text,
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
