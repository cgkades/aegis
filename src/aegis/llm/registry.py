"""Provider catalog + health probes for the settings UI."""

from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from aegis.config.schema import AegisConfig, SessionProvider
from aegis.llm.chatgpt_oauth import status_dict as oauth_status
from aegis.util.secrets import resolve_api_key


def list_provider_catalog() -> list[dict[str, Any]]:
    """Static catalog shown in settings."""
    return [
        {
            "id": "realtime",
            "name": "OpenAI Realtime (API key)",
            "kind": "voice_duplex",
            "needs": ["OPENAI_API_KEY"],
            "description": "Full-duplex speech-to-speech via Realtime API. Best voice quality.",
            "models": [
                "gpt-realtime-2.1-mini",
                "gpt-realtime-2.1",
                "gpt-realtime-2",
            ],
        },
        {
            "id": "openai_api",
            "name": "OpenAI Chat API (API key)",
            "kind": "chat",
            "needs": ["OPENAI_API_KEY"],
            "description": "Chat Completions with an API key (cascaded text path).",
            "models": ["gpt-4o-mini", "gpt-4o", "gpt-4.1-mini", "gpt-4.1", "o4-mini"],
        },
        {
            "id": "chatgpt_oauth",
            "name": "ChatGPT account (OAuth)",
            "kind": "chat_oauth",
            "needs": ["oauth"],
            "description": (
                "Sign in with your ChatGPT subscription (device code / browser). "
                "Uses OAuth bearer tokens — great for chat/tools; Realtime voice still "
                "prefers an API key when available."
            ),
            "models": ["gpt-4o", "gpt-4o-mini", "o3-mini", "o4-mini"],
        },
        {
            "id": "litellm",
            "name": "LiteLLM proxy",
            "kind": "openai_compatible",
            "needs": ["LITELLM_API_KEY?"],
            "description": "Any model behind a LiteLLM OpenAI-compatible proxy.",
            "models": [],  # dynamic
        },
        {
            "id": "ollama",
            "name": "Ollama (local)",
            "kind": "openai_compatible",
            "needs": [],
            "description": "Local models via Ollama (http://127.0.0.1:11434).",
            "models": [],  # dynamic from /api/tags
        },
        {
            "id": "azure_openai",
            "name": "Azure OpenAI / Foundry",
            "kind": "chat",
            "needs": ["AZURE_OPENAI_API_KEY", "endpoint"],
            "description": (
                "Azure OpenAI deployments or Azure AI Foundry model endpoints "
                "(api-key or Entra bearer)."
            ),
            "models": ["gpt-4o-mini", "gpt-4o", "gpt-4.1-mini", "gpt-4.1"],
        },
        {
            "id": "bedrock",
            "name": "AWS Bedrock",
            "kind": "chat",
            "needs": ["AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"],
            "description": (
                "Amazon Bedrock Runtime Converse API (SigV4). "
                "Uses env credentials or ~/.aws profile — no boto3 required."
            ),
            "models": [
                "amazon.nova-lite-v1:0",
                "amazon.nova-pro-v1:0",
                "anthropic.claude-3-5-sonnet-20241022-v2:0",
                "anthropic.claude-3-haiku-20240307-v1:0",
            ],
        },
        {
            "id": "mock",
            "name": "Mock (offline)",
            "kind": "dev",
            "needs": [],
            "description": "Deterministic offline dogfood — no network.",
            "models": ["mock"],
        },
    ]


def list_ollama_models(native_base_url: str) -> list[str]:
    url = f"{native_base_url.rstrip('/')}/api/tags"
    req = Request(url, method="GET", headers={"Accept": "application/json"})
    try:
        with urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, OSError):
        return []
    models = []
    for m in data.get("models") or []:
        name = m.get("name") or m.get("model")
        if name:
            models.append(str(name))
    return models


def list_openai_compatible_models(base_url: str, api_key: str) -> list[str]:
    url = f"{base_url.rstrip('/')}/models"
    req = Request(
        url,
        method="GET",
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
    )
    try:
        with urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, OSError):
        return []
    out = []
    for m in data.get("data") or []:
        mid = m.get("id")
        if mid:
            out.append(str(mid))
    return out[:100]


