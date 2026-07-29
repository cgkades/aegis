"""What transport each session provider actually speaks.

This lived as three parallel string sets — one in the session runner (also
imported by the daemon to pre-reject), one inside ``create_voice_session`` to
route, and the stub list. Drift between them meant either a text backend
entering the voice loop (a session the user cannot speak to, billed and
useless) or a supported backend being refused.
"""

from __future__ import annotations

from enum import StrEnum


class Transport(StrEnum):
    """How a provider can be driven."""

    VOICE = "voice"
    """Full-duplex audio: usable by the voice session loop."""

    TEXT = "text"
    """Text chat only — no microphone path is wired up."""

    UNIMPLEMENTED = "unimplemented"
    """A stub whose connect() raises; refuse before opening a session."""


_TRANSPORTS: dict[str, Transport] = {
    "realtime": Transport.VOICE,
    "mock": Transport.VOICE,
    "ollama": Transport.TEXT,
    "litellm": Transport.TEXT,
    "chatgpt_oauth": Transport.TEXT,
    "openai_api": Transport.TEXT,
    "azure_openai": Transport.TEXT,
    "azure": Transport.TEXT,
    "bedrock": Transport.TEXT,
    "aws_bedrock": Transport.TEXT,
    "hybrid_text_tools": Transport.TEXT,
    "text_fallback": Transport.UNIMPLEMENTED,
    "gpt_live": Transport.UNIMPLEMENTED,
    "gptlive": Transport.UNIMPLEMENTED,
}


def normalize_provider(name: object) -> str:
    """Canonical provider key (``Azure-OpenAI`` → ``azure_openai``)."""
    value = getattr(name, "value", name)
    return str(value).strip().lower().replace("-", "_")


def transport_for(name: object) -> Transport:
    """Transport for a provider; unknown names default to voice (realtime)."""
    return _TRANSPORTS.get(normalize_provider(name), Transport.VOICE)


def is_text_only(name: object) -> bool:
    return transport_for(name) is Transport.TEXT


def is_unimplemented(name: object) -> bool:
    return transport_for(name) is Transport.UNIMPLEMENTED


def is_voice_capable(name: object) -> bool:
    return transport_for(name) is Transport.VOICE


TEXT_ONLY_PROVIDERS: frozenset[str] = frozenset(
    key for key, transport in _TRANSPORTS.items() if transport is Transport.TEXT
)
UNIMPLEMENTED_PROVIDERS: frozenset[str] = frozenset(
    key for key, transport in _TRANSPORTS.items() if transport is Transport.UNIMPLEMENTED
)
