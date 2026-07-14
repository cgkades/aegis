# Delta for Daemon & IPC

## ADDED Requirements

### Requirement: Tray reflects session state

When a tray client is available, it MUST reflect at least idle, connecting, active, and approval-pending states via daemon IPC events or polling.

#### Scenario: Active session indicator

- **GIVEN** tray is running and a session becomes active
- **WHEN** state transitions to active
- **THEN** the tray indicator changes to an active representation within a short latency

### Requirement: Tray actions are IPC thin client

Tray actions (end session, mute, quit) MUST be issued over local IPC and MUST NOT embed alternate tool execution paths.

#### Scenario: End session from tray

- **WHEN** the user chooses End session in the tray
- **THEN** the daemon receives an IPC request and ends the session through the normal state machine
