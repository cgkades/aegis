# Delta for Daemon & IPC

## ADDED Requirements

### Requirement: Real-device idle SLO measurement path

The product SHALL provide a practical way to sample idle CPU/RSS while the daemon is wake-listening (doctor or dedicated idle-profile mode) so efficiency SLOs can be checked on the user’s machine.

#### Scenario: Idle profile sampling

- **WHEN** the user runs an idle-profile diagnostic for a fixed duration
- **THEN** the tool reports process CPU and RSS samples
- **AND** asserts cloud audio remains closed during the sample window

### Requirement: Autostart install is one-command documented

Installing the user systemd unit SHALL be a documented one-script or one-command path that works for a normal user session.

#### Scenario: Fresh install

- **GIVEN** a user with systemd --user
- **WHEN** they run the documented install steps
- **THEN** the aegis user service can start and survive logout/login policies appropriate to user units
