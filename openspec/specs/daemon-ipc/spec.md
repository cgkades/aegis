# Daemon & IPC

## Purpose

Define the always-on daemon process model and local unix-socket control plane.

## Requirements

### Requirement: Always-on daemon mode

The product SHALL provide a long-lived daemon mode that can perform local wake listening and accept activation over IPC.

#### Scenario: Foreground daemon

- **WHEN** the user runs `aegis daemon` (foreground)
- **THEN** the process stays up serving wake/IPC until stopped

### Requirement: Unix socket IPC

Control operations (status, session start, shutdown) SHALL use a user-local unix socket with restricted permissions.

#### Scenario: status command

- **WHEN** a daemon is running and the user runs `aegis status`
- **THEN** status is retrieved via the socket
- **AND** a missing daemon is reported clearly when the socket is absent

### Requirement: systemd user unit support

The product SHALL provide a systemd user unit sketch/install path so users can autostart the daemon under their session.

#### Scenario: install script present

- **WHEN** a user follows install documentation
- **THEN** a user service unit can be installed and started with systemd --user

### Requirement: Single-user ownership

Daemon IPC endpoints MUST be owned by the invoking user and not expose a network-facing control plane by default.

#### Scenario: No default TCP control port for daemon control

- **WHEN** using default configuration
- **THEN** daemon control is via local unix socket, not a public TCP bind
