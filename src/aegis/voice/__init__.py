"""Voice backends: protocol, gateway, mock, Realtime (PR 8), GPT-Live stub."""

from aegis.voice.gateway import CloudAudioGateway, GatewayError, default_gateway
from aegis.voice.mock import MockVoiceSession
from aegis.voice.protocol import (
    ToolCallRequest,
    UsageSnapshot,
    VoiceEvent,
    VoiceEventType,
    VoiceSession,
)
from aegis.voice.realtime import RealtimeVoiceSession

__all__ = [
    "CloudAudioGateway",
    "GatewayError",
    "MockVoiceSession",
    "RealtimeVoiceSession",
    "ToolCallRequest",
    "UsageSnapshot",
    "VoiceEvent",
    "VoiceEventType",
    "VoiceSession",
    "default_gateway",
]
