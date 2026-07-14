# Proposal: GPT-Live voice adapter

## Intent

DESIGN requires a minimal VoiceSession abstraction so GPT-Live can replace or complement Realtime when a developer API is production-ready. A stub exists today; a real adapter is planned.

## Scope

**In scope**
- Real adapter implementing VoiceSession
- Provider switch `gpt_live` (or successor name)
- Feature detection / clear error when API unavailable

**Out of scope**
- Hard dependency on GPT-Live for MVP
- Removing Realtime

## Approach

Keep session.machine free of provider event names; implement mapping only in `voice/` adapter module. Fall back messaging if API terms change.

## Impact

Reduces vendor lock risk; optional until API stabilizes.
