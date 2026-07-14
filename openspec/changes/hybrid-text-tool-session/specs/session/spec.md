# Delta for Session Lifecycle

## ADDED Requirements

### Requirement: Hybrid speech + text tool mode

The system SHALL support a hybrid session mode where multi-step tool execution can run on a configured chat/text model while user-facing speech uses a speech backend or TTS.

#### Scenario: Tool loop on text model

- **GIVEN** hybrid mode is enabled with chat_provider=ollama and speech backend configured
- **WHEN** the agent performs multiple tool calls
- **THEN** those tool turns may execute against the chat provider
- **AND** approvals/audit still apply

#### Scenario: User hears summary

- **WHEN** the agent produces a user-facing answer in hybrid mode
- **THEN** speech or TTS delivers the summary through the normal playback path
