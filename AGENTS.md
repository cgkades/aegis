# Agent notes for Aegis

Guidance for humans and coding agents working in this repository.

## Product

- **Name:** Aegis (locked). Wake phrase: “Hey Aegis”.
- **Design:** [`DESIGN.md`](./DESIGN.md) (approved; implementation spine complete).
- **Platform:** Linux desktop MVP.

## Non-negotiables

1. Never stream mic audio to the cloud while idle.
2. Private system tools are **client-side `function` tools** (daemon executes).
3. **No `shell=True`** — argv-only policy engine.
4. Shell **off** in `mvp`. kubectl/oc/helm/sudo/ssh reserved DENY via shell.
5. Secrets path globs never auto for `read_file` / shell.
6. API keys via env / `secrets.env` only.

## Stack

- Python 3.12+, **uv**
- Layout: `src/aegis/…`, tests under `tests/`
- CLI: `aegis` → `aegis.cli:main`

## Commands

```bash
uv sync --all-extras
uv run aegis doctor
uv run aegis session once --backend mock
uv run pytest
uv run ruff check src tests
```

## Profiles

| Profile | Use |
| --- | --- |
| mvp | Conversation + fs tools |
| standard | + git/process/write |
| oncall | + full model + structured kubectl |

## Extending

- Tools: `src/aegis/tools/builtin/` + register in `factory.py`
- MCP local: config `mcp.local.servers` + `LocalMcpBridge`
- Voice providers: `src/aegis/voice/factory.py`
