"""Voice session provider factory."""

from __future__ import annotations

from typing import Any

from aegis.config.schema import AegisConfig
from aegis.util.secrets import resolve_api_key
from aegis.voice.gateway import CloudAudioGateway, default_gateway
from aegis.voice.gpt_live import GptLiveVoiceSession
from aegis.voice.mock import MockVoiceSession
from aegis.voice.protocol import VoiceSession
from aegis.voice.realtime import RealtimeVoiceSession
from aegis.voice.text_fallback import TextFallbackSession


def create_voice_session(
    cfg: AegisConfig,
    *,
    backend: str | None = None,
    paths=None,
    tools: list[dict[str, Any]] | None = None,
    gateway: CloudAudioGateway | None = None,
    instructions: str | None = None,
) -> VoiceSession:
    """Create a voice session for the configured or explicit backend."""
    gw = gateway or default_gateway
    if backend is not None:
        provider = backend
    else:
        provider = (
            cfg.session.provider.value
            if not isinstance(cfg.session.provider, str)
            else cfg.session.provider
        )

    if provider in {"mock"}:
        return MockVoiceSession(
            gateway=gw,
            register_gateway=True,
            reply_text="Mock Aegis online.",
        )

    if provider in {"gpt_live", "gpt-live"}:
        return GptLiveVoiceSession()

    if provider in {"text_fallback", "hybrid_text_tools"}:
        return TextFallbackSession(cfg=cfg)

    # default: realtime
    if paths is not None:
        key = resolve_api_key(
            env_var=cfg.openai.api_key_env,
            secrets_file=paths.secrets_env,
        )
    else:
        key = resolve_api_key(env_var=cfg.openai.api_key_env)

    return RealtimeVoiceSession(
        api_key=key,
        api_key_env=cfg.openai.api_key_env,
        base_url=cfg.openai.realtime_url,
        gateway=gw,
        tools=tools or [],
        instructions=instructions,
    )


def provider_status(cfg: AegisConfig) -> dict[str, Any]:
    return {
        "configured": cfg.session.provider.value,
        "model": cfg.session.model,
        "realtime_available": True,
        "gpt_live_available": False,
        "text_fallback_available": True,
    }
