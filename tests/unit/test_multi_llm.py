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
    assert {
        "realtime",
        "openai_api",
        "chatgpt_oauth",
        "litellm",
        "ollama",
        "azure_openai",
        "bedrock",
        "mock",
    } <= ids


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


def test_apply_azure_and_bedrock_settings() -> None:
    cfg = build_config({})
    updated = apply_llm_settings(
        cfg,
        provider="azure_openai",
        azure_endpoint="https://my.openai.azure.com",
        azure_deployment="gpt-4o",
        azure_api_style="deployments",
        bedrock_region="us-west-2",
        bedrock_model_id="amazon.nova-lite-v1:0",
    )
    assert updated.session.provider.value == "azure_openai"
    assert updated.llm.azure_openai.endpoint == "https://my.openai.azure.com"
    assert updated.llm.azure_openai.deployment == "gpt-4o"
    assert updated.llm.bedrock.region == "us-west-2"
    assert updated.llm.bedrock.model_id == "amazon.nova-lite-v1:0"
    toml = config_to_toml(updated)
    assert "azure_openai" in toml
    assert "bedrock" in toml


def test_azure_client_url_and_headers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "az-key-123")
    cfg = build_config(
        {
            "session": {"provider": "azure_openai", "model": "my-deploy"},
            "llm": {
                "azure_openai": {
                    "endpoint": "https://example.openai.azure.com",
                    "deployment": "my-deploy",
                    "api_style": "deployments",
                    "api_version": "2024-10-21",
                    "auth_mode": "api_key",
                }
            },
        }
    )
    client = create_llm_client(cfg, provider="azure_openai")
    assert client.provider == "azure_openai"
    assert client.model == "my-deploy"
    assert "deployments/my-deploy" in client.base_url
    assert client.auth_mode == "api_key"
    assert client.extra_query.get("api-version") == "2024-10-21"
    assert client.include_model_in_body is False

    fake_body = json.dumps(
        {
            "choices": [{"message": {"role": "assistant", "content": "azure-hi"}}],
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

    captured: dict = {}

    def fake_urlopen(req, timeout=120):
        captured["url"] = req.full_url
        captured["headers"] = {k.lower(): v for k, v in req.headers.items()}
        captured["body"] = req.data
        return Resp()

    with patch("aegis.llm.client.urlopen", side_effect=fake_urlopen):
        out = client.chat_sync([ChatMessage(role="user", content="hello")])
    assert out.text == "azure-hi"
    assert "api-version=2024-10-21" in captured["url"]
    assert captured["headers"].get("api-key") == "az-key-123"
    body = json.loads(captured["body"].decode())
    assert "model" not in body  # deployments style


def test_bedrock_client_mock_http(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIATEST")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "secretsecretsecretsecret")
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    cfg = build_config(
        {
            "session": {"provider": "bedrock", "model": "amazon.nova-lite-v1:0"},
            "llm": {"bedrock": {"model_id": "amazon.nova-lite-v1:0", "region": "us-east-1"}},
        }
    )
    client = create_llm_client(cfg, provider="bedrock")
    assert client.provider == "bedrock"
    assert client.model == "amazon.nova-lite-v1:0"

    fake_body = json.dumps(
        {
            "output": {
                "message": {
                    "role": "assistant",
                    "content": [{"text": "bedrock-pong"}],
                }
            },
            "usage": {"inputTokens": 1, "outputTokens": 2},
        }
    ).encode()

    class Resp:
        def read(self):
            return fake_body

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    captured: dict = {}

    def fake_urlopen(req, timeout=120):
        captured["url"] = req.full_url
        captured["headers"] = dict(req.headers)
        captured["body"] = req.data
        return Resp()

    with patch("aegis.llm.bedrock.urlopen", side_effect=fake_urlopen):
        out = client.chat_sync([ChatMessage(role="user", content="hi")])
    assert out.text == "bedrock-pong"
    assert "bedrock-runtime.us-east-1.amazonaws.com" in captured["url"]
    assert "amazon.nova-lite-v1%3A0" in captured["url"]
    assert "Authorization" in captured["headers"] or any(
        k.lower() == "authorization" for k in captured["headers"]
    )
    payload = json.loads(captured["body"].decode())
    assert payload["messages"][0]["role"] == "user"


def test_bedrock_requires_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    for k in (
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
        "AWS_PROFILE",
    ):
        monkeypatch.delenv(k, raising=False)
    cfg = build_config(
        {
            "session": {"provider": "bedrock"},
            "llm": {"bedrock": {"profile": "", "model_id": "x"}},
        }
    )
    with patch("aegis.llm.bedrock._read_aws_profile", return_value=None):
        with pytest.raises(RuntimeError, match="AWS credentials"):
            create_llm_client(cfg, provider="bedrock")


def test_probe_azure_and_bedrock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "k")
    cfg = build_config(
        {
            "llm": {
                "azure_openai": {
                    "endpoint": "https://x.openai.azure.com",
                    "deployment": "d1",
                }
            }
        }
    )
    az = probe_provider(cfg, "azure_openai")
    assert az["ok"] is True
    assert "d1" in az["models"]

    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIATEST")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "secret")
    br = probe_provider(cfg, "bedrock")
    assert br["ok"] is True


def test_create_voice_session_azure_bedrock() -> None:
    cfg = build_config({})
    assert create_voice_session(cfg, backend="azure_openai").__class__.__name__ == "ChatLLMSession"
    assert create_voice_session(cfg, backend="bedrock").__class__.__name__ == "ChatLLMSession"


def test_sigv4_deterministic_headers() -> None:
    from datetime import UTC, datetime

    from aegis.llm.aws_sigv4 import sign_headers

    headers = sign_headers(
        method="POST",
        url="https://bedrock-runtime.us-east-1.amazonaws.com/model/amazon.nova-lite-v1%3A0/converse",
        body=b'{"messages":[]}',
        region="us-east-1",
        service="bedrock",
        access_key="AKIATEST",
        secret_key="wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
        now=datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC),
    )
    assert headers["Authorization"].startswith("AWS4-HMAC-SHA256 Credential=AKIATEST/")
    assert headers["x-amz-date"] == "20240101T120000Z"


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
