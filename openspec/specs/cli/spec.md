# CLI

## Purpose

Define the `aegis` command surface for operators and dogfood workflows.

## Requirements

### Requirement: Core command groups

The CLI SHALL provide at least: `session`, `daemon`, `config`, `doctor`, `settings`, `auth`, `status`, `activation`, and `version`.

#### Scenario: Help lists groups

- **WHEN** the user runs `aegis --help`
- **THEN** the listed command groups are discoverable

### Requirement: Session once dogfood

The CLI SHALL support `session once` with a `--backend` choice covering implemented providers and optional max duration.

#### Scenario: Backend choices include cloud and local providers

- **WHEN** inspecting session once options
- **THEN** backends include realtime, openai_api, chatgpt_oauth, azure_openai, bedrock, litellm, ollama, and mock

### Requirement: Doctor diagnostics

The CLI SHALL provide `doctor` to report readiness (python, config, key presence, tool set, audio optional deps, idle cloud-audio assertion).

#### Scenario: Doctor without API key

- **WHEN** OPENAI_API_KEY is missing
- **THEN** doctor still runs
- **AND** reports api key as missing rather than crashing

### Requirement: Settings command

The CLI SHALL launch a local settings web UI bound to localhost by default.

#### Scenario: settings binds loopback

- **WHEN** `aegis settings` starts
- **THEN** the server listens on 127.0.0.1 by default
- **AND** prints the local URL

### Requirement: Auth commands for ChatGPT OAuth

The CLI SHALL support `auth login`, `auth status`, and `auth logout` for OAuth token lifecycle.

#### Scenario: auth status when signed out

- **WHEN** no token is stored
- **THEN** `auth status` reports signed_in: no
