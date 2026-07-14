"""Multi-provider LLM client, OAuth store, and registry tests."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from aegis.config import build_config
from aegis.config.save import apply_llm_settings, config_to_toml
from aegis.llm.chatgpt_oauth import (
    OAuthTokens,
    clear_tokens,
    load_tokens,
    save_manual_token,
    save_tokens,
    status_dict,
)
from aegis.llm.client import ChatMessage, OpenAICompatibleClient, create_llm_client
from aegis.llm.registry import list_ollama_models, list_provider_catalog, probe_provider
from aegis.voice.factory import create_voice_session, provider_status


def test_catalog_has_core_providers() -> None:
    ids = {p["id"] for p in list_provider_catalog()}
    assert {"realtime", "openai_api", "chatgpt_oauth", "litellm", "ollama", "mock"} <= ids


def test_apply_multi_provider_settings() -> None:
    cfg = build_config({})
    updated = apply_llm_settings(
        cfg,
        provider="ollama",
        model="llama3.2",
        ollama_base_url="http://127.0.0.1:11434/v1",
        ollama_model="llama3.2",
        litellm_base_url="http://127.0.0.1:4000/v1",
        temperature=0.2,
    )
    assert updated.session.provider.value == "ollama"
    assert updated.session.model == "llama3.2"
    assert updated.llm.ollama.model == "llama3.2"
    assert "llm" in config_to_toml(updated)


def test_oauth_token_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "tok.json"
    save_tokens(
        path,
        OAuthTokens(access_token="tok123", email="a@b.c", expires_at=9e12),
    )
    loaded = load_tokens(path)
    assert loaded is not None
    assert loaded.access_token == "tok123"
    assert loaded.signed_in is True
    st = status_dict(path)
    assert st["signed_in"] is True
    clear_tokens(path)
    assert load_tokens(path) is None


def test_save_manual_token(tmp_path: Path) -> None:
    path = tmp_path / "m.json"
    result = save_manual_token(path, "access-xyz", email="u@example.com")
    assert result["ok"] is True
    assert load_tokens(path).access_token == "access-xyz"  # type: ignore[union-attr]


def test_openai_compatible_client_mock_http() -> None:
    client = OpenAICompatibleClient(
        provider="ollama",
        model="llama3.2",
        base_url="http://127.0.0.1:9/v1",
        api_key="ollama",
    )
    fake_body = json.dumps(
        {
            "model": "llama3.2",
            "choices": [{"message": {"role": "assistant", "content": "hi"}}],
            "usage": {},
        }
    ).encode()

    class Resp:
        def read(self):
            return fake_body

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    with patch("aegis.llm.client.urlopen", return_value=Resp()):
        out = client.chat_sync([ChatMessage(role="user", content="hello")])
    assert out.text == "hi"


def test_create_llm_client_ollama(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = build_config(
        {
            "session": {"provider": "ollama", "model": "llama3.2"},
            "llm": {"ollama": {"model": "llama3.2"}},
        }
    )
    client = create_llm_client(cfg, provider="ollama")
    assert client.provider == "ollama"
    assert client.model == "llama3.2"


def test_create_llm_client_ollama_ignores_realtime_session_model() -> None:
    """CLI --backend ollama with default realtime session.model must use llm.ollama.model."""
    cfg = build_config(
        {
            "session": {"provider": "realtime", "model": "gpt-realtime-2.1-mini"},
            "llm": {"ollama": {"model": "llama3.2:1b"}},
        }
    )
    client = create_llm_client(cfg, provider="ollama")
    assert client.model == "llama3.2:1b"


def test_create_llm_client_oauth_requires_login(tmp_path: Path) -> None:
    cfg = build_config(
        {
            "session": {"provider": "chatgpt_oauth", "model": "gpt-4o"},
            "llm": {
                "chatgpt_oauth": {
                    "token_path": str(tmp_path / "missing.json"),
                }
            },
        }
    )
    with pytest.raises(RuntimeError, match="OAuth not signed in"):
        create_llm_client(cfg, provider="chatgpt_oauth")


def test_create_voice_session_chat_providers() -> None:
    cfg = build_config({"session": {"provider": "ollama", "model": "llama3.2"}})
    sess = create_voice_session(cfg, backend="ollama")
    assert sess.__class__.__name__ == "ChatLLMSession"


def test_provider_status_shape() -> None:
    cfg = build_config({})
    st = provider_status(cfg)
    assert "providers" in st
    assert "ollama" in st


def test_probe_mock() -> None:
    cfg = build_config({})
    r = probe_provider(cfg, "mock")
    assert r["ok"] is True


def test_list_ollama_models_down() -> None:
    # Unreachable port
    assert list_ollama_models("http://127.0.0.1:1") == []


@pytest.mark.asyncio
async def test_chat_session_flow(monkeypatch: pytest.MonkeyPatch) -> None:
    from aegis.llm.chat_session import ChatLLMSession
    from aegis.llm.client import LLMResponse
    from aegis.voice.protocol import VoiceEventType

    cfg = build_config({"session": {"provider": "ollama", "model": "x"}})

    class FakeClient:
        provider = "ollama"
        model = "x"

        async def chat(self, messages, **kwargs):
            return LLMResponse(text="pong", model="x")

    sess = ChatLLMSession(cfg, provider="ollama")
    with patch("aegis.llm.chat_session.create_llm_client", return_value=FakeClient()):
        await sess.connect(cfg.session)
        await sess.inject_user_text("ping")
        await sess.end()
        events = []
        async for ev in sess.events():
            events.append(ev)
        types = [e.type for e in events]
        assert VoiceEventType.READY in types
        assert VoiceEventType.USER_TRANSCRIPT in types
        assert VoiceEventType.AGENT_TRANSCRIPT in types
        assert VoiceEventType.ENDED in types
