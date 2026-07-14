# Aegis — Specification Index

| Field | Value |
| --- | --- |
| **Product** | Aegis |
| **Wake phrase** | “Hey Aegis” |
| **Platform** | Linux desktop (MVP) |
| **Spec style** | [OpenSpec](https://github.com/Fission-AI/OpenSpec) |
| **Authoritative specs** | [`openspec/specs/`](./openspec/specs/) |
| **Planned work (deltas)** | [`openspec/changes/`](./openspec/changes/) |
| **Design (implementation detail)** | [`DESIGN.md`](./DESIGN.md) |
| **Agent notes** | [`AGENTS.md`](./AGENTS.md) |
| **As-of** | 2026-07-13 (multi-LLM + Azure + Bedrock shipped) |

This file is the **human entry point**. Behavioral contracts live under `openspec/` in OpenSpec format (`### Requirement:` / `#### Scenario:` with SHALL/MUST). They describe the intended Aegis product; implementation may lag a requirement and must not silently weaken it. Keep `DESIGN.md` for architecture and PR history.

---

## What Aegis is

Aegis is a **local-first, always-on personal voice/agent** for Linux. It listens only for a local wake phrase (or CLI/hotkey), then opens a short-lived session with a configured LLM/voice backend, runs **on-device tools** under policy, and tears the cloud session down when done.

**Not goals (v1):** multi-user daemon, mobile mesh, always-streaming cloud wake, unsupervised production-mutating automation, Windows/macOS first-class support.

---

## Non-negotiables

1. Never stream mic audio to the cloud while idle (local wake only).
2. Private system tools are **client-side `function` tools** (daemon executes).
3. **No `shell=True`** — argv-only policy engine.
4. Shell **off** in `mvp`; kubectl/oc/helm/sudo/ssh reserved DENY via shell.
5. Secrets path globs never auto for `read_file` / shell.
6. API keys via env / `.env` / `secrets.env` only — never commit secrets.

---

## Capability map (product contract)

| Capability | Spec |
| --- | --- |
| Product identity & trust model | [product](./openspec/specs/product/spec.md) |
| Privacy & security | [privacy-security](./openspec/specs/privacy-security/spec.md) |
| Configuration & profiles | [configuration](./openspec/specs/configuration/spec.md) |
| LLM / voice providers | [llm-providers](./openspec/specs/llm-providers/spec.md) |
| Session lifecycle | [session](./openspec/specs/session/spec.md) |
| Wake & activation | [wake-activation](./openspec/specs/wake-activation/spec.md) |
| Tools & policy | [tools-policy](./openspec/specs/tools-policy/spec.md) |
| MCP | [mcp](./openspec/specs/mcp/spec.md) |
| Daemon & IPC | [daemon-ipc](./openspec/specs/daemon-ipc/spec.md) |
| CLI | [cli](./openspec/specs/cli/spec.md) |
| Settings UI | [settings-ui](./openspec/specs/settings-ui/spec.md) |
| Cost, audit, observability | [observability](./openspec/specs/observability/spec.md) |

---

## Provider matrix (summary)

| Provider | Kind | Voice duplex | Chat/tools | Auth |
| --- | --- | --- | --- | --- |
| `realtime` | OpenAI Realtime | **Yes** | Via session | `OPENAI_API_KEY` |
| `openai_api` | Chat Completions | No (cascaded STT/TTS planned) | Yes | `OPENAI_API_KEY` |
| `chatgpt_oauth` | Chat via OAuth | No | Yes | Device/OAuth token file |
| `azure_openai` | Azure OpenAI / Foundry | No | Yes | `AZURE_OPENAI_API_KEY` or Entra bearer |
| `bedrock` | AWS Bedrock Converse | No | Yes | AWS env / profile (SigV4, no boto3) |
| `litellm` | OpenAI-compatible proxy | No | Yes | `LITELLM_API_KEY` optional |
| `ollama` | Local Ollama | No | Yes | Local HTTP |
| `mock` | Offline dogfood | Simulated | Simulated | None |

When a text-only provider is selected, Settings and every available status/presence
surface MUST disclose that voice is unavailable until a cascaded STT/TTS path is
configured. Activation MUST explain the limitation instead of failing silently.

---

## Profiles (summary)

| Profile | Tools (default) | Shell | kubectl |
| --- | --- | --- | --- |
| `mvp` | `list_dir`, `read_file`, `search_files` | Off | Off |
| `standard` | + git, process, write | Optional (off by default) | Off |
| `oncall` | + structured kubectl | Optional | Structured only; shell DENY |

---

## Planned enhancements (open changes)

These are **not** current behavior. Review and edit under `openspec/changes/<name>/`.

| Change | Intent |
| --- | --- |
| [cascaded-stt-tts](./openspec/changes/cascaded-stt-tts/) | Mic → STT → chat model → TTS for non-Realtime providers |
| [custom-hey-aegis-wake](./openspec/changes/custom-hey-aegis-wake/) | Ship/install a reliable openWakeWord “Hey Aegis” model |
| [always-on-dogfood](./openspec/changes/always-on-dogfood/) | Real-device always-on daemon dogfood + install polish |
| [tray-presence](./openspec/changes/tray-presence/) | System tray status / mute / quit (Phase 1 design) |
| [gpt-live-adapter](./openspec/changes/gpt-live-adapter/) | Real GPT-Live adapter when API is available (stub exists) |
| [hybrid-text-tool-session](./openspec/changes/hybrid-text-tool-session/) | Cheap text model for tool loops + speech only when needed |

---

## How to revise intent

1. **Change current behavior contract** → edit `openspec/specs/<capability>/spec.md` (or open a change with `## MODIFIED Requirements`).
2. **Add planned work** → add/edit a folder under `openspec/changes/` with `proposal.md` + delta `specs/`.
3. **Architecture / code layout** → `DESIGN.md` (not behavioral SHALL).
4. After you approve deltas and they ship, archive the change and merge deltas into `openspec/specs/` (OpenSpec archive flow).

---

## Verification baseline (as implemented)

```bash
uv sync --all-extras
uv run pytest          # ≥80% coverage enforced
uv run ruff check src tests
uv run aegis doctor
uv run aegis session once --backend mock
```

---

## Open questions (for you)

Answer under each item (or rewrite the question). Prefer short decisions: **yes / no / later / N/A**, plus any nuance. After you fill this in, we can fold answers into `openspec/specs/` and `openspec/changes/`.

### Product & daily use

1. **Primary daily path:** What do you want as the *default* day-to-day experience?
   - [ ] Always-on daemon + “Hey Aegis”
   - [ ] Hotkey / DE keybind only (no always-on mic)
   - [ ] Foreground `aegis session once` when needed
   - [ ] Other: ___  
   **Answer:**

2. **Default provider when you sit down tomorrow:** realtime / ollama / azure / bedrock / openai_api / chatgpt_oauth / other?  
   **Answer:**

3. **Voice quality vs cost vs privacy (rank 1–3):** duplex voice quality ___ · $ cost ___ · keep data local ___  
   **Answer:**

4. **Is “Jarvis-like always listening companion” still the north star, or more “on-call tool when I summon it”?**  
   **Answer:**

### Wake word

5. **Custom “Hey Aegis” model:** train/install a custom OWW model soon, stick with generic phrases + hotkey for now, or switch Porcupine?  
   **Answer:**

6. **`confirm_speech` after wake (default ~1.5s):** keep, shorten, lengthen, or disable?  
   **Answer:**

7. **False-accept tolerance:** prefer occasional false starts (easy wake) or fewer wakes (stricter threshold)?  
   **Answer:**

### Providers & models

8. **Azure:** do you already have a resource/deployment we should treat as a first-class default (endpoint style: deployments vs Foundry)?  
   **Answer:**

9. **Bedrock:** preferred model/inference profile ids, and region? Any requirement for SSO/profile-only (no long-lived keys in `.env`)?  
   **Answer:**

10. **Ollama:** default model for “fast local” vs “smart local”? (You have several installed already.)  
    **Answer:**

11. **ChatGPT OAuth:** is subscription OAuth a must-have daily path, or a nice-to-have fallback when you don’t want API spend?  
    **Answer:**

12. **Should LiteLLM stay first-class**, or is it only a bridge until Azure/Bedrock/Ollama cover you?  
    **Answer:**

### Cascaded voice (planned)

13. **Priority of STT→chat→TTS for non-Realtime providers:** P0 soon / P1 after dogfood / nice-to-have / drop?  
    **Answer:**

14. **Preferred STT:** fully local (which engine?) vs cloud STT (which?) vs “whatever works”?  
    **Answer:**

15. **Preferred TTS:** local vs cloud vs OpenAI TTS vs system speech-dispatcher?  
    **Answer:**

16. **Barge-in for cascaded mode:** must interrupt mid-TTS, or is turn-taking OK for v1 cascade?  
    **Answer:**

### Tools, shell, on-call

17. **Approval strictness:** keep `auto_readonly` + prompt non-reads, or prompt **every** tool including reads?  
    **Answer:**

18. **Shell in daily life:** ever enable shell on this machine, or structured tools only forever for personal use?  
    **Answer:**

19. **kubectl:** which contexts/namespaces should the default allowlist cover (if any)? Or kubectl off until you say otherwise?  
    **Answer:**

20. **Write/patch tools:** OK with approval prompts, or do you want a “trusted project dir auto-write” mode?  
    **Answer:**

21. **Browser / web research tool:** Playwright MCP, simple web search, both, or none for now?  
    **Answer:**

22. **Fish shell:** any need for interactive fish features in tools, or plain argv subprocess is enough?  
    **Answer:**

### UX / presence

23. **Tray icon:** want it soon, later, or never (CLI + chime enough)?  
    **Answer:**

24. **Settings UI:** is the local web page the long-term config UX, or do you want TUI/CLI-only eventually?  
    **Answer:**

25. **Chimes / voice disclosure (“cloud session open”):** keep as-is, quieter, or more explicit?  
    **Answer:**

### Security & machine

26. **YubiKey / hardware auth:** is browser FIDO still a pain for GitHub/OpenAI login on this box, and should Aegis docs/scripts care?  
    **Answer:**

27. **Secrets globs:** any extra paths to always treat as secrets (beyond defaults like `~/.ssh`, `*.pem`, etc.)?  
    **Answer:**

28. **Audit log retention:** keep forever locally, rotate N days, or ship somewhere?  
    **Answer:**

### Roadmap priority (reorder or kill)

29. **Rank or drop these planned changes** (1 = next, …, or `drop`):

| Change | Your rank / decision |
| --- | --- |
| cascaded-stt-tts | |
| custom-hey-aegis-wake | |
| always-on-dogfood | |
| tray-presence | |
| gpt-live-adapter | |
| hybrid-text-tool-session | |
| (add your own) ___ | |

**Answer / notes:**

30. **Anything in the current specs that is *wrong* for your intent** (too ambitious, too cautious, missing a must-have)?  
    **Answer:**

31. **Anything you do *not* want Aegis to ever do** (hard non-goals beyond DESIGN’s list)?  
    **Answer:**

32. **Success definition in 30 days:** what would make you say “this is actually my daily driver”?  
    **Answer:**

### Freeform

33. **Other notes / constraints / brand preferences:**  
    **Answer:**
