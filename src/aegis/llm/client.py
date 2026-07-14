"""LLM client protocol and OpenAI-compatible HTTP implementation."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from aegis.config.schema import AegisConfig, SessionProvider
from aegis.util.logging import get_logger
from aegis.util.secrets import resolve_api_key

log = get_logger("llm.client")


@dataclass(slots=True)
class ChatMessage:
    role: str
    content: str


@dataclass(slots=True)
class LLMResponse:
    text: str
    model: str = ""
    raw: dict[str, Any] = field(default_factory=dict)
    usage: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class LLMClient(Protocol):
    provider: str
    model: str

    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse: ...

    def chat_sync(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse: ...


class OpenAICompatibleClient:
    """Chat Completions client for OpenAI / LiteLLM / Ollama / OAuth / Azure."""

    def __init__(
        self,
        *,
        provider: str,
        model: str,
        base_url: str,
        api_key: str,
        temperature: float = 0.4,
        max_tokens: int = 2048,
        default_headers: dict[str, str] | None = None,
        auth_mode: Literal["bearer", "api_key"] = "bearer",
        extra_query: dict[str, str] | None = None,
        include_model_in_body: bool = True,
    ) -> None:
        self.provider = provider
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.default_headers = default_headers or {}
        self.auth_mode = auth_mode
        self.extra_query = dict(extra_query or {})
        self.include_model_in_body = include_model_in_body

    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        # stdlib sync HTTP is fine for settings probes and short chat turns
        import asyncio

        return await asyncio.to_thread(
            self.chat_sync,
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )

    def chat_sync(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        url = f"{self.base_url}/chat/completions"
        if self.extra_query:
            url = f"{url}?{urlencode(self.extra_query)}"
        payload: dict[str, Any] = {
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "temperature": self.temperature if temperature is None else temperature,
            "max_tokens": self.max_tokens if max_tokens is None else max_tokens,
        }
        if self.include_model_in_body:
            payload["model"] = self.model
        headers = {
            "Content-Type": "application/json",
            **self.default_headers,
        }
        if self.auth_mode == "api_key":
            headers["api-key"] = self.api_key
        else:
            headers["Authorization"] = f"Bearer {self.api_key}"
        req = Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urlopen(req, timeout=120) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"{self.provider} HTTP {exc.code}: {body[:500]}") from exc
        except URLError as exc:
            raise RuntimeError(f"{self.provider} connection failed: {exc.reason}") from exc

        try:
            text = data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError(f"{self.provider} unexpected response: {data!r}") from exc
        return LLMResponse(
            text=text,
            model=str(data.get("model") or self.model),
            raw=data,
            usage=data.get("usage") or {},
        )


def _chat_model(session_model: str | None, provider_default: str) -> str:
    """Pick chat model: ignore Realtime model names when falling back to provider default."""
    if not session_model:
        return provider_default
    # Default session.model is often a Realtime id; do not send that to Ollama/LiteLLM
    if "realtime" in session_model.lower():
        return provider_default
    return session_model


def create_llm_client(cfg: AegisConfig, *, provider: str | None = None) -> LLMClient:
    """Build an LLM client from config for the given or session provider."""
    prov = provider or (
        cfg.session.provider.value
        if hasattr(cfg.session.provider, "value")
        else str(cfg.session.provider)
    )
    llm = cfg.llm
    temp = llm.temperature
    max_tok = llm.max_tokens

    if prov in {SessionProvider.OLLAMA.value, "ollama"}:
        key = (
            resolve_api_key(env_var=llm.ollama.api_key_env)
            or llm.ollama.default_api_key
        )
        model = _chat_model(cfg.session.model, llm.ollama.model)
        return OpenAICompatibleClient(
            provider="ollama",
            model=model,
            base_url=llm.ollama.base_url,
            api_key=key,
            temperature=temp,
            max_tokens=max_tok,
        )

    if prov in {SessionProvider.LITELLM.value, "litellm"}:
        key = (
            resolve_api_key(env_var=llm.litellm.api_key_env)
            or llm.litellm.default_api_key
        )
        model = _chat_model(cfg.session.model, llm.litellm.model)
        return OpenAICompatibleClient(
            provider="litellm",
            model=model,
            base_url=llm.litellm.base_url,
            api_key=key,
            temperature=temp,
            max_tokens=max_tok,
        )

    if prov in {SessionProvider.CHATGPT_OAUTH.value, "chatgpt_oauth"}:
        from aegis.llm.chatgpt_oauth import load_tokens

        tokens = load_tokens(llm.chatgpt_oauth.token_path)
        if not tokens or not tokens.access_token:
            raise RuntimeError(
                "ChatGPT OAuth not signed in. Open Settings → ChatGPT OAuth → Sign in, "
                "or run: aegis auth login"
            )
        model = _chat_model(cfg.session.model, "gpt-4o")
        return OpenAICompatibleClient(
            provider="chatgpt_oauth",
            model=model,
            base_url=llm.chatgpt_oauth.api_base_url,
            api_key=tokens.access_token,
            temperature=temp,
            max_tokens=max_tok,
        )

    if prov in {SessionProvider.AZURE_OPENAI.value, "azure_openai", "azure"}:
        from aegis.llm.azure import create_azure_client

        model = _chat_model(cfg.session.model, llm.azure_openai.deployment)
        return create_azure_client(
            llm.azure_openai,
            model=model,
            temperature=temp,
            max_tokens=max_tok,
        )

    if prov in {SessionProvider.BEDROCK.value, "bedrock", "aws_bedrock"}:
        from aegis.llm.bedrock import BedrockConverseClient, resolve_aws_credentials

        model = _chat_model(cfg.session.model, llm.bedrock.model_id)
        creds, region = resolve_aws_credentials(
            access_key_env=llm.bedrock.access_key_env,
            secret_key_env=llm.bedrock.secret_key_env,
            session_token_env=llm.bedrock.session_token_env,
            region_env=llm.bedrock.region_env,
            profile=llm.bedrock.profile,
            default_region=llm.bedrock.region,
        )
        return BedrockConverseClient(
            model_id=model,
            region=region,
            credentials=creds,
            temperature=temp,
            max_tokens=max_tok,
            endpoint_url=llm.bedrock.endpoint_url,
        )

    # openai_api, realtime (chat helper), text_fallback, default
    key = resolve_api_key(env_var=cfg.openai.api_key_env)
    if not key:
        raise RuntimeError(
            f"{cfg.openai.api_key_env} not set. Add it in Settings or .env"
        )
    model = cfg.session.model or "gpt-4o-mini"
    # If realtime model name, map to a chat model for text path
    if "realtime" in model:
        model = "gpt-4o-mini"
    base = cfg.openai.chat_base_url or "https://api.openai.com/v1"
    return OpenAICompatibleClient(
        provider="openai_api",
        model=model,
        base_url=base,
        api_key=key,
        temperature=temp,
        max_tokens=max_tok,
    )
