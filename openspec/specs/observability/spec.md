# Cost, Audit & Observability

## Purpose

Define usage metering, cost caps, audit logging, and diagnostic expectations.

## Requirements

### Requirement: Usage metering

Sessions SHALL collect usage snapshots (tokens/audio usage when provided by the backend) and estimate or record cost where applicable.

#### Scenario: Realtime usage event

- **WHEN** a Realtime session reports usage
- **THEN** metrics capture input/output usage fields for session summary

### Requirement: Session cost cap

The system SHALL end or refuse to continue a session when configured max session cost is exceeded.

#### Scenario: Cost limit reached

- **GIVEN** `max_session_cost_usd` is configured low
- **WHEN** estimated cost exceeds the cap
- **THEN** the session ends with a clear reason

### Requirement: Audit JSONL

Tool invocations and session security-relevant events SHALL be append-logged as JSON lines under the user data/audit location, with secret redaction.

#### Scenario: Tool call audited

- **WHEN** a tool executes
- **THEN** an audit record is written including tool name and decision metadata
- **AND** raw secrets are not stored in full

### Requirement: Doctor readiness

`aegis doctor` SHALL report a concise readiness summary for config, provider/key presence, tools, optional audio deps, and idle cloud-audio safety.

#### Scenario: Doctor exit success on healthy-enough env

- **WHEN** python and package install are healthy
- **THEN** doctor returns actionable rows even if optional components (audio, wake models, API keys) are missing
