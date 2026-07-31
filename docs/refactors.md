# Deferred refactors

Four structural items came out of the multi-agent review (2026-07-29). They are
real, but they are refactors rather than defects: the current code works, and
each one needs a deliberate restructuring pass rather than a targeted patch.
Everything else from that review is fixed and covered by tests.

## Status — all complete

| | Item | Branch |
|---|---|---|
| R1 | Collapse the four copies of session teardown | `refactor/session-runner` |
| R3 | Stop the serial tool loop from starving audio drain | `refactor/session-runner` |
| R4 | Own the event queue instead of `asyncio.Queue` internals | `refactor/review-followups` |
| R2 | One declarative settings table | `refactor/review-followups` |
| — | Smaller cleanups | `refactor/review-followups` |

R1 and R3 landed together because both restructure `run_session_once` and R3
rewrites the loop that R1 splits out.

One item was resolved by removal rather than implementation:
`session.reasoning_effort` was dropped from the settings UI instead of being
wired into the Realtime payload. The GA session object has no field for it, and
an unrecognized key in `session.update` risks the server rejecting the update
and breaking every session. The config field remains, marked reserved.

The sections below are kept as the record of what each change was for.

**Ground rules for all four**

- The suite must stay green (`uv run pytest -q`, currently 452 passing) and
  `uv run ruff check src tests` clean. Coverage gate is 80%.
- These are behaviour-preserving. If a change alters observable behaviour, that
  is a bug in the refactor, not an improvement — except where explicitly noted
  under R3.
- The invariant that must never regress: **idle means no open cloud audio
  socket.** `CloudAudioGateway` accounting and `assert_idle_has_no_cloud` exist
  to enforce it. Any teardown path you touch must keep open/close balanced.

---

## R1 — Collapse the four copies of session teardown — DONE

**Where:** `src/aegis/session/runner.py`, `run_session_once` (~360 lines).

**Problem.** The cleanup sequence exists in four places: three connect-phase
`except` blocks (`TimeoutError` at ~181, `Exception` at ~198, `BaseException` at
~212) and the main `finally` (~352). Each does some subset of: stop the audio
graph if we own it, close the MCP bridge, end the voice session, set presence
idle. The copies have already drifted — the connect-phase paths write no
`session_end` audit record and skip the gateway idle assertion.

Adding a resource means editing four blocks in the right order, and missing one
leaks exactly what the design exists to prevent: a billable cloud socket, MCP
child processes, or audio streams.

**Approach.** Acquire each resource through an `AsyncExitStack` in order (MCP
bridge, audio graph, voice session, signal handlers) so every exit path unwinds
identically. Split the connect phase and the event loop into separate functions
— `_connect_session(...)` returning the live session, and `_run_event_loop(...)`
— so `run_session_once` becomes composition rather than one long body.

**Watch for.** The graph is only stopped when `owns_graph` is true (the daemon
passes its own and keeps it). The `BaseException` arm is deliberate: cancellation
during connect must still balance gateway accounting. `teardown_cancelled`
re-raises `CancelledError` after cleanup — preserve that.

**Done when.** One teardown path, all four current arms produce the same
observable result they do now (plus the audit record the connect-phase paths
currently skip), and `test_runner_more.py` / `test_review_session_scenarios.py`
still pass.

---

## R2 — One declarative settings table instead of four parallel lists

**Where:** `src/aegis/config/save.py` (`apply_llm_settings`, ~35 keyword-only
params), `src/aegis/ui/settings_server.py` (`_settings_dict` for GET, and the
POST body→kwarg mapping), `src/aegis/ui/settings_page.html` (`formPayload`).

**Problem.** The set of exposed settings is hand-written four times. A field
added in three of the four places fails silently — the value is dropped on save
or never displayed. The file's own history records this class of bug: an earlier
writer "silently dropped tables", wiping MCP servers on save.

**Approach.** One table mapping setting name → dotted config path (+ type/
coercion), consumed by all four. `apply_llm_settings(cfg, **35 kwargs)` becomes
`apply_settings(cfg, updates: dict)`; the GET payload is generated from the same
table; the page gets the field list as JSON rather than hardcoding it.

**Watch for.** `apply_llm_settings` is public API used outside the settings
server — keep a thin wrapper or update callers. Round-tripping must preserve
nested tables and arrays-of-tables (`tools.shell.rules`, `mcp.local.servers`);
`config_to_toml` handles this today via `model_dump`, don't regress it.
`test_settings_and_env.py` and `test_settings_body_cap.py` cover the surface.

**Done when.** Adding a settings field means editing one table, and a test
asserts the GET payload keys and the accepted POST keys are derived from the
same source.

