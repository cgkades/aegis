# Configuration & Profiles

## Purpose

Define how Aegis loads, validates, and persists configuration, including privilege profiles and secret env loading.

## Requirements

### Requirement: TOML configuration

User configuration SHALL be represented as validated structured config (TOML on disk) under the XDG config directory, with sensible defaults when the file is missing.

#### Scenario: Missing config is allowed for dogfood

- **WHEN** `config.toml` is missing and a command runs with missing-ok semantics
- **THEN** defaults apply (including profile `mvp`)
- **AND** the process does not require a pre-existing config file for basic mock/doctor flows

#### Scenario: Config init writes starter file

- **WHEN** the user runs config initialization
- **THEN** a starter config is written to the config path

### Requirement: Privilege profiles

The system SHALL support at least three profiles: `mvp`, `standard`, and `oncall`, which expand into concrete tool and policy defaults.

#### Scenario: mvp defaults

- **GIVEN** profile `mvp`
- **WHEN** tools are registered
- **THEN** filesystem read tools are available
- **AND** shell is disabled
- **AND** kubectl is disabled

#### Scenario: standard expands local agent tools

- **GIVEN** profile `standard`
- **WHEN** tools are registered
- **THEN** git/process/write class tools may be enabled per profile expansion
- **AND** shell remains off unless explicitly enabled

#### Scenario: oncall enables structured kubectl path

- **GIVEN** profile `oncall`
- **WHEN** kubectl tools are enabled in config
- **THEN** structured kubectl tools are available
- **AND** shell still hard-denies kubectl binary

### Requirement: CLI profile override

The CLI SHALL allow overriding the profile for a single invocation without permanently rewriting config.

#### Scenario: --profile flag

- **WHEN** the user runs `aegis --profile oncall <command>`
- **THEN** that invocation uses oncall defaults
- **AND** on-disk config is not necessarily rewritten

### Requirement: Dotenv secret loading

The system SHALL load non-empty keys from project `.env`, user config `.env`, and `secrets.env` without committing those files. Empty placeholder values MUST NOT clobber already-set environment variables.

#### Scenario: Load order respects existing env

- **GIVEN** `OPENAI_API_KEY` is already set in the process environment
- **WHEN** dotenv files are loaded without override
- **THEN** the existing process value remains

#### Scenario: Empty placeholders ignored

- **GIVEN** a dotenv file contains `OPENAI_API_KEY=`
- **WHEN** env is loaded
- **THEN** the empty value does not wipe a previously set key

### Requirement: Settings persistence

The settings UI and save helpers SHALL persist LLM-related settings into user `config.toml` with restricted file permissions when possible.

#### Scenario: Save from settings page

- **WHEN** the user saves provider settings via the settings API
- **THEN** config is written to the user config path
- **AND** nested `llm.*` provider fields are included in the serialized TOML
