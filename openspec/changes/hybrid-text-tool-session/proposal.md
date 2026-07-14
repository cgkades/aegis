# Proposal: Hybrid text tool loop + speech

## Intent

Long tool-heavy investigations on Realtime are expensive. A hybrid mode should use a cheaper text model for multi-step tools and only use speech (Realtime or TTS) for user-facing talk turns.

## Scope

**In scope**
- Session mode that routes tool loops to a chat model
- Optional speech channel for summaries / Q&A
- Cost meter attribution across both paths

**Out of scope**
- Fully automatic model routing ML
- Replacing structured tools

## Approach

Extend session.provider / hybrid config to pair a speech backend with a `llm.chat_provider` for tools. Preserve approval/audit on all tools.

## Impact

Lowers cost for on-call style workflows; adds config complexity (keep defaults simple).
