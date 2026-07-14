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
    REALTIME = "realtime"
    GPT_LIVE = "gpt_live"
    TEXT_FALLBACK = "text_fallback"
    HYBRID_TEXT_TOOLS = "hybrid_text_tools"


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
    ONCE = "once"
    SAME_TOOL = "same_tool"
    SAME_RISK_CLASS = "same_risk_class"
    ALL = "all"


class McpApproval(StrEnum):
    ALWAYS = "always"
    NEVER = "never"
    # OpenAI also supports finer-grained modes; keep simple for v1 config.


def _expand_user_path(value: str | Path) -> str:
    return str(Path(str(value)).expanduser())


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
    enabled: bool = True
    engine: WakeEngine = WakeEngine.OPENWAKEWORD
    phrase: str = "hey_aegis"
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
    keyring_service: str = "aegis"


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
    voice_confirm_phrase: bool = True
    mute_uplink_during_approval: bool = True
    session_grant_applies_to: SessionGrantScope = SessionGrantScope.SAME_TOOL


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
    working_directory: str = "~"
    sandbox_to_workdir: bool = True
    max_output_bytes: int = Field(default=100_000, ge=1024)
    default_timeout_s: int = Field(default=30, ge=1)
    max_tool_calls_per_turn: int = Field(default=8, ge=1)
    max_tool_calls_per_session: int = Field(default=64, ge=1)
    parallel_read_tools: bool = True
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


class McpRemoteConfig(BaseModel):
    servers: list[McpRemoteServer] = Field(default_factory=list)


class McpConnector(BaseModel):
    label: str
    connector_id: str
    require_approval: McpApproval = McpApproval.ALWAYS
    allowed_tools: list[str] = Field(default_factory=list)
    authorization: str | None = None


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
    metrics_enabled: bool = True
    metrics_bind: str | None = None


class AegisConfig(BaseModel):
    """Top-level Aegis configuration after profile expansion."""

    app: AppConfig = Field(default_factory=AppConfig)
    profile: ProfileConfig = Field(default_factory=ProfileConfig)
    audio: AudioConfig = Field(default_factory=AudioConfig)
    wake: WakeConfig = Field(default_factory=WakeConfig)
    activation: ActivationConfig = Field(default_factory=ActivationConfig)
    session: SessionConfig = Field(default_factory=SessionConfig)
    openai: OpenAIConfig = Field(default_factory=OpenAIConfig)
    tools: ToolsConfig = Field(default_factory=ToolsConfig)
    mcp: McpConfig = Field(default_factory=McpConfig)
    privacy: PrivacyConfig = Field(default_factory=PrivacyConfig)
    observability: ObservabilityConfig = Field(default_factory=ObservabilityConfig)

    @model_validator(mode="after")
    def validate_remote_mcp_privacy(self) -> AegisConfig:
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
    lower = url.lower()
    if lower.startswith("http://localhost") or lower.startswith("https://localhost"):
        return True
    if "://127." in lower or "://[::1]" in lower or "://0.0.0.0" in lower:
        return True
    # RFC1918 rough check
    for prefix in ("://10.", "://192.168.", "://172.16.", "://172.17.", "://172.18."):
        if prefix in lower:
            return True
    for i in range(16, 32):
        if f"://172.{i}." in lower:
            return True
    return False


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
