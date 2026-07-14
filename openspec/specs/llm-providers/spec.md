# LLM & Voice Providers

## Purpose

Define supported session backends, auth, model selection, and the split between duplex voice and chat-only providers.

## Requirements

### Requirement: Multi-provider session backends

The system SHALL support selecting a session backend among at least: `realtime`, `openai_api`, `chatgpt_oauth`, `azure_openai`, `bedrock`, `litellm`, `ollama`, and `mock`.

#### Scenario: CLI backend selection

- **WHEN** the user runs `aegis session once --backend <provider>`
- **THEN** the selected provider's voice capability is checked without requiring a config rewrite
- **AND** an unavailable voice path is rejected with a clear explanation rather than opening a broken session
- **AND** unsupported names are rejected by CLI choice validation

#### Scenario: Config provider selection

- **WHEN** `session.provider` is set in config
- **THEN** daemon/session flows use that provider unless overridden
- **AND** a text-only provider is visibly identified as unavailable for voice until cascaded STT/TTS is configured

### Requirement: Text-only provider disclosure

The system SHALL make the voice capability of the selected provider visible before
the user attempts activation. Settings and each available status/presence surface
MUST show a clear warning for a text-only provider, including that cascaded
STT/TTS is required for spoken interaction.

#### Scenario: Text-only provider selected

- **GIVEN** `session.provider` is a chat-only provider without a configured cascade
- **WHEN** Settings or a status/presence surface is displayed
- **THEN** it shows that the provider is text-only and cannot currently serve voice activation
- **AND** an attempted voice activation explains the limitation and how to change provider or configure a cascade

### Requirement: Realtime duplex voice

The `realtime` provider SHALL open a full-duplex OpenAI Realtime voice session using an API key from the configured env var (default `OPENAI_API_KEY`).

#### Scenario: Missing API key for realtime

- **GIVEN** no API key is configured
- **WHEN** a realtime session is requested
- **THEN** the system fails with a clear error indicating the missing key

### Requirement: OpenAI chat API provider

The `openai_api` provider SHALL perform Chat Completions (or compatible) requests against the configured OpenAI chat base URL using an API key.

#### Scenario: Realtime model name not used for chat

- **GIVEN** `session.model` is a Realtime model id
- **WHEN** a non-realtime chat provider is selected
- **THEN** the client uses a provider-appropriate chat model default instead of sending the Realtime id

### Requirement: ChatGPT OAuth provider

The `chatgpt_oauth` provider SHALL authenticate with a stored OAuth access token (device-code login or manual paste) and call chat APIs with a bearer token. Tokens MUST live in a user credentials file, not in git-tracked env templates as committed secrets.

#### Scenario: Not signed in

- **GIVEN** no valid token file
- **WHEN** creating a chatgpt_oauth client
- **THEN** the system errors with guidance to run `aegis auth login` or paste a token in Settings

#### Scenario: Auth CLI lifecycle

- **WHEN** the user runs `aegis auth login` / `status` / `logout`
- **THEN** token state is created, reported, or cleared accordingly

### Requirement: Azure OpenAI / Foundry provider

The `azure_openai` provider SHALL call Azure OpenAI or Azure AI Foundry chat endpoints using configured endpoint, deployment/model, API version, API style, and auth mode.

#### Scenario: Classic deployments style

- **GIVEN** `api_style = deployments`
- **WHEN** a chat request is sent
- **THEN** the request targets `{endpoint}/openai/deployments/{deployment}/chat/completions` with `api-version` query
- **AND** authentication uses `api-key` header when `auth_mode = api_key`

#### Scenario: Foundry style

- **GIVEN** `api_style = foundry`
- **WHEN** a chat request is sent
- **THEN** the request targets the Foundry models chat completions path with api-version as configured

#### Scenario: Missing endpoint or key

- **WHEN** endpoint or API key is missing
- **THEN** client creation fails with an actionable error

### Requirement: AWS Bedrock provider

The `bedrock` provider SHALL call Amazon Bedrock Runtime **Converse** using AWS Signature Version 4. The implementation MUST work without a hard dependency on boto3.

#### Scenario: Env credentials

- **GIVEN** `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` are set
- **WHEN** a bedrock chat request is made
- **THEN** the request is SigV4-signed to the regional bedrock-runtime endpoint
- **AND** model ids containing `:` are path-encoded

#### Scenario: Shared credentials profile

- **GIVEN** env keys are unset but `llm.bedrock.profile` or `AWS_PROFILE` points at a valid `~/.aws/credentials` profile
- **WHEN** resolving credentials
- **THEN** the profile keys are used

#### Scenario: Missing credentials

- **WHEN** no env keys and no usable profile exist
- **THEN** client creation fails describing how to configure AWS credentials

### Requirement: LiteLLM proxy provider

The `litellm` provider SHALL call an OpenAI-compatible base URL (default local proxy) with optional API key.

#### Scenario: Custom base URL

- **WHEN** `llm.litellm.base_url` is configured
- **THEN** chat requests use that base URL’s `/chat/completions` path

### Requirement: Ollama local provider

The `ollama` provider SHALL call a local Ollama OpenAI-compatible endpoint and list models from Ollama’s native tags API when probing.

#### Scenario: Default local endpoints

- **WHEN** defaults are used
- **THEN** chat uses `http://127.0.0.1:11434/v1` and model listing uses `http://127.0.0.1:11434`

#### Scenario: Ollama unreachable probe

- **WHEN** Ollama is not running
- **THEN** probe reports failure/empty models without crashing the settings UI

### Requirement: Mock offline provider

The `mock` provider SHALL support offline dogfood sessions without network credentials.

#### Scenario: Mock session once

- **WHEN** `aegis session once --backend mock` runs
- **THEN** a session completes successfully without requiring cloud API keys

### Requirement: Provider catalog and probe

The system SHALL expose a provider catalog and health/probe results for the settings UI (models when available, ok/detail status).

#### Scenario: Probe mock always ok

- **WHEN** mock is probed
- **THEN** result is ok with model list containing mock

#### Scenario: Probe azure without endpoint

- **WHEN** azure is probed without endpoint configured
- **THEN** result is not ok and explains missing endpoint

### Requirement: Chat providers accept text turns

Non-Realtime chat providers SHALL implement the session protocol sufficiently for text inject and agent transcript events. Audio PCM MAY be accepted but ignored until cascaded STT is available.

#### Scenario: Chat session text turn

- **GIVEN** an ollama/azure/bedrock/etc chat session is connected
- **WHEN** user text is injected
- **THEN** a user transcript event is emitted
- **AND** an agent transcript event with model output is emitted
