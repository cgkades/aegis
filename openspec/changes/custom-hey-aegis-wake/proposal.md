# Proposal: Custom “Hey Aegis” wake model

## Intent

Default openWakeWord models may not reliably detect “Hey Aegis”. Ship or document a first-class custom model path and install steps so always-on wake matches the locked product phrase.

## Scope

**In scope**
- Recommended model artifact location / download or training notes
- Config defaults pointing at the Aegis phrase model when present
- Doctor check for model presence

**Out of scope**
- Building a proprietary wake-training SaaS product
- Cloud wake detection

## Approach

Prefer openWakeWord custom model under a well-known path; keep Porcupine as pluggable alternative with access key. Document false-accept tuning (threshold, confirm-speech).

## Impact

Improves always-on usability; no change to idle privacy non-negotiables.
