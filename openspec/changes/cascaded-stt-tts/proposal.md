# Proposal: Cascaded STT → LLM → TTS for chat providers

## Intent

Non-Realtime providers (Ollama, Azure, Bedrock, LiteLLM, OpenAI chat, ChatGPT OAuth) currently accept PCM but ignore it. Users should be able to speak and hear replies with these backends without requiring OpenAI Realtime.

## Scope

**In scope**
- Local or cloud STT for uplink after activation
- Existing chat LLM path for reasoning/tools
- TTS for agent replies to speakers
- Clear UX that duplex barge-in quality may be worse than Realtime

**Out of scope**
- Matching Realtime full-duplex latency/quality
- Replacing Realtime as the default best-voice path

## Approach

Add an optional cascaded pipeline behind the existing `VoiceSession` protocol: buffer/VAD utterance → STT text → `inject_user_text` / chat → TTS PCM downlink. Provider choice for STT/TTS should be configurable (local preferred for privacy when possible).

## Impact

- Non-breaking for Realtime
- Enables voice dogfood on free/local models
- New config keys under `llm` or `voice.cascade`
