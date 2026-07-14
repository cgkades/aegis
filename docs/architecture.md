# Aegis architecture

See the full design in [`../DESIGN.md`](../DESIGN.md).

## Runtime components

```text
aegisd (optional always-on)
  ├─ local wake (openWakeWord / Porcupine / mock)
  ├─ AudioGraph (single capture + resample + VAD)
  ├─ unix socket IPC
  └─ on activation → session runner

aegis session once (foreground dogfood)
  ├─ SessionMachine
  ├─ VoiceSession (Realtime | mock | GPT-Live stub)
  ├─ CloudAudioGateway (sole cloud audio path)
  ├─ ToolRegistry + policy + approval
  └─ StatusPresenter (disclosure)
```

## Non-negotiables

1. Idle: no cloud mic audio.
2. Private tools execute on-device as function tools.
3. Argv-only command execution; never `shell=True`.
4. Shell must not bypass structured kubectl/git ownership.

## Provider switch

`session.provider`:

| Value | Status |
| --- | --- |
| `realtime` | v1 default (`gpt-realtime-2.1-mini`) |
| `mock` | offline tests / dogfood |
| `gpt_live` | stub until API available |
| `text_fallback` | stub cost profile |
