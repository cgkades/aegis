"""Persist Aegis config to TOML on disk."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import tomli_w

from aegis.config.schema import AegisConfig

_HEADER = (
    "# Aegis configuration — managed by `aegis settings` / settings page\n"
    "# See DESIGN.md and configs/aegis.example.toml for full schema.\n\n"
)


def _drop_none(value: Any) -> Any:
    """Recursively strip None values — TOML has no null; a missing key means default.

    Applied to the whole config tree so optional fields (e.g. ShellRule.allowed_flags,
    McpLocalServer.cwd) round-trip as "absent" rather than crashing the serializer.
    """
    if isinstance(value, dict):
        return {k: _drop_none(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [_drop_none(v) for v in value]
    return value


def config_to_toml(cfg: AegisConfig) -> str:
    """Serialize the full config to TOML.

    Uses a real TOML writer so nested tables, arrays-of-tables (``tools.shell.rules``,
    ``mcp.local.servers``, ``mcp.remote.servers``) and dict fields (``*.env``) survive a
    round-trip. The previous hand-rolled writer silently dropped them, so saving from
    the settings page wiped MCP servers and custom shell rules.
    """
    data = _drop_none(cfg.model_dump(mode="json"))
    return _HEADER + tomli_w.dumps(data)


def save_config(cfg: AegisConfig, path: Path) -> Path:
    """Write config TOML to path (creates parent dirs)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = config_to_toml(cfg)
    path.write_text(text, encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return path


def apply_llm_settings(
    cfg: AegisConfig,
    *,
    profile: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    voice: str | None = None,
    reasoning_effort: str | None = None,
    max_session_cost_usd: float | None = None,
    max_duration_s: int | None = None,
    idle_timeout_s: int | None = None,
    api_key_env: str | None = None,
    realtime_url: str | None = None,
    log_level: str | None = None,
    # multi-provider
    openai_chat_base_url: str | None = None,
    litellm_base_url: str | None = None,
    litellm_api_key_env: str | None = None,
    litellm_model: str | None = None,
    ollama_base_url: str | None = None,
    ollama_native_base_url: str | None = None,
    ollama_model: str | None = None,
    chatgpt_token_path: str | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
    # Azure OpenAI / Foundry
    azure_endpoint: str | None = None,
    azure_api_key_env: str | None = None,
    azure_api_version: str | None = None,
    azure_deployment: str | None = None,
    azure_api_style: str | None = None,
    azure_auth_mode: str | None = None,
    # AWS Bedrock
    bedrock_region: str | None = None,
    bedrock_model_id: str | None = None,
    bedrock_profile: str | None = None,
    bedrock_endpoint_url: str | None = None,
) -> AegisConfig:
    """Return a copy of cfg with LLM-related fields updated."""
    data = cfg.model_dump(mode="json")
    if profile is not None:
        data["profile"]["name"] = profile
    if provider is not None:
        data["session"]["provider"] = provider
        data["llm"]["chat_provider"] = provider
    if model is not None:
        data["session"]["model"] = model
    if voice is not None:
        data["session"]["voice"] = voice
    if reasoning_effort is not None:
        data["session"]["reasoning_effort"] = reasoning_effort
    if max_session_cost_usd is not None:
        data["session"]["max_session_cost_usd"] = float(max_session_cost_usd)
    if max_duration_s is not None:
        data["session"]["max_duration_s"] = int(max_duration_s)
    if idle_timeout_s is not None:
        data["session"]["idle_timeout_s"] = int(idle_timeout_s)
    if api_key_env is not None:
        data["openai"]["api_key_env"] = api_key_env
        data["llm"]["openai"]["api_key_env"] = api_key_env
    if realtime_url is not None:
        data["openai"]["realtime_url"] = realtime_url
        data["llm"]["openai"]["realtime_url"] = realtime_url
    if openai_chat_base_url is not None:
        data["openai"]["chat_base_url"] = openai_chat_base_url
        data["llm"]["openai"]["chat_base_url"] = openai_chat_base_url
    if log_level is not None:
        data["app"]["log_level"] = log_level
    if litellm_base_url is not None:
        data["llm"]["litellm"]["base_url"] = litellm_base_url
    if litellm_api_key_env is not None:
        data["llm"]["litellm"]["api_key_env"] = litellm_api_key_env
    if litellm_model is not None:
        data["llm"]["litellm"]["model"] = litellm_model
    if ollama_base_url is not None:
        data["llm"]["ollama"]["base_url"] = ollama_base_url
    if ollama_native_base_url is not None:
        data["llm"]["ollama"]["native_base_url"] = ollama_native_base_url
    if ollama_model is not None:
        data["llm"]["ollama"]["model"] = ollama_model
    if chatgpt_token_path is not None:
        data["llm"]["chatgpt_oauth"]["token_path"] = chatgpt_token_path
    if temperature is not None:
        data["llm"]["temperature"] = float(temperature)
    if max_tokens is not None:
        data["llm"]["max_tokens"] = int(max_tokens)
    if azure_endpoint is not None:
        data["llm"]["azure_openai"]["endpoint"] = azure_endpoint
    if azure_api_key_env is not None:
        data["llm"]["azure_openai"]["api_key_env"] = azure_api_key_env
    if azure_api_version is not None:
        data["llm"]["azure_openai"]["api_version"] = azure_api_version
    if azure_deployment is not None:
        data["llm"]["azure_openai"]["deployment"] = azure_deployment
        # Keep session.model aligned when saving Azure deployment
        if model is None:
            data["session"]["model"] = azure_deployment
    if azure_api_style is not None:
        data["llm"]["azure_openai"]["api_style"] = azure_api_style
    if azure_auth_mode is not None:
        data["llm"]["azure_openai"]["auth_mode"] = azure_auth_mode
    if bedrock_region is not None:
        data["llm"]["bedrock"]["region"] = bedrock_region
    if bedrock_model_id is not None:
        data["llm"]["bedrock"]["model_id"] = bedrock_model_id
        if model is None:
            data["session"]["model"] = bedrock_model_id
    if bedrock_profile is not None:
        data["llm"]["bedrock"]["profile"] = bedrock_profile
    if bedrock_endpoint_url is not None:
        data["llm"]["bedrock"]["endpoint_url"] = bedrock_endpoint_url
    return AegisConfig.model_validate(data)
