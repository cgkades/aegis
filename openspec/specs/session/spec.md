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

### Requirement: Turn completion and idle-session timeout

The system SHALL distinguish the end of a user turn from teardown of an inactive
voice session. Realtime VAD (or release of push-to-talk) SHALL determine when the
user has finished a turn. A separately configurable `idle_timeout_s`, defaulting
to 45 seconds, SHALL end an active but quiet session and return it to local-only
idle behavior. The idle timer MUST pause while the user is speaking, the assistant
is speaking, a tool is running, or approval is pending.

#### Scenario: Quiet session after a response

- **GIVEN** the assistant has completed a response and no user turn begins
- **WHEN** `idle_timeout_s` elapses
- **THEN** the voice session ends cleanly
- **AND** the daemon returns to local wake / hotkey / push-to-talk readiness

#### Scenario: User begins another turn

- **GIVEN** an active session is waiting for the next request
- **WHEN** local VAD detects user speech or push-to-talk is held
- **THEN** the idle timer is reset or paused until that turn is complete

#### Scenario: Push-to-talk follow-up

- **GIVEN** the user released push-to-talk after a request and the assistant replied
- **WHEN** the user holds push-to-talk again before `idle_timeout_s` elapses
- **THEN** the existing session accepts the follow-up turn and retains its conversation context

### Requirement: Context retention limits

The system SHALL apply context retention limits (tool result truncation, transcript turn caps) so long sessions do not unbounded-grow context.

#### Scenario: Large tool output retained limited

- **WHEN** a tool returns a large payload
- **THEN** retained context is truncated per configured max tool result characters
