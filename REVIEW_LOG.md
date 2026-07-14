# Aegis Code Review Log

Full multi-agent review of the Aegis always-on voice agent ("hey aegis" wake word).
Code is the source of truth; SPEC.md was written after the fact.

## Baseline (2026-07-13)

- 264 tests passing, 81.0% coverage (`--cov-fail-under=80` enforced)
- `ruff check` clean (E, F, I, UP, B)
- Python 3.12, src layout, ~9.5k source LOC / ~4.4k test LOC

## Review rounds

### Round 1 — five parallel expert reviews (in progress)

Reviewers: Python practices, software engineering/architecture, code quality/dead code,
efficiency, security (secrets + prompt injection).

Findings and fixes will be appended below as they land.

## Findings

### Security review (round 1)

| # | Sev | Location | Issue |
|---|-----|----------|-------|
| S1 | CRITICAL | ui/settings_server.py | No CSRF/Origin/Host validation; `_read_json` ignores Content-Type. Any web page can POST to 127.0.0.1: rewrite `openai_chat_base_url` to attacker server, then `/api/test-chat` sends real `Authorization: Bearer <key>` there. `/api/env-key` writes arbitrary env vars to .env. DNS-rebinding possible; `--host 0.0.0.0` exposes to LAN. |
| S2 | HIGH | voice/realtime.py:123, session/tool_loop.py:74, mcp/bridge.py:111, tools/executor.py:85 | Prompt injection: tool/MCP/shell output returned to model verbatim — no untrusted-content delimiters, no ANSI stripping, no size cap on some paths. Session grants (`grant_session`) make every later `run_command` auto-approved, so injected content can drive commands. |
| S3 | HIGH | util/secrets.py:10, audit/log.py:41, tools/registry.py:120 | Audit log writes raw `args_summary`/`result_summary` (500 chars) per tool call; regex redaction misses JWTs, AWS keys, high-entropy tokens, `write_file` content, secrets-file reads. |
| S4 | MED | systemd/aegis.service | No sandbox hardening (NoNewPrivileges, ProtectSystem, PrivateTmp, etc.). |
| S5 | MED | tools/builtin/git_tools.py:13-78 | `_git()` doesn't use `scrubbed_env` → git children inherit API keys (visible via /proc, git hooks/credential helpers). `path` arg not sandbox-checked → read files outside workdir via `git_diff`. |
| S6 | MED | tools/builtin/write_tools.py:26-33 | DENY only honored when `reason == "sandbox"`; other DENY reasons silently ignored. |
| S7 | LOW | scripts/yubikey-fix-access.sh:26-44 | udev rule broader than needed (plugdev group grant for all vendor-1050 nodes). |
| S8 | LOW | daemon.py:82-89 | Socket world-reachable between bind and chmod (mitigated by 0700 state dir); no IPC auth token. |
| S9 | LOW | mcp/bridge.py:118-127 | Text-content branch of MCP results unbounded (dict branch caps 100KB). |
| S10 | LOW | tools/policy.py:247-262 | Allowed executable dirs trusted implicitly; no ownership/writability check. |

Overall: argv-only exec, scrubbed env, sandboxing and adversarial tests are solid. Systemic gaps: settings server trusts loopback as its whole boundary; no untrusted-content handling on tool results.

### Efficiency review (round 1)

Frame math: 48kHz, 20ms blocks = 50 frames/sec, 24/7 in daemon.