---

## R3 — Stop the serial tool loop from starving audio drain — DONE

**Where:** `src/aegis/session/runner.py:~327` (`await handle_tool_call(...)`
inline in the event-consumption loop) and `src/aegis/voice/realtime.py`
(`_put_event` / `_drop_oldest_audio`).

**Problem.** While a tool runs (default 30s timeout) or an approval waits, the
runner is not draining `session.events()`. Realtime keeps producing audio and
transcript deltas; the 256-slot queue fills and starts evicting `AGENT_AUDIO`.
Speech the agent already produced is discarded, and what survives plays in a
burst after the tool finishes. Usage/cost accounting also stalls, so the cost cap
is blind for the duration of the tool.

**This is the one item that intentionally changes behaviour** — that is the
point. Agent audio should keep playing while a tool runs.

**Approach.** A dedicated consumer task that always drains events and routes
`AGENT_AUDIO` straight to `graph.play_session_audio`, with tool calls dispatched
separately. Keep at most one tool call in flight: the Realtime protocol expects
`function_call_output` in order, and the approval state machine assumes one
pending approval. So this is "don't block the drain", not "run tools
concurrently".

**Watch for.** `handle_tool_call` drives the session machine
(`TOOL_NEEDS_APPROVAL` / `APPROVAL_ALLOW` / `APPROVAL_DENY`) and those triggers
raise `InvalidTransition` from the wrong state — moving dispatch off the main
loop makes state races newly reachable. `mute_uplink` during approval must still
hold (`test_review_session_scenarios.py` covers it). Idle-timeout bookkeeping
(`last_activity`) is updated across several event branches; it must not expire
mid-tool.

**Done when.** A test drives a session with a slow tool while the mock emits
agent audio, and asserts no `AGENT_AUDIO` events were dropped and playback was
not deferred to after the tool returned.

---

## R4 — Own the event queue instead of mutating `asyncio.Queue` internals

**Where:** `src/aegis/voice/realtime.py`, `_drop_oldest_audio` (~235).

**Problem.** Eviction reaches into `self._events._queue` — a private CPython
deque — to drop the oldest audio event while preserving control events. A Queue
implementation change breaks eviction at exactly the moment it matters (under
pressure), and type checkers cannot see the access.

**Approach.** Either two queues (control + audio, with the consumer preferring
control), or a small explicit `deque`-backed structure owned by the session with
an `asyncio.Event` for readiness. Two queues is simpler and makes the priority
rule explicit rather than emergent.

**Watch for.** The current backpressure contract is tested and worth preserving:
`AGENT_AUDIO` is evicted oldest-first, `TOOL_CALL` and `ERROR` are never dropped,
and the terminal `None` sentinel must always get through
(`_put_event_nowait` warns when it cannot). See
`test_realtime_adapter.py::test_realtime_backpressure_preserves_queued_tool_calls`
and `test_coverage_boost.py::test_realtime_put_event_drops_audio_when_full`.

**Done when.** No `_queue` access anywhere, and the existing backpressure tests
pass unmodified.

---

## Smaller cleanups (optional, low risk)

- `ChatLLMSession.send_audio` / `send_tool_result` silently no-op when not
  connected, while Realtime and Mock raise `RuntimeError`. Document the contract
  on the `VoiceSession` protocol and make the implementations agree.
  (`src/aegis/llm/chat_session.py:~100`)
- `VoiceEventType.USAGE` means "delta" from `response.done` but "cumulative
  total" from `RealtimeVoiceSession.end()`, and `SessionMetrics.add_usage` merges
  every one it sees. Only the current runner's early loop exit keeps this from
  double-counting. Define USAGE as delta-only and stop re-emitting the total.
- `TEXT_ONLY_BACKENDS` (runner) and the dispatch set in `create_voice_session`
  (voice/factory.py) are the same nine strings in two places. Attach
  `voice_capable` to the provider definition and derive both.
- `daemon._wake_process_frame` is annotated `tuple[object | None, object | None]`
  but returns `WakeEvent | None`; the caller reads `event.score` through
  `object`. Annotate it properly — this is the wake-engine/daemon contract seam.
- `ToolRegistry.dispatch` hardcodes a `run_command` argv shape check by tool
  name. Move it behind a `ToolSpec.validate_args` hook so the registry stays
  tool-agnostic. (`src/aegis/tools/registry.py:~113`)
- `session.reasoning_effort` is validated, persisted, per-profile and editable in
  the UI, but never sent in the Realtime `session.update` payload. Either wire it
  up or drop it from the UI; it is currently marked reserved in the schema.
