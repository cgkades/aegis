# Wake & Activation

## Purpose

Define local wake-word engines and non-voice activation paths (CLI, socket, hotkey guidance).

## Requirements

### Requirement: Pluggable wake engines

The system SHALL support pluggable local wake engines including at least openWakeWord, Porcupine, and a mock engine for tests.

#### Scenario: Mock wake in tests

- **WHEN** tests use the mock wake engine
- **THEN** wake events can be simulated without audio hardware

#### Scenario: Engine selection from config

- **WHEN** `wake.engine` is set
- **THEN** the factory constructs the corresponding backend

### Requirement: Local-only wake path

Wake detection MUST run locally. Wake scoring MUST NOT require cloud audio streaming.

#### Scenario: Idle listening

- **GIVEN** wake is enabled and daemon is idle
- **WHEN** audio frames are processed for KWS
- **THEN** processing remains on-device

### Requirement: Optional confirm-speech gate

When confirm-speech timeout is configured, the system SHOULD require local speech after a wake hit before opening a cloud session, to reduce false-accept cost.

#### Scenario: No speech after wake

- **GIVEN** confirm-speech is enabled
- **WHEN** wake fires but no speech is detected before timeout
- **THEN** the system returns to idle without opening cloud audio

### Requirement: CLI and socket activation always work

Activation via CLI/socket MUST work even when global hotkey grab is unavailable (e.g. constrained Wayland environments).

#### Scenario: session start via CLI/socket

- **WHEN** a user runs `aegis session start` against a running daemon
- **THEN** the daemon begins a session without requiring wake audio

### Requirement: Hotkey best-effort with DE guidance

Global hotkey capture MAY be best-effort. The product SHALL document how to bind a DE custom shortcut to activation commands when OS grab is unavailable.

#### Scenario: activation help command

- **WHEN** the user runs `aegis activation`
- **THEN** guidance is printed for hotkey / DE keybind / CLI activation paths

### Requirement: Custom wake model path

Configuration SHALL allow a custom wake model path for openWakeWord (or equivalent) so users can install a “Hey Aegis” model without code changes.

#### Scenario: custom_model_path set

- **WHEN** `wake.custom_model_path` points to a model file
- **THEN** the openWakeWord backend prefers that model when loading
