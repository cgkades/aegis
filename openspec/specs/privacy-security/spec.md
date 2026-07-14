# Privacy & Security

## Purpose

Enforce local-first privacy for idle listening, on-device private tools, argv-only execution, and secret handling.

## Requirements

### Requirement: No cloud mic while idle

The system MUST NOT stream microphone audio to any cloud endpoint while idle (waiting for wake). Only local wake-word processing may consume idle mic audio.

#### Scenario: Idle daemon

- **GIVEN** the daemon is running with no active session
- **WHEN** the microphone is open for wake detection
- **THEN** no cloud audio WebSocket or equivalent uplink is open
- **AND** idle readiness checks report that cloud audio is closed

#### Scenario: Session teardown restores idle privacy

- **GIVEN** an active cloud voice session
- **WHEN** the session ends
- **THEN** cloud audio paths are closed
- **AND** the system returns to local-wake-only behavior

### Requirement: Cloud audio sole gateway

Cloud audio sessions MUST open only through a designated cloud-audio gateway path so idle assertions and session lifecycle remain enforceable.

#### Scenario: Voice backends open audio only via gateway

- **WHEN** a Realtime (or future duplex) session connects
- **THEN** cloud audio connectivity is tracked through the gateway
- **AND** idle status can observe open/closed cloud audio count

### Requirement: Private tools execute on-device

Private system access (filesystem, git, process inspection, shell under policy, kubectl structured tools) MUST execute as client-side function tools on the local host. The model proposes tool calls; the local runtime executes them.

#### Scenario: Tool call execution locus

- **WHEN** the model emits a function/tool call for a private tool
- **THEN** the local tool executor runs the operation
- **AND** only the tool result text is returned to the model session

### Requirement: No shell=True execution

Command execution MUST use argv lists only. The system MUST NOT invoke a shell with a concatenated command string (`shell=True` or equivalent).

#### Scenario: Shell tool argv-only

- **WHEN** a shell/run_command tool is enabled and invoked
- **THEN** the executor passes an argv array to the process API
- **AND** does not pass a single shell string for interpretation

### Requirement: Reserved binaries denied via shell

High-risk binaries including at least kubectl, oc, helm, sudo, and ssh MUST be deny-listed for the generic shell path so they cannot bypass structured-tool ownership.

#### Scenario: kubectl via shell denied

- **GIVEN** shell tools are enabled
- **WHEN** a tool attempts to run `kubectl` via the shell path
- **THEN** the policy engine denies the call
- **AND** structured kubectl (when enabled) remains the intended path

### Requirement: Secrets path protection

Paths matching configured secrets globs MUST NOT auto-execute for `read_file` or shell tools without explicit user approval or deny, per policy.

#### Scenario: Reading a secret path

- **GIVEN** a path matching secrets globs (e.g. under `~/.ssh` or similar configured patterns)
- **WHEN** `read_file` or shell is asked to read that path
- **THEN** the decision is prompt or deny (never silent auto-allow)

### Requirement: Secrets not committed or logged in full

API keys and OAuth tokens MUST be loaded from environment / dotenv / secrets files that are not part of the product source of truth. Audit and UI surfaces MUST redact secret values.

#### Scenario: Settings UI env display

- **WHEN** the settings UI shows environment key status
- **THEN** full secret values are not displayed
- **AND** presence is shown as set/missing with masking

#### Scenario: Credential file permissions

- **WHEN** ChatGPT OAuth tokens are saved to disk
- **THEN** the token file is written under the user config credentials path
- **AND** permissions are restricted (e.g. mode 600)
