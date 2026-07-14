"""Azure OpenAI / Azure AI Foundry client helpers."""

from __future__ import annotations

from urllib.parse import quote

from aegis.config.schema import AzureOpenAIConfig
from aegis.llm.client import OpenAICompatibleClient
from aegis.util.secrets import resolve_api_key


def build_azure_chat_url(cfg: AzureOpenAIConfig, deployment: str) -> tuple[str, dict[str, str]]:
    """Return (base_url for .../chat/completions, extra_query params).

    OpenAICompatibleClient appends `/chat/completions` to base_url.
    """
    endpoint = (cfg.endpoint or "").rstrip("/")
    if not endpoint:
        raise RuntimeError(
            "Azure OpenAI endpoint not set. Configure llm.azure_openai.endpoint "
            "(e.g. https://my-resource.openai.azure.com)."
        )
    dep = quote(deployment, safe="")
    style = cfg.api_style
    if style == "deployments":
        base = f"{endpoint}/openai/deployments/{dep}"
        return base, {"api-version": cfg.api_version}
    if style == "foundry":
        # Azure AI Foundry model inference
        base = f"{endpoint}/models"
        return base, {"api-version": cfg.api_version}
    # openai_v1 — model goes in JSON body
    base = f"{endpoint}/openai/v1"
    return base, {}


def create_azure_client(
    cfg: AzureOpenAIConfig,
    *,
    model: str,
    temperature: float,
    max_tokens: int,
) -> OpenAICompatibleClient:
    key = resolve_api_key(env_var=cfg.api_key_env)
    if not key:
        raise RuntimeError(
            f"{cfg.api_key_env} not set. Add it in Settings → Secrets or .env"
        )
    deployment = model or cfg.deployment
    base_url, query = build_azure_chat_url(cfg, deployment)
    auth_mode = "api_key" if cfg.auth_mode == "api_key" else "bearer"
    # For deployments style the model is in the path; still send deployment as model
    # for openai_v1 / foundry body field.
    return OpenAICompatibleClient(
        provider="azure_openai",
        model=deployment,
        base_url=base_url,
        api_key=key,
        temperature=temperature,
        max_tokens=max_tokens,
        auth_mode=auth_mode,
        extra_query=query,
        # deployments path already selects the model — some APIs still want body.model
        include_model_in_body=cfg.api_style != "deployments",
    )
