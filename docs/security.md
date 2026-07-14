# Aegis security model

## Threats

| Threat | Mitigation |
| --- | --- |
| Always-on cloud mic | Local wake only; `CloudAudioGateway` idle assert |
| Prompt injection → shell | Argv policy, reserved binaries, structured tools preferred |
| kubectl via shell | Hard DENY; structured tool + verb/namespace matrix |
| Secrets exfil via read/cat | Secrets path globs → prompt/deny |
| Approval barge-in | ApprovalPending mutes uplink |
| Key leakage in logs | Audit redaction |

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
