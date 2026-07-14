# Delta for LLM & Voice Providers

## ADDED Requirements

### Requirement: GPT-Live provider adapter

When the GPT-Live (or successor continuous-voice) developer API is available, the system SHALL offer a VoiceSession adapter selectable via provider config that maps to the shared session event model.

#### Scenario: Provider selected without API

- **GIVEN** GPT-Live API credentials/endpoints are not configured
- **WHEN** the user selects the gpt_live provider
- **THEN** the system fails with a clear “not configured / not available” message rather than hanging

#### Scenario: Successful live session

- **GIVEN** API access is configured
- **WHEN** a gpt_live session connects
- **THEN** ready/audio/transcript/tool events surface through the shared protocol
- **AND** idle teardown closes cloud audio via the gateway