| # | Sev | Location | Issue |
|---|-----|----------|-------|
| E-H1 | HIGH | audio/playback.py:123 (runner.py:168) | Blocking `queue.put()` (maxsize=32) called synchronously from event loop per AGENT_AUDIO delta; realtime delivers faster than 1x playback → queue fills → whole event loop stalls on multi-sentence replies. |
| E-H2 | HIGH | daemon.py:137, session/runner.py:271 | `asyncio.to_thread()` per 20ms frame, 50x/sec forever — Future+contextvar+threadpool roundtrip just to read one block. Dominant idle structural cost. |
| E-H3 | HIGH | daemon.py:76, pipeline.py:67, playback.py:57 | Daemon starts playback stream it never uses → output callback fires 50-190x/sec forever, exception-per-tick zero-fill, keeps output device active (blocks PipeWire idle suspend → power cost). |
| E-M1 | MED | session/runner.py:82 vs daemon.py:76 | Session creates 2nd AudioGraph while daemon's keeps running → 2 in+2 out streams; daemon queue hits `queue.Full` every frame during session; ~1.3s stale audio fed to wake on resume. |
| E-M2 | MED | audio/resampler.py:29,45-52 | Resample rebuilds float64 linspace grids + ~6 temporaries per frame; float32 convert before equal-rate early return. 48k→16k/24k are exact 3:1/2:1 → decimation ~10x cheaper. |
| E-M3 | MED | wake/openwakeword.py:85 | oWW.predict() called 50x/sec with 320-sample chunks; designed for 1280 (80ms) → feature pipeline runs 4x too often. Largest idle CPU after model. |
| E-M4 | MED | session/runner.py:153,253 | `wait_for(_next_event, 0.25)` makes task+timer per event; on 250ms gap cancels in-flight `__anext__`, finalizes the events() generator → later StopAsyncIteration treated as session end (latent correctness bug). |
| E-M5 | MED | voice/realtime.py:115 (runner.py:264) | One websocket message per 20ms mic frame = 50 sends/sec each with base64+json.dumps+syscall. Coalesce 3-5 frames. |
| E-M6 | MED | llm/chat_session.py:66-90 | `_history` unbounded, full list serialized+sent every turn → O(n²) tokens/payload; ContextManager pruning not used. |
| E-M7 | MED | wake/porcupine.py:83-88 | Per-frame `np.concatenate` + `frame.tolist()` boxes 512 int16→Python ints (~16k objects/sec). Use ring buffer + pass ndarray directly. |
| E-M8 | MED | daemon.py:134-136 | Wake loop busy-polls machine.state at 10Hz for whole session (~600 wakeups/min). Await session task/Event instead. |
| E-L1 | LOW | voice/realtime.py:55 | `_events` queue unbounded; keeps enqueuing audio deltas during 60s approval prompt → tens of MB PCM. |
| E-L2 | LOW | audio/vad.py:55-59 | Two temporaries per frame; use `np.dot`. |
| E-L3 | LOW | audit/log.py:71-79 | Reopen+chmod file per event; no retention sweep. |
| E-L4 | LOW | session/runner.py:164 | `import numpy` inside per-event hot branch. |
| E-L5 | LOW | session/context.py:27, voice/protocol.py:64 | List-copy pruning (use deque); empty `extra` dict per event. |

Overall: idle architecture mostly right (no cloud sockets idle, lazy imports, drop-oldest queue) but pays needless structural tax: H2/H3/M2/M3 are cheap local fixes cutting idle CPU + enabling device power-down. H1 is the one serious active-path bug (event-loop stall mid-response). M4 doubles as correctness bug.

### Software engineering review (round 1) — the first-run blockers

