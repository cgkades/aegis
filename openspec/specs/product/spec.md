# Product Identity

## Purpose

Define product naming, platform scope, and trust model so packaging, UX, and security assumptions stay consistent.

## Requirements

### Requirement: Product name is Aegis

The product SHALL be named **Aegis**. Packaging, CLI binary, config paths, and user-facing strings MUST use Aegis (not “Jarvis” or other historical candidates).

#### Scenario: CLI identity

- **WHEN** a user runs `aegis --help` or `aegis version`
- **THEN** help and version text identify the product as Aegis

#### Scenario: Config and data paths

- **WHEN** the product stores user configuration or state
- **THEN** it uses Aegis-scoped XDG paths under `aegis` (e.g. `~/.config/aegis/`)

### Requirement: Default wake phrase

The default wake phrase SHALL be **“Hey Aegis”** (engine-specific encoding of that phrase is configurable).

#### Scenario: Default configuration

- **WHEN** config is initialized without a custom wake phrase
- **THEN** the configured default phrase targets “Hey Aegis” / `hey_aegis` style identifiers

### Requirement: Linux desktop MVP scope

The MVP platform SHALL be Linux desktop. First-class Windows/macOS support is out of scope for current specs.

#### Scenario: Documented platform

- **WHEN** a user reads product scope
- **THEN** Linux desktop is stated as the supported MVP platform

### Requirement: Single-user trust model

The system SHALL assume a single-user desktop under the owner’s login session. Multi-user ACLs, remote multi-tenant control planes, and shared daemons across users are out of scope until redesigned.

#### Scenario: File and socket permissions

- **WHEN** the daemon creates IPC sockets or sensitive files
- **THEN** they are restricted to the owning user (e.g. `0600` / `0700` style permissions)
