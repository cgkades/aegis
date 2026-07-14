# Delta for LLM & Voice Providers

## ADDED Requirements

### Requirement: Cascaded voice for chat providers

Chat-only providers MUST support an optional cascaded voice mode: speech-to-text for user audio, chat completion for reasoning/tools, and text-to-speech for agent replies.

#### Scenario: Cascaded ollama voice turn

- **GIVEN** provider is `ollama` and cascade STT/TTS is enabled and available
- **WHEN** the user speaks a complete utterance after activation
- **THEN** STT produces text
- **AND** the chat model receives that text
- **AND** TTS plays the agent reply

#### Scenario: Cascade disabled keeps text-only behavior

- **GIVEN** cascade is disabled
- **WHEN** PCM audio is sent to a chat provider session
- **THEN** audio may be ignored without error
- **AND** text inject continues to work

### Requirement: Realtime remains preferred duplex path

When the user wants highest-quality full-duplex barge-in, the system SHOULD continue to recommend `realtime` over cascaded chat providers.

#### Scenario: Settings discloses capability

- **WHEN** the settings UI describes providers
- **THEN** Realtime is labeled as full duplex voice
- **AND** chat providers are labeled chat or cascaded voice as applicable