def probe_provider(cfg: AegisConfig, provider: str) -> dict[str, Any]:
    """Health-check a provider for the settings UI."""
    provider = provider.strip().lower()
    result: dict[str, Any] = {
        "provider": provider,
        "ok": False,
        "detail": "",
        "models": [],
    }

    if provider == "mock":
        result.update(ok=True, detail="offline mock always available", models=["mock"])
        return result

    if provider == "ollama":
        models = list_ollama_models(cfg.llm.ollama.native_base_url)
        result["models"] = models
        if models:
            result.update(ok=True, detail=f"{len(models)} local model(s)")
        else:
            result["detail"] = (
                f"Cannot reach Ollama at {cfg.llm.ollama.native_base_url}. "
                "Is `ollama serve` running?"
            )
        return result

    if provider == "litellm":
        key = (
            resolve_api_key(env_var=cfg.llm.litellm.api_key_env)
            or cfg.llm.litellm.default_api_key
        )
        models = list_openai_compatible_models(cfg.llm.litellm.base_url, key)
        result["models"] = models
        if models:
            result.update(ok=True, detail=f"LiteLLM reachable ({len(models)} models)")
        else:
            # Try a tiny chat probe instead of /models (some proxies disable it)
            try:
                from aegis.llm.client import create_llm_client

                create_llm_client(cfg, provider="litellm")
                result["detail"] = (
                    f"LiteLLM at {cfg.llm.litellm.base_url} — /models empty or blocked; "
                    "save a model id manually (e.g. gpt-4o-mini)."
                )
                result["ok"] = True
            except Exception as exc:
                result["detail"] = str(exc)
        return result

    if provider == "chatgpt_oauth":
        st = oauth_status(cfg.llm.chatgpt_oauth.token_path)
        result["oauth"] = st
        result["ok"] = bool(st.get("signed_in"))
        result["detail"] = (
            f"Signed in as {st.get('email')}"
            if st.get("signed_in")
            else "Not signed in — use Sign in with ChatGPT"
        )
        result["models"] = ["gpt-4o", "gpt-4o-mini", "o4-mini"]
        return result

    if provider in {"openai_api", "realtime", SessionProvider.OPENAI_API.value}:
        key = resolve_api_key(env_var=cfg.openai.api_key_env)
        if not key:
            result["detail"] = f"{cfg.openai.api_key_env} not set"
            return result
        models = list_openai_compatible_models(
            cfg.openai.chat_base_url or "https://api.openai.com/v1",
            key,
        )
        result["models"] = models[:50]
        result["ok"] = True
        result["detail"] = (
            f"API key present ({len(models)} models listed)"
            if models
            else "API key present (model list unavailable)"
        )
        return result

    if provider in {"azure_openai", "azure"}:
        az = cfg.llm.azure_openai
        key = resolve_api_key(env_var=az.api_key_env)
        if not az.endpoint:
            result["detail"] = "llm.azure_openai.endpoint not set"
            return result
        if not key:
            result["detail"] = f"{az.api_key_env} not set"
            return result
        result["ok"] = True
        result["detail"] = (
            f"Azure endpoint configured ({az.api_style}, deployment={az.deployment})"
        )
        result["models"] = [az.deployment] if az.deployment else []
        result["models"].extend(
            [
                "gpt-4o-mini",
                "gpt-4o",
                "gpt-4.1-mini",
                "gpt-4.1",
                "o4-mini",
            ]
        )
        # de-dupe preserve order
        seen: set[str] = set()
        uniq: list[str] = []
        for m in result["models"]:
            if m and m not in seen:
                seen.add(m)
                uniq.append(m)
        result["models"] = uniq
        return result

    if provider in {"bedrock", "aws_bedrock"}:
        from aegis.llm.bedrock import resolve_aws_credentials

        br = cfg.llm.bedrock
        try:
            _creds, region = resolve_aws_credentials(
                access_key_env=br.access_key_env,
                secret_key_env=br.secret_key_env,
                session_token_env=br.session_token_env,
                region_env=br.region_env,
                profile=br.profile,
                default_region=br.region,
            )
        except RuntimeError as exc:
            result["detail"] = str(exc)
            result["models"] = [
                br.model_id,
                "amazon.nova-lite-v1:0",
                "amazon.nova-pro-v1:0",
                "anthropic.claude-3-5-sonnet-20241022-v2:0",
            ]
            return result
        result["ok"] = True
        result["detail"] = f"AWS credentials present (region={region})"
        result["models"] = [
            br.model_id,
            "amazon.nova-lite-v1:0",
            "amazon.nova-pro-v1:0",
            "amazon.nova-micro-v1:0",
            "anthropic.claude-3-5-sonnet-20241022-v2:0",
            "anthropic.claude-3-haiku-20240307-v1:0",
            "meta.llama3-8b-instruct-v1:0",
        ]
        seen = set()
        uniq = []
        for m in result["models"]:
            if m and m not in seen:
                seen.add(m)
                uniq.append(m)
        result["models"] = uniq
        return result

    result["detail"] = f"unknown provider {provider}"
    return result
