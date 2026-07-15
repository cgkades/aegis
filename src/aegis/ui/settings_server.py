"""Local settings web UI for multi-provider LLM configuration."""

from __future__ import annotations

import json
import secrets
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

# Per-process CSRF token. Injected into the served page and required (via a custom
# request header, which forces a CORS preflight) on every state-changing POST, so a
# malicious web page the user has open cannot drive the settings API cross-origin
# and exfiltrate API keys. Regenerated each server start.
_CSRF_TOKEN = secrets.token_urlsafe(32)

# Hosts we accept in the Host header — blocks DNS-rebinding attacks that resolve an
# attacker domain to 127.0.0.1 to reach this loopback server.
_ALLOWED_HOST_NAMES = {"127.0.0.1", "localhost", "[::1]", "::1"}

# Cap POST bodies so a local client cannot OOM the settings process.
_MAX_JSON_BODY_BYTES = 256 * 1024
_MAX_ENV_KEY_BODY_BYTES = 8 * 1024

# secrets.env is for API keys / provider credentials — not arbitrary process env.
_ALLOWED_ENV_KEYS = frozenset(
    {
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
        "LITELLM_API_KEY",
        "LITELLM_BASE_URL",
        "AZURE_OPENAI_API_KEY",
        "AZURE_OPENAI_ENDPOINT",
        "AZURE_OPENAI_API_VERSION",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
        "AWS_REGION",
        "AWS_DEFAULT_REGION",
        "BEDROCK_API_KEY",
        "PICOVOICE_ACCESS_KEY",
        "ANTHROPIC_API_KEY",
        "GOOGLE_API_KEY",
        "GROQ_API_KEY",
        "TOGETHER_API_KEY",
        "MISTRAL_API_KEY",
        "COHERE_API_KEY",
        "DEEPSEEK_API_KEY",
        "XAI_API_KEY",
        "OLLAMA_API_KEY",
        "OLLAMA_HOST",
    }
)


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
        "azure_endpoint": cfg.llm.azure_openai.endpoint,
        "azure_api_key_env": cfg.llm.azure_openai.api_key_env,
        "azure_api_version": cfg.llm.azure_openai.api_version,
        "azure_deployment": cfg.llm.azure_openai.deployment,
        "azure_api_style": cfg.llm.azure_openai.api_style,
        "azure_auth_mode": cfg.llm.azure_openai.auth_mode,
        "bedrock_region": cfg.llm.bedrock.region,
        "bedrock_model_id": cfg.llm.bedrock.model_id,
        "bedrock_profile": cfg.llm.bedrock.profile,
        "bedrock_endpoint_url": cfg.llm.bedrock.endpoint_url,
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
                "AZURE_OPENAI_API_KEY",
                "AWS_ACCESS_KEY_ID",
                "AWS_SECRET_ACCESS_KEY",
                "AWS_SESSION_TOKEN",
                "AWS_REGION",
                "AWS_PROFILE",
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

    def _read_json(self, *, max_bytes: int = _MAX_JSON_BODY_BYTES) -> dict[str, Any]:
        # Require exactly application/json — a cross-origin "simple" POST cannot set
        # this content type without triggering a CORS preflight, so this (with the
        # CSRF check) blocks drive-by requests from other web pages.
        ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if ctype != "application/json":
            raise ValueError("content-type must be application/json")
        length = int(self.headers.get("Content-Length") or 0)
        if length < 0:
            raise ValueError("invalid content-length")
        if length > max_bytes:
            raise ValueError(f"body too large (max {max_bytes} bytes)")
        raw = self.rfile.read(length) if length else b"{}"
        if len(raw) > max_bytes:
            raise ValueError(f"body too large (max {max_bytes} bytes)")
        try:
            data = json.loads(raw.decode("utf-8") or "{}")
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid json: {exc}") from exc
        if not isinstance(data, dict):
            raise ValueError("json body must be an object")
        return data

    def _host_ok(self) -> bool:
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip().lower()
        return host in _ALLOWED_HOST_NAMES

    def _csrf_ok(self) -> bool:
        token = self.headers.get("X-Aegis-CSRF") or ""
        return secrets.compare_digest(token, _CSRF_TOKEN)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)

        if not self._host_ok():
            self._json(403, {"error": "bad_host"})
            return

        if path in {"/", "/settings", "/index.html"}:
            html = _HTML_PATH.read_text(encoding="utf-8").replace(
                "__AEGIS_CSRF_TOKEN__", _CSRF_TOKEN
            )
            self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")
            return

        if path == "/api/settings":
            self._json(200, _full_settings_payload())
            return

        if path == "/api/providers":
            self._json(200, {"catalog": list_provider_catalog()})
            return

        try:
            self._do_get_api(path, qs)
        except Exception as exc:
            log.exception("settings GET api error")
            self._json(500, {"error": str(exc)})

    def _do_get_api(self, path: str, qs: dict[str, list[str]]) -> None:
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
        if not self._host_ok():
            self._json(403, {"error": "bad_host"})
            return
        if not self._csrf_ok():
            self._json(403, {"error": "csrf_failed"})
            return
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
                    azure_endpoint=body.get("azure_endpoint"),
                    azure_api_key_env=body.get("azure_api_key_env"),
                    azure_api_version=body.get("azure_api_version"),
                    azure_deployment=body.get("azure_deployment"),
                    azure_api_style=body.get("azure_api_style"),
                    azure_auth_mode=body.get("azure_auth_mode"),
                    bedrock_region=body.get("bedrock_region"),
                    bedrock_model_id=body.get("bedrock_model_id"),
                    bedrock_profile=body.get("bedrock_profile"),
                    bedrock_endpoint_url=body.get("bedrock_endpoint_url"),
                )
                save_config(updated, paths.config_file)
                payload = _full_settings_payload()
                payload["ok"] = True
                payload["config_path"] = str(paths.config_file)
                self._json(200, payload)
                return

            if path == "/api/env-key":
                body = self._read_json(max_bytes=_MAX_ENV_KEY_BODY_BYTES)
                key = str(body.get("key") or "OPENAI_API_KEY").strip()
                value = str(body.get("value") or "").strip()
                if not key or not value:
                    raise ValueError("key and value required")
                if any(c in key for c in " =\n\r"):
                    raise ValueError("invalid key name")
                if key not in _ALLOWED_ENV_KEYS and not (
                    key.endswith("_API_KEY") or key.endswith("_ACCESS_KEY")
                ):
                    raise ValueError(
                        f"key {key!r} not allowed in secrets.env "
                        "(use known provider key names)"
                    )
                # Write to the user's secrets file (inside the 0700 config dir),
                # not $CWD/.env — the server may be launched from anywhere and we
                # must not scatter API keys into arbitrary working directories.
                env_path = default_paths().secrets_env
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
    # The settings API can read/write secrets and API keys; keep it loopback-only.
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError(
            f"refusing to bind settings server to non-loopback host {host!r}; "
            "it exposes API keys and secrets. Use 127.0.0.1."
        )
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
