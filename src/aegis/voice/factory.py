"""Voice session provider factory."""

from __future__ import annotations

from typing import Any

from aegis.config.schema import AegisConfig, SessionProvider
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
    """Create a voice/chat session for the configured or explicit backend."""
    gw = gateway or default_gateway
    provider = backend if backend is not None else cfg.session.provider.value
    provider = str(provider).lower().replace("-", "_")

    if provider in {"mock"}:
        return MockVoiceSession(
            gateway=gw,
            register_gateway=True,
            reply_text="Mock Aegis online.",
        )

    if provider in {"gpt_live", "gptlive"}:
        return GptLiveVoiceSession()

    # Explicit text_fallback stub (legacy); hybrid uses chat client
    if provider in {"text_fallback"}:
        return TextFallbackSession(cfg=cfg)

    # Chat / OpenAI-compatible providers → text chat session
    if provider in {
        "ollama",
        "litellm",
        "chatgpt_oauth",
        "openai_api",
        "azure_openai",
        "azure",
        "bedrock",
        "aws_bedrock",
        "hybrid_text_tools",
    }:
        from aegis.llm.chat_session import ChatLLMSession

        # hybrid_text_tools may point chat at llm.chat_provider when set.
        chat_provider = provider
        if provider == "hybrid_text_tools":
            raw = getattr(cfg.llm, "chat_provider", None)
            if raw and str(raw) not in {"realtime", "hybrid_text_tools", "gpt_live"}:
                chat_provider = str(raw)
        return ChatLLMSession(cfg, provider=chat_provider, instructions=instructions)

    # default: realtime duplex
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
    from aegis.llm.chatgpt_oauth import status_dict
    from aegis.llm.registry import probe_provider

    current = cfg.session.provider.value
    return {
        "configured": current,
        "model": cfg.session.model,
        "realtime_available": bool(resolve_api_key(env_var=cfg.openai.api_key_env)),
        "chatgpt_oauth": status_dict(cfg.llm.chatgpt_oauth.token_path),
        "ollama": probe_provider(cfg, "ollama"),
        "litellm": probe_provider(cfg, "litellm"),
        # Both are stubs whose connect() raises NotImplementedError.
        "gpt_live_available": False,
        "text_fallback_available": False,
        "azure_openai": probe_provider(cfg, "azure_openai"),
        "bedrock": probe_provider(cfg, "bedrock"),
        "providers": [
            SessionProvider.REALTIME.value,
            SessionProvider.OPENAI_API.value,
            SessionProvider.CHATGPT_OAUTH.value,
            SessionProvider.LITELLM.value,
            SessionProvider.OLLAMA.value,
            SessionProvider.AZURE_OPENAI.value,
            SessionProvider.BEDROCK.value,
            SessionProvider.MOCK.value,
        ],
    }
