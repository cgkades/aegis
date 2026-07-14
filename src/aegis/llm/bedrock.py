"""AWS Bedrock Runtime Converse client (SigV4, stdlib HTTP)."""

from __future__ import annotations

import configparser
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from aegis.llm.aws_sigv4 import quote_path_segment, sign_headers
from aegis.llm.client import ChatMessage, LLMResponse
from aegis.util.logging import get_logger

log = get_logger("llm.bedrock")


@dataclass(slots=True)
class AwsCredentials:
    access_key: str
    secret_key: str
    session_token: str | None = None
    region: str | None = None


def _read_aws_profile(profile: str) -> AwsCredentials | None:
    """Load keys from ~/.aws/credentials (+ region from ~/.aws/config)."""
    if not profile:
        return None
    cred_path = Path.home() / ".aws" / "credentials"
    if not cred_path.is_file():
        return None
    cp = configparser.ConfigParser()
    try:
        cp.read(cred_path)
    except configparser.Error:
        return None
    if profile not in cp:
        return None
    section = cp[profile]
    access = section.get("aws_access_key_id", "").strip()
    secret = section.get("aws_secret_access_key", "").strip()
    if not access or not secret:
        return None
    token = section.get("aws_session_token", "").strip() or None
    region = section.get("region", "").strip() or None

    config_path = Path.home() / ".aws" / "config"
    if not region and config_path.is_file():
        cfg = configparser.ConfigParser()
        try:
            cfg.read(config_path)
            # profiles in config are "profile name" except default
            key = "default" if profile == "default" else f"profile {profile}"
            if key in cfg:
                region = cfg[key].get("region", "").strip() or None
        except configparser.Error:
            pass

    return AwsCredentials(
        access_key=access,
        secret_key=secret,
        session_token=token,
        region=region,
    )


def resolve_aws_credentials(
    *,
    access_key_env: str = "AWS_ACCESS_KEY_ID",
    secret_key_env: str = "AWS_SECRET_ACCESS_KEY",
    session_token_env: str = "AWS_SESSION_TOKEN",
    region_env: str = "AWS_REGION",
    profile: str = "",
    default_region: str = "us-east-1",
) -> tuple[AwsCredentials, str]:
    """Resolve AWS credentials + region from env or shared profile."""
    access = os.environ.get(access_key_env, "").strip()
    secret = os.environ.get(secret_key_env, "").strip()
    token = os.environ.get(session_token_env, "").strip() or None
    region = (
        os.environ.get(region_env, "").strip()
        or os.environ.get("AWS_DEFAULT_REGION", "").strip()
        or default_region
    )

    if access and secret:
        return (
            AwsCredentials(
                access_key=access,
                secret_key=secret,
                session_token=token,
            ),
            region,
        )

    prof = profile or os.environ.get("AWS_PROFILE", "").strip()
    if prof:
        loaded = _read_aws_profile(prof)
        if loaded:
            return loaded, loaded.region or region

    # default profile as last resort
    loaded = _read_aws_profile("default")
    if loaded:
        return loaded, loaded.region or region

    raise RuntimeError(
        "AWS credentials not found. Set AWS_ACCESS_KEY_ID + AWS_SECRET_ACCESS_KEY "
        "(and optional AWS_SESSION_TOKEN), or configure llm.bedrock.profile / AWS_PROFILE."
    )


def _messages_to_converse(
    messages: list[ChatMessage],
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Split OpenAI-style messages into Converse system + messages."""
    system: list[dict[str, str]] = []
    out: list[dict[str, Any]] = []
    for m in messages:
        role = m.role
        text = m.content or ""
        if role == "system":
            if text:
                system.append({"text": text})
            continue
        if role not in {"user", "assistant"}:
            role = "user"
        # Converse requires alternating roles; merge consecutive same-role turns
        if out and out[-1]["role"] == role:
            prev = out[-1]["content"]
            if prev and isinstance(prev[-1], dict) and "text" in prev[-1]:
                prev[-1]["text"] = prev[-1]["text"] + "\n" + text
            else:
                prev.append({"text": text})
        else:
            out.append({"role": role, "content": [{"text": text}]})
    # Converse requires first message to be user
    if out and out[0]["role"] != "user":
        out.insert(0, {"role": "user", "content": [{"text": "(continue)"}]})
    return out, system


def _extract_text(response: dict[str, Any]) -> str:
    output = response.get("output") or {}
    message = output.get("message") or {}
    parts = message.get("content") or []
    texts: list[str] = []
    for p in parts:
        if isinstance(p, dict) and "text" in p:
            texts.append(str(p["text"]))
    return "".join(texts).strip()


class BedrockConverseClient:
    """Bedrock Runtime Converse API client."""

    def __init__(
        self,
        *,
        model_id: str,
        region: str,
        credentials: AwsCredentials,
        temperature: float = 0.4,
        max_tokens: int = 2048,
        endpoint_url: str = "",
    ) -> None:
        self.provider = "bedrock"
        self.model = model_id
        self.region = region
        self.credentials = credentials
        self.temperature = temperature
        self.max_tokens = max_tokens
        if endpoint_url:
            self.endpoint_url = endpoint_url.rstrip("/")
        else:
            self.endpoint_url = f"https://bedrock-runtime.{region}.amazonaws.com"

    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
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
        converse_messages, system = _messages_to_converse(messages)
        body_obj: dict[str, Any] = {
            "messages": converse_messages,
            "inferenceConfig": {
                "maxTokens": int(self.max_tokens if max_tokens is None else max_tokens),
                "temperature": float(self.temperature if temperature is None else temperature),
            },
        }
        if system:
            body_obj["system"] = system
        body = json.dumps(body_obj).encode("utf-8")

        path_model = quote_path_segment(self.model)
        url = f"{self.endpoint_url}/model/{path_model}/converse"
        headers = sign_headers(
            method="POST",
            url=url,
            body=body,
            region=self.region,
            service="bedrock",
            access_key=self.credentials.access_key,
            secret_key=self.credentials.secret_key,
            session_token=self.credentials.session_token,
        )
        req = Request(url, data=body, headers=headers, method="POST")
        try:
            with urlopen(req, timeout=120) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except HTTPError as exc:
            err_body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"bedrock HTTP {exc.code}: {err_body[:500]}") from exc
        except URLError as exc:
            raise RuntimeError(f"bedrock connection failed: {exc.reason}") from exc

        text = _extract_text(data)
        usage = data.get("usage") or {}
        return LLMResponse(
            text=text,
            model=self.model,
            raw=data,
            usage=usage if isinstance(usage, dict) else {},
        )
