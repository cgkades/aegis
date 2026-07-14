"""Voice factory and stubs."""

from __future__ import annotations

import pytest

from aegis.config import build_config
from aegis.voice.factory import create_voice_session, provider_status
from aegis.voice.gpt_live import GptLiveVoiceSession
from aegis.voice.mock import MockVoiceSession
from aegis.voice.text_fallback import TextFallbackSession


def test_provider_status() -> None:
    cfg = build_config({})
    st = provider_status(cfg)
    assert st["realtime_available"] is True
    assert st["gpt_live_available"] is False


def test_create_mock() -> None:
    cfg = build_config({})
    s = create_voice_session(cfg, backend="mock")
    assert isinstance(s, MockVoiceSession)


def test_create_gpt_live() -> None:
    cfg = build_config({})
    s = create_voice_session(cfg, backend="gpt_live")
    assert isinstance(s, GptLiveVoiceSession)


def test_create_text_fallback() -> None:
    cfg = build_config({})
    s = create_voice_session(cfg, backend="text_fallback")
    assert isinstance(s, TextFallbackSession)


@pytest.mark.asyncio
async def test_gpt_live_raises() -> None:
    s = GptLiveVoiceSession()
    with pytest.raises(NotImplementedError):
        await s.connect(build_config({}).session)


@pytest.mark.asyncio
async def test_text_fallback_raises() -> None:
    s = TextFallbackSession()
    with pytest.raises(NotImplementedError):
        await s.connect(build_config({}).session)


@pytest.mark.asyncio
async def test_create_realtime_without_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    cfg = build_config({})
    # Force empty key regardless of dotenv files on disk
    s = create_voice_session(cfg, backend="realtime")
    # Bypass any key loaded via secrets path
    if hasattr(s, "_api_key"):
        s._api_key = None  # noqa: SLF001
    with pytest.raises(RuntimeError):
        await s.connect(cfg.session)
