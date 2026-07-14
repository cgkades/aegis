# Session Lifecycle

## Purpose

Define how sessions start, run tools, handle approvals, and return to idle with cost and duration controls.

## Requirements

### Requirement: Provider-agnostic session protocol

Session orchestration SHALL depend on a minimal voice/session protocol (connect, audio, tool results, interrupt, end, events) rather than Realtime-specific event type names in the state machine.

#### Scenario: Adapters map to shared events

- **WHEN** any backend session runs
- **THEN** orchestration consumes a shared event set (ready, transcripts, tool calls, usage, error, ended)
- **AND** provider-specific wire formats remain inside adapters

### Requirement: Session state machine

The system SHALL implement a session lifecycle covering idle, connecting/active, approval-pending, ending, and return to idle.

#### Scenario: Successful mock session

- **WHEN** a mock session starts and completes
- **THEN** the user sees connect → active disclosure → end
- **AND** idle privacy is restored after end

#### Scenario: Connect failure does not leave cloud audio open

- **WHEN** connection fails or times out
- **THEN** the session returns to idle
- **AND** cloud audio is not left open

### Requirement: Foreground dogfood session

The CLI SHALL support `aegis session once` to run a foreground session without requiring the always-on daemon.

#### Scenario: session once with backend flag

- **WHEN** `aegis session once --backend mock` is invoked
- **THEN** a complete short session runs in-process

### Requirement: Approval pending mutes uplink

When a tool requires interactive approval, the system SHALL enter an approval-pending state that mutes mic uplink to the cloud for that session until allow/deny is resolved (or session ends).

#### Scenario: Prompted tool call

- **GIVEN** policy decision is `prompt`
- **WHEN** the tool is requested during an active session
- **THEN** uplink is muted while awaiting user decision
- **AND** a allow/deny result is returned to the model path

### Requirement: Session cost and duration caps

Sessions SHALL honor configured maximum duration and maximum session cost limits, ending the session when exceeded.

#### Scenario: Max duration

- **GIVEN** a max duration is configured or passed via CLI
- **WHEN** the session exceeds that duration
- **THEN** the session ends cleanly

### Requirement: Context retention limits

The system SHALL apply context retention limits (tool result truncation, transcript turn caps) so long sessions do not unbounded-grow context.

#### Scenario: Large tool output retained limited

- **WHEN** a tool returns a large payload
- **THEN** retained context is truncated per configured max tool result characters
