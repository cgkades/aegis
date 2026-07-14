# Aegis — OpenSpec project context

## Purpose

Aegis is a local-first always-on personal voice AI agent for Linux desktops. It combines on-device wake-word listening, short-lived cloud or local LLM sessions, and guarded local tool execution for ops / on-call workflows.

## Product locks

| Item | Value |
| --- | --- |
| Name | **Aegis** (not Jarvis) |
| Wake phrase | “Hey Aegis” (configurable) |
| CLI / package | `aegis` |
| Config | `~/.config/aegis/` |
| Stack | Python 3.12+, `uv`, layout `src/aegis/` |
| Platform MVP | Linux desktop (PipeWire/Pulse, Wayland/X11) |
| Trust model | Single-user login session; user-owned sockets/files |

## Related documents

| Doc | Role |
| --- | --- |
| [`../SPEC.md`](../SPEC.md) | Human index of specs + planned work |
| [`../DESIGN.md`](../DESIGN.md) | Approved architecture & PR plan (implementation detail) |
| [`../AGENTS.md`](../AGENTS.md) | Agent/contributor non-negotiables & commands |
| [`../README.md`](../README.md) | Quick start |

## Spec organization

- `openspec/specs/` — **current** behavioral source of truth (what is implemented / accepted now).
- `openspec/changes/` — **proposed** enhancements as delta specs (what we plan next).
- Prefer behavior (WHAT) in specs; keep class names, libraries, and file maps in `DESIGN.md`.

## Tech constraints (normative for agents)

1. Idle: no cloud mic audio.
2. Tools execute on-device; no `shell=True`.
3. Profiles gate privilege (`mvp` / `standard` / `oncall`).
4. Secrets never committed; redacted in audit logs.
5. Tests: `uv run pytest` with coverage ≥ 80%.

## Domain glossary

| Term | Meaning |
| --- | --- |
| KWS | Keyword spotting / wake-word engine (local) |
| CloudAudioGateway | Sole path that may open cloud audio sessions |
| VoiceSession | Provider-agnostic session protocol |
| Chat provider | Text LLM path (Ollama, Azure, Bedrock, etc.) — not full duplex yet |
| Realtime | OpenAI Realtime duplex speech session |
| Argv policy | Command execution as argv lists only, never shell strings |
