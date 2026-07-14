# Aegis

**Aegis** is a local-first, always-on personal voice AI agent for Linux desktops.

Say **“Hey Aegis”** (or use a hotkey / CLI) to open a full-duplex voice session with OpenAI’s Realtime API. Aegis answers questions and runs **local tools** — files, git, process inspection, optional shell under argv policy, MCP servers, and structured kubectl — so you can pair-debug incidents hands-free.

## Status

**v0.1 — implementation complete for design spine (PRs 1–22).**

| Doc | Path |
| --- | --- |
| Spec (OpenSpec index) | [`SPEC.md`](./SPEC.md) |
| OpenSpec capabilities | [`openspec/specs/`](./openspec/specs/) |
| Planned changes | [`openspec/changes/`](./openspec/changes/) |
| Design | [`DESIGN.md`](./DESIGN.md) |
| Architecture | [`docs/architecture.md`](./docs/architecture.md) |
| Security | [`docs/security.md`](./docs/security.md) |
| Cost | [`docs/cost.md`](./docs/cost.md) |

## Quick start

Requirements: Linux, Python 3.12+, [uv](https://docs.astral.sh/uv/).

```bash
cd ai-audio-agent
uv sync --all-extras

# Config
uv run aegis config init
uv run aegis config show
uv run aegis doctor

# LLM settings page (local browser UI)
cp .env.example .env   # then edit OPENAI_API_KEY= / LiteLLM keys as needed
uv run aegis settings  # http://127.0.0.1:8765

# Providers (Settings UI or --backend):
#   realtime       — OpenAI Realtime duplex (API key) — full voice
#   openai_api     — OpenAI Chat Completions (API key)
#   chatgpt_oauth  — Sign in with ChatGPT (device code / paste token)
#   azure_openai   — Azure OpenAI deployments or Azure AI Foundry
#   bedrock        — AWS Bedrock Runtime Converse (SigV4)
#   litellm        — LiteLLM OpenAI-compatible proxy
#   ollama         — local Ollama models
#   mock           — offline dogfood
#
# Chat providers (everything except realtime) are text/tools today;
# cascaded STT/TTS for voice is a follow-up.

uv run aegis auth login          # ChatGPT OAuth
uv run aegis auth status

# Offline dogfood (no API key)
uv run aegis session once --backend mock

# Live voice (Realtime)
export OPENAI_API_KEY=sk-...
# sudo apt install libportaudio2
uv sync --extra audio
uv run aegis session once --backend realtime

# Local / proxy / cloud chat providers are configurable in the Settings UI,
# but cannot run through the voice-session CLI until cascaded STT/TTS lands.
```

### Azure OpenAI / Foundry

```bash
# .env
AZURE_OPENAI_API_KEY=...

# config.toml (or Settings UI)
# [llm.azure_openai]
# endpoint = "https://my-resource.openai.azure.com"
# deployment = "gpt-4o-mini"
# api_style = "deployments"   # or "openai_v1" / "foundry"
# api_version = "2024-10-21"
# Azure OpenAI is currently text-only; it cannot run via `session once` yet.
```

### AWS Bedrock

```bash
# credentials via env or ~/.aws/credentials profile
export AWS_ACCESS_KEY_ID=...
export AWS_SECRET_ACCESS_KEY=...
export AWS_REGION=us-east-1

# [llm.bedrock]
# region = "us-east-1"
# model_id = "amazon.nova-lite-v1:0"
# profile = ""   # optional shared-credentials profile name
# Bedrock is currently text-only; it cannot run via `session once` yet.
```

### Always-on daemon

```bash
uv run aegis daemon              # foreground
uv run aegis status
uv run aegis session start       # IPC activate
uv run aegis activation          # hotkey / DE keybind help

# systemd --user
./scripts/install-user-service.sh
systemctl --user start aegis
```

## Non-negotiables

1. **Local wake only** — idle mic audio never leaves the machine.
2. **Full-duplex voice** — OpenAI Realtime (`gpt-realtime-2.1-mini` default).
3. **On-device tools** — private access as client-side function tools + approval + audit.
4. **Cheap when idle** — KWS only until wake; Realtime torn down promptly.

## Profiles

| Profile | Model | Tools |
| --- | --- | --- |
| `mvp` | mini | `fs` |
| `standard` | mini | `fs`, `git`, `process` |
| `oncall` | full | + structured `kubectl` |

```bash
uv run aegis --profile oncall config show
uv run aegis --profile oncall session once --backend realtime
```

## Tools (mvp)

- `list_dir`, `read_file`, `search_files`
- Optional: `run_command` (argv-only, off by default)
- `standard`: `git_*`, `list_processes`, `tail_log`, `env_info`, `write_file`, `apply_patch` (when `write` pack enabled)
- `oncall`: structured `kubectl` (shell kubectl always denied)

## MCP

- **Local stdio**: configure `[[mcp.local.servers]]` — bridged as function tools.
- **Remote HTTPS**: `[[mcp.remote.servers]]` — injected into Realtime session (`require_approval = always` default).

## Tests

```bash
uv sync --all-extras
uv run pytest                 # enforces ≥80% coverage (currently ~84%)
uv run ruff check src tests
```

Coverage is measured with `pytest-cov` on the `aegis` package. Major features covered:

- Config/profiles, session state machine, tool policy (argv/secrets/kubectl)
- FS/git/process/write tools, MCP bridge + remote injection
- Realtime adapter (mocked WS), mock voice, provider factory
- Daemon IPC, activation backends, cost/context metrics
- CLI (`doctor`, `config`, `session once --backend mock`)

## License

MIT — see [`LICENSE`](./LICENSE).
