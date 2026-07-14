"""Exercise all GPT-Live and text_fallback methods."""

from __future__ import annotations

import pytest

from aegis.config.schema import SessionConfig
from aegis.voice.gpt_live import GptLiveVoiceSession
from aegis.voice.text_fallback import TextFallbackSession


@pytest.mark.asyncio
async def test_gpt_live_all():
    s = GptLiveVoiceSession()
    with pytest.raises(NotImplementedError):
        await s.connect(SessionConfig())
    with pytest.raises(NotImplementedError):
        await s.send_audio(b"x")
    with pytest.raises(NotImplementedError):
        await s.send_tool_result("c", "o")
    with pytest.raises(NotImplementedError):
        await s.interrupt_agent()
    await s.end()
    with pytest.raises(NotImplementedError):
        async for _ in s.events():
            pass


@pytest.mark.asyncio
async def test_text_fallback_all():
    s = TextFallbackSession()
    with pytest.raises(NotImplementedError):
        await s.connect(SessionConfig())
    with pytest.raises(NotImplementedError):
        await s.send_audio(b"x")
    with pytest.raises(NotImplementedError):
        await s.send_tool_result("c", "o")
    await s.interrupt_agent()
    await s.end()
    with pytest.raises(NotImplementedError):
        async for _ in s.events():
            pass
