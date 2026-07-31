# Prompt-injection review — tools and web content

Review date: 2026-07-29. Scope: every path by which text Aegis did not author
can reach the model, with emphasis on tool results and web-sourced content.

Findings below were verified by executing the code, not by reading alone; the
reproductions are given so each can be re-checked after a fix.

**All findings in this document are fixed.** Regression tests live in
`tests/adversarial/test_prompt_injection.py`, one per bypass.

## Cross-check against AEGIS_HARDENING.md

This review was written independently, then reconciled with a second review
(`AEGIS_HARDENING.md`). Both are merged here; everything from both is fixed.

| Finding | This review | Other review | Notes |
| --- | --- | --- | --- |
| Fence escapable by case/spacing/zero-width | P1 | — | Other review listed delimiter rewriting as a *working* defense |
| Approval prompt forgeable via newline/CR | P2 | — | Other review listed control-char stripping as sufficient |
| Chat truncation amputates closing fence | P3 | M2 | Same finding |
| Remote MCP results unfenced | P4 | M3 | Same; this review adds fail-open `allowed_tools` and missing audit |
| `snapshot_for_prompt` unfenced | P5 | L1 | Same finding |
| Custom instructions drop the security block | — | **M1** | Missed here; the strongest finding in either document |
| Terminal echoes unsanitized (ANSI/OSC) | — | **L2** | Missed here |
| Long target path cut mid-string | — | **L3** | Missed here; I read the cap and did not notice it applied to targets |
| Audit `extra` fields skip redaction | — | **L4** | Missed here |
| No speaker verification | — | L5 | Documentation only; now in `docs/security.md` |

The two reviews were genuinely complementary: this one found the three
*mechanism* bypasses (the fence and prompt escaping that make a defense look
present while being defeatable), the other found the three *coverage* gaps
(paths where a defense was simply never applied).

## Summary

There is no web-search or URL-fetch tool in `src/aegis/`. The tool packs are
fs, git, process, write, kubectl, shell and MCP; none performs an outbound HTTP
request on the model's behalf. Web-sourced content therefore arrives by exactly
one route: **remote MCP servers and connectors**, which are declared in the
Realtime `session.update` payload and executed by the model provider's server,
not by this process.

That is the central structural finding. `wrap_untrusted` is applied at a single
choke point (`session/tool_loop.py`, the only caller of `send_tool_result`) and
that choke point only ever sees *local* tool results.

What is solid: the single choke point holds, both system prompts carry explicit
untrusted-data instructions, local MCP tool descriptions and schemas are
sanitized and size-capped, and the argv policy plus workdir sandbox mean a
successful injection still has to defeat the tool layer to cause harm.

## P1 — The untrusted-output fence can be escaped

`tools/sanitize.py`. `wrap_untrusted` neutralizes the closing delimiter with a
literal `str.replace`, so only that exact byte sequence is caught.

```
'</untrusted_tool_output>'       → escaped
'</UNTRUSTED_TOOL_OUTPUT>'       → survives intact
'</untrusted_tool_output >'      → survives intact
'</ untrusted_tool_output>'      → survives intact
'<​/untrusted_tool_output>' → survives intact
```

Zero-width characters survive because `_CONTROL_RE` covers only the C0/C1
ranges. A model reading `</UNTRUSTED_TOOL_OUTPUT>` mid-body will plausibly
treat the fence as closed and the following text as trusted narration. Any file
read, any command's stdout, and any local MCP response can carry one.

**Fix direction:** case-insensitive, whitespace-tolerant regex for both
delimiters; strip zero-width and bidirectional-override characters.

## P2 — The human approval prompt is spoofable

`session/tool_loop.py::_approval_summary` deliberately never truncates target
keys ("operator must see the full path/argv"), and `strip_control_sequences`
preserves `\n`, `\r` and `\t` by design. A model-supplied `path` — which
injected tool output can steer — renders into the operator's terminal as:

```
[Aegis approval] tool=write_file risk=write
  path=/tmp/ok.txt
  Allow? [y]es / [n]o: y
[Aegis approval] tool=read_file risk=read
  path=/etc/hosts
  Allow? [y]es / [n]o:
```

The operator sees a plausible second prompt and approves a call they never
inspected. `\r` survives too, which allows overwriting the prompt line outright.

