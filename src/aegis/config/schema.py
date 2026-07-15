"""Pydantic configuration schema for Aegis."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class ProfileName(StrEnum):
    MVP = "mvp"
    STANDARD = "standard"
    ONCALL = "oncall"


class WakeEngine(StrEnum):
    OPENWAKEWORD = "openwakeword"
    PORCUPINE = "porcupine"


class SessionProvider(StrEnum):
    """Voice/LLM backend.

    - realtime: OpenAI Realtime API (API key) — full duplex voice
    - openai_api: OpenAI Chat Completions / Responses via API key (cascaded voice later)
    - chatgpt_oauth: ChatGPT account OAuth tokens (subscription path; chat + tools)
    - litellm: OpenAI-compatible LiteLLM proxy
    - ollama: local Ollama OpenAI-compatible API
    - azure_openai: Azure OpenAI / Azure AI Foundry (api-key or Entra bearer)
    - bedrock: AWS Bedrock Runtime Converse API (SigV4)
    - mock / gpt_live / text_fallback: dev / stubs
    """

    REALTIME = "realtime"
    OPENAI_API = "openai_api"
    CHATGPT_OAUTH = "chatgpt_oauth"
    LITELLM = "litellm"
    OLLAMA = "ollama"
    AZURE_OPENAI = "azure_openai"
    BEDROCK = "bedrock"
    GPT_LIVE = "gpt_live"
    TEXT_FALLBACK = "text_fallback"
    HYBRID_TEXT_TOOLS = "hybrid_text_tools"
    MOCK = "mock"


class HotkeyBackend(StrEnum):
    AUTO = "auto"
    X11_PYNPUT = "x11_pynput"
    WAYLAND_EXTERNAL = "wayland_external"
    EVDEV = "evdev"
    NONE = "none"


class PushToTalkMode(StrEnum):
    TOGGLE = "toggle"
    HOLD = "hold"


class ShellMode(StrEnum):
    ARGV_POLICY = "argv_policy"


class SecretsDecision(StrEnum):
    PROMPT = "prompt"
    DENY = "deny"


class ApprovalDefault(StrEnum):
    AUTO_READONLY = "auto_readonly"
    PROMPT_ALL = "prompt_all"
    DENY_ALL = "deny_all"


class SessionGrantScope(StrEnum):
    """What a CLI "session" grant applies to.

    Only ``same_tool`` is implemented today, and it means *exact argument
    fingerprint* for *read-risk* tools — not a broad tool-name or risk-class
    grant. ``once`` means no session grant is stored (single allow only).
    """

    ONCE = "once"
    SAME_TOOL = "same_tool"


def _default_workspace_dir() -> str:
    """Return the XDG-aware default workspace without import-time evaluation."""
    from aegis.config.paths import default_paths

    return str(default_paths().workspace_dir)


class McpApproval(StrEnum):
    ALWAYS = "always"
    NEVER = "never"
    # OpenAI also supports finer-grained modes; keep simple for v1 config.


def _expand_user_path(value: str | Path) -> str:
    return str(Path(str(value)).expanduser())


def _validate_env_reference(value: str, field_name: str) -> None:
    """Require secret-bearing MCP settings to resolve from env/secrets.env."""
    variable = value.removeprefix("env:")
    if not value.startswith("env:") or not variable.isidentifier():
        raise ValueError(f"{field_name} must be an env:VARIABLE reference")


class AppConfig(BaseModel):
    name: str = "Aegis"
    data_dir: str = "~/.local/share/aegis"
    log_level: Literal["debug", "info", "warning", "error"] = "info"

    @field_validator("data_dir")
    @classmethod
    def expand_data_dir(cls, v: str) -> str:
        return _expand_user_path(v)


class ProfileConfig(BaseModel):
    name: ProfileName = ProfileName.MVP


class AudioConfig(BaseModel):
    input_device: str = "default"
    output_device: str = "default"
    capture_rate_hz: int = Field(default=48000, ge=0)
    wake_sample_rate_hz: int = Field(default=16000, ge=8000)
    session_sample_rate_hz: int = Field(default=24000, ge=8000)
    channels: int = Field(default=1, ge=1, le=2)
    uplink_queue_ms: int = Field(default=500, ge=50)
    local_vad_enabled: bool = True
    local_vad_hangover_ms: int = Field(default=300, ge=0)
    duck_on_playback: bool = True


class WakeConfig(BaseModel):
    # Disabled by default: openwakeword is not installable on many Python 3.12+
    # hosts, and hey_aegis needs a custom model. Enable after installing an engine
    # (``uv sync --extra porcupine``) and configuring phrase/model/access key.
    enabled: bool = False
    engine: WakeEngine = WakeEngine.PORCUPINE
    phrase: str = "porcupine"
    threshold: float = Field(default=0.5, ge=0.0, le=1.0)
    custom_model_path: str = ""
    confirm_speech_timeout_s: float = Field(default=1.5, ge=0.0)
    porcupine_access_key_env: str = "PICOVOICE_ACCESS_KEY"
    porcupine_keyword_path: str = ""

    @field_validator("custom_model_path", "porcupine_keyword_path")
    @classmethod
    def expand_paths(cls, v: str) -> str:
        return _expand_user_path(v) if v else v


class ActivationConfig(BaseModel):
    hotkey: str = "Super+Shift+Space"
    hotkey_backend: HotkeyBackend = HotkeyBackend.AUTO
    push_to_talk_mode: PushToTalkMode = PushToTalkMode.TOGGLE
    chime_on_wake: bool = True
    chime_on_end: bool = False
    chime_on_connecting: bool = True


class SessionContextConfig(BaseModel):
    max_tool_result_chars_retained: int = Field(default=8000, ge=256)
    max_transcript_turns: int = Field(default=40, ge=1)
    strip_old_audio_items: bool = True
    summarize_when_turns_exceed: int = Field(default=30, ge=1)
    keep_last_n_tool_results: int = Field(default=8, ge=0)


class SessionConfig(BaseModel):
    provider: SessionProvider = SessionProvider.REALTIME
    model: str = "gpt-realtime-2.1-mini"
    voice: str = "alloy"
    idle_timeout_s: int = Field(default=45, ge=5)
    max_duration_s: int = Field(default=900, ge=30)
    max_session_cost_usd: float = Field(default=2.0, ge=0.0)
    connect_timeout_s: float = Field(default=8.0, ge=1.0)
    reuse_grace_s: float = Field(default=0.0, ge=0.0)
    instructions_file: str = "~/.config/aegis/instructions.md"
    reasoning_effort: Literal["minimal", "low", "medium", "high", "xhigh"] = "minimal"
    context: SessionContextConfig = Field(default_factory=SessionContextConfig)

    @field_validator("instructions_file")
    @classmethod
    def expand_instructions(cls, v: str) -> str:
        return _expand_user_path(v)


class OpenAIConfig(BaseModel):
    api_key_env: str = "OPENAI_API_KEY"
    realtime_url: str = "wss://api.openai.com/v1/realtime"
    chat_base_url: str = "https://api.openai.com/v1"
    keyring_service: str = "aegis"


class ChatGptOAuthConfig(BaseModel):
    """ChatGPT subscription OAuth credentials (not an API key)."""

    enabled: bool = True
    # Token file under XDG config (chmod 600)
    token_path: str = "~/.config/aegis/credentials/chatgpt_oauth.json"
    # Optional override for device/OAuth endpoints if OpenAI changes them
    auth_base_url: str = "https://auth.openai.com"
    # Client id used by Codex-style ChatGPT login (public desktop client)
    client_id: str = "app_EMoamEEZ73f0CkXaXp7hrann"
    # Prefer chat completions base when using OAuth bearer tokens
    api_base_url: str = "https://api.openai.com/v1"

    @field_validator("token_path")
    @classmethod
    def expand_token_path(cls, v: str) -> str:
        return _expand_user_path(v)


class LiteLLMConfig(BaseModel):
    """OpenAI-compatible LiteLLM proxy."""

    base_url: str = "http://127.0.0.1:4000/v1"
    api_key_env: str = "LITELLM_API_KEY"
    # When empty, many LiteLLM setups accept any non-empty key or "sk-1234"
    default_api_key: str = "sk-litellm"
    model: str = "gpt-4o-mini"


class OllamaConfig(BaseModel):
    """Local Ollama server (OpenAI-compatible /v1 endpoints)."""

    base_url: str = "http://127.0.0.1:11434/v1"
    # Native tags API is without /v1
    native_base_url: str = "http://127.0.0.1:11434"
    api_key_env: str = "OLLAMA_API_KEY"
    # Ollama ignores key but OpenAI clients often require a value
    default_api_key: str = "ollama"
    model: str = "llama3.2"


class AzureOpenAIConfig(BaseModel):
    """Azure OpenAI or Azure AI Foundry chat endpoints.

    api_style:
      - deployments: classic Azure OpenAI
        {endpoint}/openai/deployments/{deployment}/chat/completions?api-version=…
      - openai_v1: OpenAI-compatible path on Azure
        {endpoint}/openai/v1/chat/completions
      - foundry: Azure AI Foundry model inference
        {endpoint}/models/chat/completions?api-version=…
    """

    endpoint: str = ""
    api_key_env: str = "AZURE_OPENAI_API_KEY"
    api_version: str = "2024-10-21"
    # Deployment name (Azure OpenAI) or model id (Foundry)
    deployment: str = "gpt-4o-mini"
    api_style: Literal["deployments", "openai_v1", "foundry"] = "deployments"
    # bearer = Authorization: Bearer (Entra token); api_key = api-key header
    auth_mode: Literal["api_key", "bearer"] = "api_key"


class BedrockConfig(BaseModel):
    """AWS Bedrock Runtime Converse API (SigV4, no boto3 required)."""

    region: str = "us-east-1"
    # Model or inference profile id, e.g. amazon.nova-lite-v1:0
    model_id: str = "amazon.nova-lite-v1:0"
    # Credential env var names (standard AWS names by default)
    access_key_env: str = "AWS_ACCESS_KEY_ID"
    secret_key_env: str = "AWS_SECRET_ACCESS_KEY"
    session_token_env: str = "AWS_SESSION_TOKEN"
    region_env: str = "AWS_REGION"
    # Optional shared-credentials profile (~/.aws/credentials)
    profile: str = ""
    # Optional override of runtime endpoint host prefix
    endpoint_url: str = ""


class LLMConfig(BaseModel):
    """Multi-provider LLM settings used by settings UI and chat clients."""

    # Active chat/LLM provider for non-realtime sessions (mirrors session.provider when set)
    # Kept for clarity when session.provider is realtime but tools use a chat model.
    chat_provider: SessionProvider = SessionProvider.OPENAI_API
    temperature: float = Field(default=0.4, ge=0.0, le=2.0)
    max_tokens: int = Field(default=2048, ge=64)
    openai: OpenAIConfig = Field(default_factory=OpenAIConfig)
    chatgpt_oauth: ChatGptOAuthConfig = Field(default_factory=ChatGptOAuthConfig)
    litellm: LiteLLMConfig = Field(default_factory=LiteLLMConfig)
    ollama: OllamaConfig = Field(default_factory=OllamaConfig)
    azure_openai: AzureOpenAIConfig = Field(default_factory=AzureOpenAIConfig)
    bedrock: BedrockConfig = Field(default_factory=BedrockConfig)


class ShellRule(BaseModel):
    exe: str
    verbs: list[str] = Field(default_factory=lambda: ["*"])
    risk: Literal["read", "write", "destroy", "secrets", "network", "admin"] = "read"
    decision: Literal["auto", "prompt", "deny"] = "auto"
    allowed_flags: list[str] | None = None
    denied_flags: list[str] | None = None


class ToolsShellConfig(BaseModel):
    enabled: bool = False
    mode: ShellMode = ShellMode.ARGV_POLICY
    allowed_executable_dirs: list[str] = Field(
        default_factory=lambda: ["/usr/bin", "/usr/local/bin", "/bin"]
    )
    rules: list[ShellRule] = Field(default_factory=list)
    reserved_binaries: list[str] = Field(
        default_factory=lambda: [
            "kubectl",
            "oc",
            "helm",
            "docker",
            "podman",
            "nerdctl",
            "sudo",
            "ssh",
            "doas",
            "pkexec",
            "su",
        ]
    )
    denylist_substrings: list[str] = Field(
        default_factory=lambda: ["rm -rf /", "mkfs", "dd if="]
    )


class ToolsSecretsConfig(BaseModel):
    decision: SecretsDecision = SecretsDecision.PROMPT
    path_globs: list[str] = Field(
        default_factory=lambda: [
            "**/.ssh/**",
            "**/id_rsa*",
            "**/id_ed25519*",
            "**/id_ecdsa*",
            "**/*_rsa",
            "**/*_ed25519",
            "**/.env",
            "**/.env.*",
            "**/*.pem",
            "**/*.key",
            "**/credentials.json",
            "**/credentials*.json",
            "**/secrets.env",
            "**/aegis/secrets.env",
            "**/.aws/credentials",
            "**/.kube/config",
            "**/config/gcloud/**",
            "**/.gnupg/**",
            "**/keystore*",
            "**/*secret*",
        ]
    )


class ToolsApprovalConfig(BaseModel):
    default: ApprovalDefault = ApprovalDefault.AUTO_READONLY
    timeout_s: int = Field(default=60, ge=5)
    # Reserved for future voice "say confirm" UX — not yet wired.
    voice_confirm_phrase: bool = True
    mute_uplink_during_approval: bool = True
    session_grant_applies_to: SessionGrantScope = SessionGrantScope.SAME_TOOL

    @field_validator("session_grant_applies_to", mode="before")
    @classmethod
    def migrate_legacy_grant_scope(cls, value: object) -> object:
        """Safely accept scopes emitted by older Aegis configurations.

        Broad grants were never safe to implement for model-directed tools. Keep
        existing installs bootable by narrowing the retired values to one-shot
        approval; saving the config then persists the supported value.
        """
        if value in {"same_risk_class", "all"}:
            return SessionGrantScope.ONCE.value
        return value


class ToolsGitConfig(BaseModel):
    enabled: bool = False
    allow_commit: bool = False
    allow_push: bool = False
    deny_via_shell: bool = True
    shell_readonly_rules: bool = False


class ToolsKubectlConfig(BaseModel):
    enabled: bool = False
    allowed_namespaces: list[str] = Field(default_factory=lambda: ["staging", "dev"])
    allowed_verbs: list[str] = Field(
        default_factory=lambda: ["get", "describe", "logs", "top"]
    )
    context_allowlist: list[str] = Field(default_factory=list)
    deny_via_shell: bool = True
    env_allowlist: list[str] = Field(
        default_factory=lambda: ["KUBECONFIG", "KUBECTL_CONTEXT", "KUBERNETES_MASTER"]
    )


class ToolsConfig(BaseModel):
    enabled: list[str] = Field(default_factory=lambda: ["fs"])
    # Least-privilege default: a dedicated workspace under XDG data, not all of $HOME.
    working_directory: str = Field(default_factory=_default_workspace_dir)
    sandbox_to_workdir: bool = True
    max_output_bytes: int = Field(default=100_000, ge=1024)
    max_write_bytes: int = Field(default=500_000, ge=1024)
    default_timeout_s: int = Field(default=30, ge=1)
    max_tool_calls_per_turn: int = Field(default=8, ge=1)
    max_tool_calls_per_session: int = Field(default=64, ge=1)
    # Reserved — tool loop is serial today; field kept for forward config compat.
    parallel_read_tools: bool = False
    shell: ToolsShellConfig = Field(default_factory=ToolsShellConfig)
    secrets: ToolsSecretsConfig = Field(default_factory=ToolsSecretsConfig)
    approval: ToolsApprovalConfig = Field(default_factory=ToolsApprovalConfig)
    git: ToolsGitConfig = Field(default_factory=ToolsGitConfig)
    kubectl: ToolsKubectlConfig = Field(default_factory=ToolsKubectlConfig)

    @field_validator("working_directory")
    @classmethod
    def expand_workdir(cls, v: str) -> str:
        return _expand_user_path(v)


class McpLocalServer(BaseModel):
    name: str
    command: str
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    cwd: str | None = None

    @field_validator("env")
    @classmethod
    def require_secret_env_references(cls, value: dict[str, str]) -> dict[str, str]:
        for key, setting in value.items():
            lowered = key.lower()
            if any(term in lowered for term in ("key", "token", "secret", "password", "auth")):
                _validate_env_reference(setting, f"mcp.local.env[{key!r}]")
        return value


class McpLocalConfig(BaseModel):
    servers: list[McpLocalServer] = Field(default_factory=list)


class McpRemoteServer(BaseModel):
    label: str
    server_url: str
    allowed_tools: list[str] = Field(default_factory=list)
    require_approval: McpApproval = McpApproval.ALWAYS
    allow_private_server_url: bool = False
    headers: dict[str, str] = Field(default_factory=dict)
    authorization: str | None = None

    @field_validator("headers")
    @classmethod
    def require_header_env_references(cls, value: dict[str, str]) -> dict[str, str]:
        for header, reference in value.items():
            _validate_env_reference(reference, f"mcp.remote.headers[{header!r}]")
        return value

    @field_validator("authorization")
    @classmethod
    def require_authorization_env_reference(cls, value: str | None) -> str | None:
        if value is not None:
            _validate_env_reference(value, "mcp.remote.authorization")
        return value


class McpRemoteConfig(BaseModel):
    servers: list[McpRemoteServer] = Field(default_factory=list)


class McpConnector(BaseModel):
    label: str
    connector_id: str
    require_approval: McpApproval = McpApproval.ALWAYS
    allowed_tools: list[str] = Field(default_factory=list)
    authorization: str | None = None

    @field_validator("authorization")
    @classmethod
    def require_authorization_env_reference(cls, value: str | None) -> str | None:
        if value is not None:
            _validate_env_reference(value, "mcp.connectors.authorization")
        return value


class McpConnectorsConfig(BaseModel):
    items: list[McpConnector] = Field(default_factory=list)


class McpConfig(BaseModel):
    local: McpLocalConfig = Field(default_factory=McpLocalConfig)
    remote: McpRemoteConfig = Field(default_factory=McpRemoteConfig)
    connectors: McpConnectorsConfig = Field(default_factory=McpConnectorsConfig)


class PrivacyConfig(BaseModel):
    store_transcripts: bool = True
    store_audio: bool = False
    audio_debug_buffer: bool = False
    redact_secrets_in_audit: bool = True


class ObservabilityConfig(BaseModel):
    # In-process session metrics/logging only — no Prometheus exporter.
    metrics_enabled: bool = True
    # Unused; kept so older configs validate. Prefer journal/stderr logs.
    metrics_bind: str | None = None


class AegisConfig(BaseModel):
    """Top-level Aegis configuration after profile expansion."""

    app: AppConfig = Field(default_factory=AppConfig)
    profile: ProfileConfig = Field(default_factory=ProfileConfig)
    audio: AudioConfig = Field(default_factory=AudioConfig)
    wake: WakeConfig = Field(default_factory=WakeConfig)
    activation: ActivationConfig = Field(default_factory=ActivationConfig)
    session: SessionConfig = Field(default_factory=SessionConfig)
    # Top-level openai kept for backward compatibility with early configs
    openai: OpenAIConfig = Field(default_factory=OpenAIConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    tools: ToolsConfig = Field(default_factory=ToolsConfig)
    mcp: McpConfig = Field(default_factory=McpConfig)
    privacy: PrivacyConfig = Field(default_factory=PrivacyConfig)
    observability: ObservabilityConfig = Field(default_factory=ObservabilityConfig)

    @model_validator(mode="after")
    def sync_openai_and_mcp(self) -> AegisConfig:
        """Align openai blocks; validate remote MCP privacy."""
        # Prefer explicit top-level openai when present (defaults match)
        self.llm.openai = self.openai
        for server in self.mcp.remote.servers:
            if _looks_private_url(server.server_url) and not server.allow_private_server_url:
                raise ValueError(
                    f"MCP remote server {server.label!r} uses a private/local URL "
                    f"({server.server_url}). Set allow_private_server_url = true to "
                    "opt in (not recommended — prefer local stdio bridge)."
                )
        return self

    def model_dump_toml_dict(self) -> dict[str, Any]:
        """Plain dict suitable for toml serialization (enums → values)."""
        return self.model_dump(mode="json")


def _looks_private_url(url: str) -> bool:
    # Single robust hostname-parsing implementation (see aegis.util.net).
    from aegis.util.net import is_private_url

    return is_private_url(url)


# Default read-only shell rules applied when shell is enabled without custom rules.
DEFAULT_READ_SHELL_RULES: list[ShellRule] = [
    ShellRule(exe="ls", risk="read", decision="auto"),
    ShellRule(exe="pwd", risk="read", decision="auto"),
    ShellRule(
        exe="head",
        risk="read",
        decision="auto",
        denied_flags=["-c", "--bytes"],  # still path-checked elsewhere
    ),
    ShellRule(exe="tail", risk="read", decision="auto"),
    ShellRule(exe="rg", risk="read", decision="auto"),
    ShellRule(
        exe="cat",
        risk="read",
        decision="auto",
        denied_flags=[],  # path globs handle secrets
    ),
]
