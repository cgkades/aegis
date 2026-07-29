# Aegis security model

## Threats

| Threat | Mitigation |
| --- | --- |
| Always-on cloud mic | Local wake only; `CloudAudioGateway` idle assert |
| Prompt injection → shell | Argv policy, reserved binaries, structured tools preferred |
| kubectl via shell | Hard DENY; structured tool + verb/namespace matrix |
| Secrets exfil via read/cat | Secrets path globs → prompt/deny |
| Approval barge-in | ApprovalPending mutes uplink |
| Key leakage in logs | Audit redaction (all string fields, including `extra`) |
| Injection escaping the untrusted fence | Delimiter neutralization is case/whitespace tolerant; invisible and bidi characters stripped |
| Injection forging an approval prompt | Newline/CR/tab escaped in every value rendered to the operator |
| Custom instructions dropping the rules | Security block appended, never substituted |
| Remote MCP results (unsanitizable) | `require_approval=always`, mandatory `allowed_tools`, activity audited |

## Prompt injection

**The policy layer is the security boundary, not the prompt.** The system-prompt
rules reduce how often the model is fooled; they are not what stops a fooled
model from doing damage. That is the argv policy, the path sandbox, the secrets
globs, and human approval for anything not read-class.

Local tool results (files, command output, local MCP responses) pass through one
choke point — `session/tool_loop.py` is the only caller of `send_tool_result` —
where they are escape-stripped, size-capped, and wrapped in
`<untrusted_tool_output>` markers. Anything in the content that could read as a
delimiter is neutralized first, in any case or spacing.

### Remote MCP is the one surface we cannot sanitize

Remote MCP servers and connectors are executed **by the model provider**, not by
Aegis. Their results enter the conversation without passing through this
process, so they cannot be fenced, capped, or stripped locally. A poisoned page
fetched by a remote MCP server is the most realistic injection vector in this
design.

What holds the line instead:

- `require_approval` defaults to `always`.
- `allowed_tools` is **mandatory**; a server or connector that lists none is
  refused at session start rather than exposing everything it offers.
- Private/loopback URLs are refused unless explicitly opted in, with a
  high-severity audit event.
- Remote tool activity is written to the audit log, so the surface that cannot
  be sanitized is at least observable after the fact.
- The system prompt states that MCP, remote and web content is untrusted
  *whether or not it arrives tagged*.

### Accepted risk: no speaker verification

Anyone within earshot can drive the agent; Aegis does not identify who is
speaking. Compensating controls are the wake-word confirm-speech gate, uplink
mute during approval, and approval prompts for every non-read action — but they
assume a human at the keyboard notices the prompt.

Treat `tools.approval.default = auto_readonly` as the **floor** for a machine in
a shared or unattended room, and do not enable shell or kubectl on one.

## Defaults (mvp profile)

- Tools: `fs` only (`list_dir`, `read_file`, `search_files`)
- Shell: **off**
- kubectl: **off**
- Git: off (enable in `standard` profile)

## Enabling higher privilege

1. Set profile `standard` or `oncall` in config.
2. For shell: `tools.shell.enabled = true` (still argv-only + rules).
3. For kubectl: `tools.kubectl.enabled = true` and tighten namespaces/contexts.
4. Never set `tools.kubectl.deny_via_shell = false` casually.

## Audit

JSONL under `~/.local/share/aegis/audit/YYYY-MM-DD.jsonl`.