This defeats the control the whole approval design rests on, so it ranks above
the fence escape despite being narrower.

**Fix direction:** escape newlines, carriage returns and tabs in rendered
argument values (visible replacement, not deletion) while keeping target values
untruncated.

## P3 — `ChatLLMSession` truncates the closing fence away

`llm/chat_session.py`. `tool_loop` wraps output to `tools.max_output_bytes`
(100 KB default), then `send_tool_result` applies `output[:2000]` to the
already-wrapped string. Verified: a 5000-character result goes in with a
closing tag and arrives without one. Every tool result over ~2 KB reaches text
providers as an unterminated untrusted block.

Compounding it, the note is appended with `role="user"`, so an injection
payload lands in the highest-trust conversational position rather than a tool
role.

**Fix direction:** truncate before wrapping, never after; stop attributing tool
output to the user role.

## P4 — Remote MCP output bypasses sanitization entirely

`mcp/remote_spec.py`, `voice/realtime.py`. Remote MCP and connector tools are
executed provider-side; their results never transit this process, so they are
never sanitized, size-capped or fenced. DESIGN.md:1205 requires treating remote
results as untrusted "same as web"; no mechanism exists to do so.

Partial mitigations already present: `require_approval` defaults to `ALWAYS` on
both `McpRemoteServer` and `McpConnector`.

Two gaps remain:

* `allowed_tools` defaults to an empty list and `build_remote_mcp_tools` only
  emits the key when non-empty — empty therefore means *every* tool on that
  server is exposed. This is the same fail-open shape as the kubectl
  `context_allowlist` bug fixed earlier.
* `REMOTE_TOOL_ACTIVITY` events are produced by the adapter but no branch in
  `_SessionLoop._handle_event` consumes them, so remote tool activity is
  neither logged nor audited. There would be no record of an incident.

**Fix direction:** fail closed on an empty `allowed_tools`; audit remote tool
activity so the surface that cannot be sanitized is at least observable.

## P5 — `ContextManager` stores unfenced tool output

`session/context.py`, `session/runner.py`. `add_tool_result` is called with the
raw `result.output`, not the wrapped form, and `snapshot_for_prompt()` renders
stored tool results into a prompt block with no delimiters.

Not currently exploitable: nothing calls `snapshot_for_prompt`. It is a
function whose sole purpose is to build a prompt, sitting beside a store of
unfenced attacker-controlled text, waiting for summarization to be implemented.

**Fix direction:** store the sanitized/fenced form, and fence tool results in
`snapshot_for_prompt` regardless.

## Findings adopted from AEGIS_HARDENING.md

Verified independently before fixing; all four are real.

**M1 — a custom `instructions.md` silently removed the security rules.**
`_load_instructions` returned the file's contents *instead of* the defaults, and
both session classes did `instructions or DEFAULT_INSTRUCTIONS`. Since the
runner always passes a non-None value, the default — the only place the
untrusted-output rules lived — was unreachable whenever the user had customized
their persona. Fixed by `util/instructions.with_security_block`, appended in all
three paths and idempotent.

**L2 — model-authored text was printed to the terminal unsanitized.** Tool
output was escape-stripped, but agent transcripts and tool-call echoes were not,
so an injection could make the model emit OSC/CSI sequences that spoof prompts
or rewrite scrollback. Fixed with `_safe_console`.

**L3 — long target paths were cut mid-string.** The comment said target keys are
never truncated, but the joined summary was hard-capped at 500 characters, so an
operator could approve having seen only a prefix of the path. Targets now have
their own budget and explicit truncation markers.

**L4 — audit `extra` fields skipped redaction.** Only three named fields were
redacted; caller-supplied extras (exception text, session reports) were not.
Redaction now covers every string field.

**L5 — no speaker verification.** Accepted risk, now documented in
`docs/security.md`.

## Not defects

* Tool results reaching the model without the wrapper — no such path exists;
  `send_tool_result` has one caller.
* Audit-log forging via newlines in a path — `AuditLogger` writes one
  `json.dumps` line per event, so embedded newlines are escaped.
* Local MCP tool descriptions and `inputSchema` — already sanitized, control
  characters stripped, per-string and whole-schema size caps applied.