| # | Sev | Location | Issue |
|---|-----|----------|-------|
| SE-C1 | CRITICAL | runner.py:153,253 | `wait_for(_next_event, 0.25)` cancels `__anext__` → CancelledError closes the `events()` generator → next call raises StopAsyncIteration → runner breaks. Any 250ms gap kills a real Realtime session ~immediately. Mock auto-ends so tests miss it. (= E-M4) |
| SE-C2 | CRITICAL | runner.py:118-121 vs daemon.py:303 | `run_session_once` calls `add_signal_handler` on the shared loop, replacing daemon's handlers, never restores → after first wake session SIGTERM/SIGINT set a dead event; daemon unkillable except SIGKILL. |
| SE-C3 | CRITICAL | playback.py:73-79 | Output callback re-queues chunk remainder at TAIL of queue → out-of-order/garbled agent audio when queue holds >1 chunk (normal in bursts). Plus blocking `put` (=E-H1). |
| SE-H1 | HIGH | daemon.py:186-189 | Daemon hardcodes `backend="realtime"`, ignoring `session.provider`. User who set ollama still gets Realtime → fails on missing OPENAI_API_KEY. |
| SE-H2 | HIGH | runner.py:270 | Uplink loop `while state is ACTIVE` exits on APPROVAL_PENDING and never restarts → mic dead after first approval. (=CQ-6) |
| SE-H3 | HIGH | daemon.py:74 + runner.py:81 | Daemon graph (capture+playback) stays open while session opens a 2nd graph on same device → raw ALSA 2nd-open fails (silent text-only), else double mic streams. (=E-M1) |
| SE-H4 | HIGH | config/save.py:27-72 | `config_to_toml` omits `mcp` table and serializes list-of-dicts (shell rules) as `[]`. One `/api/settings` POST wipes MCP servers + shell rules. (=PY-4) |
| SE-H5 | HIGH | llm/aws_sigv4.py:44-48 | Canonical URI single-encoded; SigV4 for non-S3 requires double-encoding the path (`%3A`→`%253A`). Every Bedrock call → SignatureDoesNotMatch. |
| SE-H6 | HIGH | pyproject.toml + daemon.py:66-72 | No `openwakeword`/`pvporcupine` extra → default wake engine can't import on fresh install → silent fallback to MockWakeEngine(energy 8000) = any loud noise opens a billed Realtime session. Cost/privacy trap. |
| SE-H7 | HIGH | voice/realtime.py:79,93 | Beta-era `session.update` shape + undocumented default model `gpt-realtime-2.1-mini`; likely fails at handshake. Never run against real API. Needs live verification. |
| SE-M1 | MED | daemon.py:206 | Session task exception never retrieved (no done-callback/await). (=PY-3) |
| SE-M2 | MED | approval/modes.py:57-63 | `wait_for(to_thread(_ask))` can't cancel the stdin thread → leaks, eats next line; under systemd (stdin=null) every approval instantly denied. No non-TTY approval path. |
| SE-M3 | MED | audio/pipeline.py:67-69 | `AudioGraph.start()` not exception-safe: if playback.start() raises after capture.start(), mic leaks open for process lifetime. |
| SE-M4 | MED | mcp/bridge.py | `LocalMcpBridge` never started/registered in runtime → configured local MCP servers silently ignored. (=CQ-7 partial) |
| SE-M5 | MED | chat_session.py:61 + runner.py | Chat providers un-drivable: nothing calls `inject_user_text`, no stdin REPL, factory drops tools for chat → banner lies. (=CQ-9) |
| SE-M6 | MED | runner.py:183 | `reset_turn()` called per TOOL_CALL → `max_tool_calls_per_turn` never enforced. (=CQ-2) |
| SE-M7 | MED | runner.py | idle_timeout_s / goodbye / connect_timeout_s unimplemented → walk-away bills full max_duration_s (900s). |
| SE-M8 | MED | daemon.py:137 + oww.py:85 | oWW fed 320-sample chunks; expects ≥400 (rec 1280). (=E-M3) |
| SE-M9 | MED | git_tools.py:22, kubectl_tools.py:141 | Timeout orphans child process (executor kills group; these don't). (=PY-10) |
| SE-M10 | MED | daemon.py:281 + service | ConfigError → raw traceback → systemd Restart=on-failure every 2s crash-loop. |
| SE-M11 | MED | llm/client.py:203 | chatgpt_oauth sends OAuth bearer to api.openai.com/v1/chat/completions; public API rejects → 401. Endpoints speculative. |
| SE-M12 | MED | settings_server.py:249 + env.py:12 | `/api/env-key` writes keys to `$CWD/.env` via cwd fallback; should target `~/.config/aegis/secrets.env`. |
| SE-L | LOW | various | `--foreground` no-op; IPC error id hardcoded "1" (daemon.py:229); playback multichannel reshape wrong; doctor live-probes hang offline; socket chmod after accept. |

### Python practices review (round 1)

Highs: (1) three hand-rolled `contextlib_suppress` classes swallow BaseException incl. CancelledError — ipc.py:83, executor.py:121, stdio_client.py:153; (2) signal clobber = SE-C2; (3) session task exc unretrieved = SE-M1; (4) TOML serializer drops list-of-tables = SE-H4. Meds: JSON via f-string interpolation of exc text (invalid JSON on quotes) across registry/executor/fs/write/process/kubectl/approval; `_next_event` monkey-patches `_aegis_aiter` onto session; racy `sleep(0.05)` connect + unused `connect_timeout_s`; blocking playback put; MCP stdio pending futures never failed on close/EOF; subprocess timeout leaks child; dead scaffolding runner.py:180-189; useless wake factory try/except; porcupine `_buf` hasattr + reset no-op; untyped `=None` attrs. Lows: dead `hasattr(enum,'value')`; `argv: list|Any`; paths as str not Path; `assert` for runtime invariants; numpy import in hot loop; `_normalize_user_dict` no-op; dead HotkeyListener members; manual loop vs asyncio.Runner; shared mutable default shell rules; git add result discarded; do_GET lacks exception guard.

### Code quality / dead-code review (round 1)

Highs: dead scaffolding runner.py:180-189; max_tool_calls reset bug (=SE-M6); triple contextlib_suppress; ~20 config fields + 3 enums nothing reads (push_to_talk, uplink_queue_ms, duck_on_playback, idle_timeout_s, connect_timeout_s, reuse_grace_s, reasoning_effort, strip_old_audio_items, keyring_service, approval.default, voice_confirm_phrase, mute_uplink_during_approval, session_grant_applies_to, parallel_read_tools, git.allow_push, store_transcripts, store_audio, audio_debug_buffer, metrics_enabled/bind, app.name/data_dir); dead wake factory fallback; uplink dies after approval (=SE-H2). Meds: HotkeyListener + activate_via_socket unwired; realtime end() emits unreadable USAGE (double-count risk); chat providers un-drivable (=SE-M5); 10x copy-paste policy-gate boilerplate; f-string JSON errors; porcupine buffer; two divergent private-URL detectors (schema vs remote_spec); `_normalize_user_dict` no-op; unfired triggers + banner promises "goodbye"; dead `can()`/`estimated_cost_usd`; context summarization half-wired; dead `--foreground`; llm/registry dedupe dup; misleading `text_fallback_available:True`. Lows: stale comments (old repo name, PR breadcrumbs); redundant policy.py double-normalization + dead re-checks; kubectl redundant risk calc; HYBRID_TEXT_TOOLS half-declared; decorative Backend Literal; dead audio helpers (frames/frames_at, bytes_to_int16, dup default-device fns); gateway allowlist redundancy; magic numbers; unused sessions_dir/models_dir; env_status advertises unread vars (OPENAI_REALTIME_URL, AEGIS_PROFILE); yubikey scripts off-topic.

## Fixes applied

Working in priority order: (1) correctness/first-run blockers, (2) security, (3) dead code + quality, (4) tests. Each fix logged below with verification note.

### Batch 1 — critical first-run blockers (DONE, 264 tests green)

- **SE-C1** runner.py: replaced `wait_for(_next_event, 0.25)` (which cancelled `__anext__` and closed the events() generator on any 250ms gap) with a persistent `pending = ensure_future(events_iter.__anext__())` + `asyncio.wait({pending}, timeout=...)` that never cancels on timeout. Removed the `_aegis_aiter` monkey-patch helper. This was the bug that would have killed every real Realtime session.
- **SE-C2** runner.py: signal handlers now saved in `installed_signals` and removed in `finally`; daemon calls runner with `install_signal_handlers=False` so it keeps its own SIGINT/SIGTERM handling. Daemon is now killable after a wake session.
- **SE-C3 / E-H1** playback.py: output callback now drains an instance-level `_carry` buffer first and accumulates whole queued chunks in order (no more re-queueing the remainder at the tail → no scrambled audio); `write()` uses `put_nowait` with drop-oldest instead of a blocking `put` that stalled the event loop.
- **SE-H1** daemon.py: session now launches with `backend=self.cfg.session.provider.value` instead of hardcoded "realtime".
- **SE-H2** runner.py: `_uplink_loop` runs while state in {ACTIVE, APPROVAL_PENDING} (was ACTIVE only) so the mic survives approval prompts; frames still gated by `mute_uplink`.
- **SE-H3** runner.py/daemon.py/pipeline.py: `run_session_once(graph=...)` reuses the daemon's AudioGraph (no second device open); daemon runs `start(capture_only=True)` and starts/stops playback lazily around a session; `owns_graph` guards teardown. `AudioGraph.start` now rolls back capture if playback fails (SE-M3).
- **SE-M1** daemon.py: `_session_task.add_done_callback(_on_session_done)` logs + audits session-task exceptions instead of losing them to GC.
- **SE-M6** runner.py: removed per-TOOL_CALL `reset_turn()`; now resets on USER_TRANSCRIPT (turn boundary) so `max_tool_calls_per_turn` is actually enforced.
- **SE-M10** daemon.py: `run_daemon` catches `ConfigError`, prints diagnosis, returns exit 78 (EX_CONFIG) to break the systemd crash-loop.
- Dead code removed: runner.py:180-189 scaffolding no-ops; `_next_event`; numpy import hoisted out of the per-event branch (E-L4); `Backend` Literal alias.
- IPC error responses now echo the request id (was hardcoded "1").

### Batch 2 — high-value bugs (DONE, 264 tests green)

- **SE-H4** config/save.py: replaced the hand-rolled TOML writer with `tomli_w.dumps` (+ recursive None-strip). Verified round-trip preserves `tools.shell.rules` and `mcp.local.servers` (incl. env dict). Added `tomli-w>=1.0` dep. Saving from the settings page no longer wipes MCP servers / shell rules.
- **SE-H5** llm/aws_sigv4.py: canonical URI now `quote(parsed.path, safe="/")` — double-encodes the already-encoded model id (`%3A`→`%253A`) matching botocore, so Bedrock signatures validate.
- **SE-H6** daemon.py + pyproject.toml: removed the dangerous silent fallback to `MockWakeEngine(energy=8000)` (any loud noise → billed session); a failed wake engine now DISABLES wake with a clear log + stderr message pointing to `aegis session start`. Discovered openwakeword is unresolvable on py3.12 (pins tflite-runtime with no 3.12 wheels) — documented in pyproject NOTE instead of a broken extra; added a `porcupine` extra that does resolve.
- **SE-M9 / S5** git_tools.py + kubectl_tools.py: subprocess timeouts now `_kill_process_group` + `await proc.wait()` (was orphaning children). git now runs with `scrubbed_env` (no API keys inherited), `GIT_TERMINAL_PROMPT=0`, and `-c credential.helper=`.
- wake/factory.py: removed the dead try/except (couldn't fire — imports are in `start()`) and self-defeating re-construct; `allow_mock` now cleanly returns a mock.
- porcupine.py: `_buf` initialized in `__init__`; `reset()` actually clears it (was a no-op leaving stale audio across sessions).
- git_commit: `git add -A` failure now returned instead of silently proceeding to commit.
- kubectl: collapsed redundant double risk computation; f-string JSON errors → `json.dumps`.
- **SE-M10** (from batch 1) pairs with systemd StartLimit (batch 3).

### Batch 3 — security (DONE, 264 tests green)

- **S1 (CRITICAL)** ui/settings_server.py + settings_page.html: added per-process CSRF token (`secrets.token_urlsafe`) injected into the page and required on every POST via `X-Aegis-CSRF` header (forces CORS preflight); Host-header allowlist (blocks DNS-rebinding); `_read_json` now requires `Content-Type: application/json`; `run_settings_server` refuses non-loopback bind. do_GET endpoints wrapped in try/except. Test updated + negative CSRF/Host assertions added.
- **S2 (HIGH)** new tools/sanitize.py: `wrap_untrusted()` strips ANSI/control escapes, caps size, and wraps tool output in `<untrusted_tool_output>` delimiters, applied at the single chokepoint in tool_loop before results go to the model. System instructions (realtime + runner) now tell the model to treat wrapped content as data, never instructions. Delimiter-forging stripped.
- **S3 (HIGH)** registry.py + secrets.py: audit no longer logs raw arg/result bodies — `_summarize_args` logs keys + redacted argv/path only; result logs length. `redact_secrets` extended with JWT, AWS AKIA/ASIA, Slack, GitHub PAT, Google key patterns.
- **S4** systemd/aegis.service: added NoNewPrivileges, PrivateTmp, ProtectSystem=strict, kernel/cgroup/clock protections, RestrictAddressFamilies, SystemCallFilter=@system-service, MemoryDenyWriteExecute, ReadWritePaths allowlist, and StartLimit to break config crash-loops.
- **S5** git_tools.py: scrubbed env (done batch 2) + new `path_within_workdir` sandbox check on the `path` cwd arg (`_resolve_cwd`) — git tools can no longer read repos outside the workdir.
- **S6** write_tools.py: `handle_write_file` honors ANY PolicyDecision.DENY (was sandbox-only).
- **S9** mcp/bridge.py: all `_format_mcp_result` branches capped (text branch was unbounded).
- **S8** daemon.py: socket created under `umask(0o177)` so it's never group/other-reachable pre-chmod.
- **SE-M12** settings_server.py: `/api/env-key` writes to `~/.config/aegis/secrets.env`, not `$CWD/.env`.
- **SE-M4** runner.py: `LocalMcpBridge` now actually started (tools registered) and closed around the session — configured local MCP servers were silently ignored.
- Python/quality (overlap): removed all 3 hand-rolled `contextlib_suppress` classes (ipc/executor/stdio) → `contextlib.suppress`; MCP stdio `_fail_pending` fails in-flight requests on close/EOF (no 60s hangs); replaced asserts in stdio_client with RuntimeError; f-string JSON errors → `err_json()` helper across executor/fs/write/process/registry/kubectl/approval; extracted shared `gate()` policy helper collapsing ~10 copy-pasted approval blocks; `remove_stale_socket` redundant `is_socket()` check dropped.

### Batch 4 — Python idiom + code-quality cleanup (DONE, 264 tests green)

- Dead code removed: `_normalize_user_dict` no-op; `SessionMachine.can()`; dead `--foreground` flag; `HotkeyListener._thread`/`_stop`/`on_press`; unused `threading` import.
- Consolidated the two divergent private-URL detectors into `util/net.is_private_url` (robust hostname parsing); schema + remote_spec both use it.
- Typing: `daemon._wake: WakeEngine | None`, `chat_session._client: LLMClient | None`.
- Removed dead `hasattr(enum, "value")` defensiveness in voice/factory (provider is always StrEnum).
- `provider_status.text_fallback_available` → False (stub raises NotImplementedError).
- Stale comments fixed: env.py old repo name; daemon busy-state narrative rewritten to one clear sentence.
- (Remaining low-value items — dead audio helpers `frames`/`frames_at`, unused config fields/enums, magic-number constants — left for a follow-up; several are aspirational features, not bugs, and touching the config schema risks churn without first-run benefit.)

### Batch 5 — efficiency (DONE, 264 tests green)

- **E-M2** resampler.py: equal-rate check before float conversion; exact integer downsample ratios (48k→16k 3:1, 48k→24k 2:1) use plain decimation (~10× cheaper); `np.interp` grids cached via `lru_cache` and computed in float32 (was rebuilding two float64 linspaces per frame).
- **E-M3 / SE-M8** openwakeword.py: internal buffer accumulates to 1280-sample (80ms) chunks before `predict()` — 4× fewer inference calls in the 24/7 idle loop; drains multiple chunks per call.
- **E-M6** chat_session.py: history pruned to `2 × max_transcript_turns` (system message preserved) before each chat call — no more O(n²) token growth / unbounded memory.
- **E-M8** daemon.py wake loop: awaits the session task (1s tick) instead of polling `machine.state` at 10Hz for the whole session.
- **E-L2** vad.py: RMS via `np.dot` (no squared-array temporary per frame).
- (E-H1/E-H3/E-M1 already fixed in batch 1: blocking playback put, daemon capture-only + lazy playback, shared graph. E-L4 numpy import hoist done batch 1.)

### Batch 6 — tests + verification (DONE)

Added `tests/unit/test_review_regressions.py` (24 tests) pinning the fixes the old
suite couldn't catch:
- SE-C1 quiet-gap-doesn't-end-session (drives a real gappy session through the runner)
- SE-C3/E-H1 playback ordering across callbacks + non-blocking write
- SE-C2 runner doesn't install signal handlers when disabled
- SE-H5 SigV4 double-encoding (%3A→%253A in canonical URI)
- S2 untrusted-output wrapping/sanitization + delimiter-forge resistance + size cap
- SE-H4 config TOML round-trip preserves shell rules + MCP servers
- S3 secret redaction (JWT/AWS/GitHub/Bearer)
- util.net private-URL detection (no substring false positives)
- SE-M6 per-turn tool-call cap enforcement
- SE-H1 provider routing to ChatLLMSession
- S6 write_file honors non-sandbox DENY
- S5 git rejects out-of-sandbox path

Also updated test_settings_and_env to send the CSRF token + assert 403 on
missing-token / bad-Host, and to check keys land in secrets.env.

## Final state

- **288 tests pass** (was 264), **coverage 81.4%** (gate ≥80%), **ruff clean** (src + tests).
- Smoke-tested via real CLI: `aegis session once --backend mock` runs end-to-end
  (gateway open/close accounting, cost, clean exit 0); `config init/show/validate`
  round-trip; `doctor` reports readiness without crashing.
- All CRITICAL and HIGH findings from all five reviews are fixed. Remaining
  deferred items are low-value/aspirational (dead audio helpers, unused config
  fields for unbuilt features, magic-number constants, chatgpt_oauth/realtime wire
  verification which needs live API access the author must do on first real run).

## Notes for first real run (needs live credentials — can't verify offline)

- **SE-H7**: the Realtime `session.update` payload + default model id
  `gpt-realtime-2.1-mini` were never tested against the live API. Verify the model
  id and payload shape against current OpenAI Realtime docs before the first real
  voice session; expect to adjust `voice/realtime.py:_send_session_update` and the
  `session.model` default.
- **SE-M11**: `chatgpt_oauth` provider endpoints are speculative; treat as experimental.
- **SE-H6**: openwakeword can't be pip-installed on py3.12 (tflite-runtime). Either
  install it in a compatible env, use the `porcupine` extra + a keyword file, or run
  sessions manually with `aegis session start`. Wake now DISABLES safely (no energy-
  trigger cost trap) if the engine can't load.
- **SE-M2/M7**: daemon-mode approval has no TTY (systemd stdin=null → auto-deny), and
  idle/goodbye timeouts are still unimplemented — a walk-away session bills up to
  `max_duration_s`. Set a conservative `max_duration_s`/`max_session_cost_usd` until
  those land.

## Fixes applied

(pending)
