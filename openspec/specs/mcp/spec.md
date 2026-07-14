# MCP Integration

## Purpose

Define local MCP stdio bridging and remote MCP server_url injection behavior.

## Requirements

### Requirement: Local MCP stdio bridge

The system SHALL be able to spawn configured local MCP servers over stdio, discover tools, and register them as local function tools subject to approval policy.

#### Scenario: Configured local server

- **GIVEN** `mcp.local.servers` lists a valid stdio server
- **WHEN** the bridge initializes
- **THEN** discovered tools are registered into the tool registry under controlled names

### Requirement: Remote MCP via session injection

Remote HTTPS MCP servers MAY be configured for injection into Realtime sessions (server_url / connector style) without giving remote servers raw local shell.

#### Scenario: Private URL guard

- **GIVEN** a remote MCP server_url that looks private
- **WHEN** config is validated without allow_private_server_url
- **THEN** validation fails unless explicitly allowed

### Requirement: MCP tools still approval-gated

Tools introduced via local MCP MUST still pass through approval/risk handling rather than auto-executing destructive actions silently.

#### Scenario: MCP tool invocation

- **WHEN** an MCP-backed function tool is called
- **THEN** the call is mediated by the local executor/approval path
