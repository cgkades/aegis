# Tools & Policy

## Purpose

Define local tool registration, risk classes, argv policy, approvals, and profile-gated capability sets.

## Requirements

### Requirement: Structured tools preferred over shell

The system SHALL prefer structured tools (filesystem, git, process, write, kubectl) over generic shell. Shell, when enabled, remains argv-policy constrained.

#### Scenario: mvp tools without shell

- **GIVEN** profile mvp
- **WHEN** listing available tools
- **THEN** filesystem tools are present
- **AND** shell tool is not enabled

### Requirement: Filesystem tools

The system SHALL provide at least `list_dir`, `read_file`, and `search_files` with path sandboxing and secrets-path policy.

#### Scenario: list_dir within allowed roots

- **WHEN** list_dir is called on an allowed path
- **THEN** directory entries are returned subject to policy

### Requirement: Git and process tools (standard+)

When the profile enables them, the system SHALL provide structured git helpers and process listing tools rather than unrestricted shell equivalents.

#### Scenario: standard profile registration

- **GIVEN** profile standard with defaults
- **WHEN** tools are built
- **THEN** git/process tools are available according to profile expansion

### Requirement: Write tools require approval defaults

Write/patch tools MUST default to prompt (or stricter) approval rather than silent auto-apply for non-read risk classes.

#### Scenario: write file request

- **WHEN** a write tool is invoked under default approval policy
- **THEN** the action is prompted or denied rather than auto-applied without policy allowing auto

### Requirement: Hard-deny operations remain blocked

The policy SHALL distinguish tools that are eligible for user approval from
operations that are categorically blocked by the active profile or safety policy.
Hard-denied operations MUST NOT become executable through a one-time, session, or
voice approval. Their result MUST clearly tell the user that the operation was
blocked and identify the relevant safe alternative or configuration path where one
exists.

#### Scenario: Categorically unsafe command

- **WHEN** an invocation matches a hard-deny rule, such as an unbounded recursive deletion
- **THEN** the executor does not run it even if approval was requested
- **AND** the model and user receive an actionable blocked explanation

### Requirement: Argv policy engine

Shell-like execution MUST validate executable path, verbs/flags matrices, risk class, and decision (auto/prompt/deny).

#### Scenario: denied flag

- **GIVEN** a rule that denies a flag
- **WHEN** run_command includes that flag
- **THEN** policy returns deny

### Requirement: Structured kubectl only

kubectl access, when enabled, MUST go through a structured tool with verb/namespace/context constraints. Shell MUST deny kubectl.

#### Scenario: oncall kubectl enabled

- **GIVEN** kubectl structured tool enabled
- **WHEN** an allowed read verb is requested
- **THEN** the structured tool may execute under policy
- **AND** shell path still denies kubectl binary

### Requirement: Tool executor isolation basics

Tool execution SHALL use timeouts, output caps, and process-group kill semantics for child processes (no hanging orphans as the normal case).

#### Scenario: timeout

- **WHEN** a tool exceeds its timeout
- **THEN** the executor terminates the process group
- **AND** returns an error result to the session

### Requirement: Approval modes and grants

The system SHALL support approval defaults such as auto-readonly, prompt-all, and
deny-all. Read-only tools MAY auto-allow under `auto_readonly`, except where a
more restrictive rule (for example, a secrets path) applies. Write, execute, and
destructive operations MUST require a one-time visual approval or be denied by
policy. Any session grant MUST be limited to an identical, non-sensitive read-only
request; it MUST NOT broaden access to writes, deletes, commands, or secret data.

#### Scenario: auto readonly

- **GIVEN** approval default auto-readonly
- **WHEN** a read-class tool runs
- **THEN** it may auto-allow
- **AND** write/destroy class tools still require prompt or deny per policy

### Requirement: Desktop-first approval

Daemon sessions SHALL use a small, persistent local Aegis approval dialog rather
than depending on daemon stdin or notification-only actions. Pending approval MUST
mute mic uplink, show the tool, bounded arguments, risk, and allow/deny action,
and deny on timeout or unavailable UI. Foreground CLI sessions MAY use a terminal
prompt. Voice approval MAY be offered only as an explicit, disabled-by-default
setting and MUST NOT replace the visual approval record by default.

#### Scenario: Daemon tool approval

- **GIVEN** a daemon session requests an approval-eligible write or command
- **WHEN** the policy returns `prompt`
- **THEN** the user receives a persistent local Aegis approval dialog
- **AND** no tool runs until an explicit approval is received
