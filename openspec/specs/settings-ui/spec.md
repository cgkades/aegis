# Settings UI

## Purpose

Define the local browser settings page for multi-provider LLM configuration, secrets entry, probes, and test chat.

## Requirements

### Requirement: Localhost-only settings server

The settings UI SHALL run as a local HTTP server defaulting to loopback (127.0.0.1) and MUST NOT require a public deployment.

#### Scenario: Default bind

- **WHEN** settings starts with defaults
- **THEN** it serves on 127.0.0.1:8765 (or configured local port)

### Requirement: Multi-provider panels

The UI SHALL expose configuration panels for OpenAI/Realtime, ChatGPT OAuth, Azure OpenAI/Foundry, AWS Bedrock, LiteLLM, and Ollama.

#### Scenario: Provider switch shows relevant panel

- **WHEN** the user selects `bedrock`
- **THEN** Bedrock fields (region, model id, profile) are shown
- **AND** unrelated provider panels are hidden

### Requirement: Save config and env secrets

The UI SHALL save non-secret provider settings to config.toml and allow writing selected secret keys to project `.env`.

#### Scenario: Save Azure endpoint

- **WHEN** the user saves an Azure endpoint and deployment
- **THEN** those values persist in config and reload into the form

#### Scenario: Save API key to .env

- **WHEN** the user pastes AZURE_OPENAI_API_KEY and saves
- **THEN** the key is written to the project `.env`
- **AND** subsequent status shows the key as set (masked)

### Requirement: Probe and test chat

The UI SHALL allow probing the selected provider and running a short test chat request.

#### Scenario: Test chat mock path

- **WHEN** provider is mock and test chat is invoked (or mock session test)
- **THEN** the UI reports success without cloud credentials

### Requirement: OAuth controls

The UI SHALL support ChatGPT OAuth sign-in, sign-out, and manual token paste fallback.

#### Scenario: Manual token paste

- **WHEN** the user pastes an access token and saves
- **THEN** OAuth status becomes signed in
- **AND** the token is stored in the credentials path
