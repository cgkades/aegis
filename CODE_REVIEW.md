# Code Review Report

## Verdict

Request changes

## Executive summary

The reviewed change set adds daemon-mediated approvals, stronger tool boundaries,
realtime backpressure limits, and deployment/config updates. It contained two
blockers: relative git paths could escape the configured workspace, and daemon
session reloads discarded `--config` / `--profile` overrides. The remaining
findings cover reload ownership, realtime resource limits, approval IPC parsing,
profile persistence, XDG defaults, installer portability, migration, and
subprocess cleanup.

## Findings

### F001 — Relative git paths escape the configured workspace · BLOCKER

`src/aegis/tools/builtin/git_tools.py:16` validated relative paths below
`tools.working_directory` but passed the unmodified relative string to git,
which resolves `cwd` relative to the daemon process. Resolve once with
`resolve_tool_path()` and use that absolute path for both validation and git.

### F002 — Session reload discards CLI config/profile overrides · BLOCKER

`src/aegis/daemon.py:204` reloaded only default paths, so a daemon launched with
`--config` or `--profile` used different policy for its sessions. Preserve and
reuse the invocation's config source and profile override.

### F003 — Reload orphans in-flight approval requests · CONCERN

`src/aegis/daemon.py:209` replaced the approval broker even though the session
held the old broker's bound request method. Keep the broker stable for active
sessions, or defer broker replacement until no approvals remain.

### F004 — Realtime queue can lose tool/control events · CONCERN

`src/aegis/voice/realtime.py:209` removed the FIFO head on full-queue control
events, despite promising to preserve control events. Preserve a control reserve
or selectively evict only audio events; add saturation coverage and drop metrics.

### F005 — Function-argument buffering can exhaust CPU and memory · CONCERN

`src/aegis/voice/realtime.py:328` rescanned all previous chunks for every delta
and only capped each call ID independently. Track per-call bytes incrementally,
enforce total/concurrent budgets, and clear unfinished buffers at boundaries.

### F006 — Reloaded wake settings do not match live wake resources · CONCERN

`src/aegis/daemon.py:204` updated reported wake configuration but did not rebuild
or stop the wake engine/audio path. Apply the changes atomically while idle, or
make wake/audio settings restart-required and report runtime state truthfully.

### F007 — Approval IPC accepts unsafe/mismatched input · CONCERN

`src/aegis/daemon.py:364` did not implement the documented response wire schema
and interpreted non-empty strings such as `"false"` as approval. Use a typed,
compatible request parser and require JSON booleans.

### F008 — Settings saves overwrite profile-specific user overrides · CONCERN

`src/aegis/config/save.py:94` reapplied a selected profile overlay even when it
was unchanged, re-enabling tools a user had disabled. Do not reapply unchanged
profiles; construct an actual profile switch from a baseline plus explicit
overrides.

### F009 — Default workspace is inconsistent with XDG paths · CONCERN

`src/aegis/config/schema.py:389` hard-coded a home-relative workspace while
`AegisPaths` creates an XDG-derived workspace. Derive one default from
`AegisPaths` and provision it from every session entrypoint.

### F010 — Installer may write an unusable service executable path · CONCERN

`scripts/install-user-service.sh:45` ignored uv's XDG tool-bin location and
could write an `ExecStart` path that does not exist. Resolve and verify
`$(uv tool dir --bin)/aegis` before writing the unit.

### F011 — Persisted approval-grant configuration no longer loads · CONCERN

`src/aegis/config/schema.py:85` removed existing serialized enum values without
migration. Retain legacy parsing and migrate it to a safe supported scope.

### F012 — Cancellation cleanup can hang after verbose subprocess output · CONCERN

`src/aegis/tools/builtin/git_tools.py:50` and equivalent helpers kill a process
then wait with undrained pipes. Centralize bounded termination cleanup that kills,
drains output, and never leaves the serial session stuck.

## Verification at review time

- `uv run pytest`: 395 passed, 84.5% coverage, with one unawaited-coroutine
  warning in a mocked timeout test.
- `uv run ruff check src tests`: passed.
- `git diff --check`: passed.
