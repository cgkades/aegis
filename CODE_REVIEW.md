# Multi-Agent Code Review Prompt

A reusable prompt for running a thorough, multi-perspective code review with
parallel subagents, a running findings log, and a fix-and-verify loop. Paste the
**Orchestrator prompt** below into Claude Code (or hand it to any agent runner
that can spawn subagents). Adjust the scope line and model choices as needed.

---

## How to use

1. Fill in the **Project context** block (one paragraph: what the code does, the
   entry points, the stack, and any "not yet run / just shipped" caveats).
2. Give the orchestrator the prompt. It spawns the reviewer agents **in parallel**,
   collects findings into `REVIEW_LOG.md`, then fixes in priority order and loops
   until the suite is green and the findings are addressed.
3. Reviewers **only report** — they never edit. Only the orchestrator edits, so
   fixes don't collide.

## Model guide (per agent)

Assign the strongest model to the tasks where a subtle miss is most expensive
(security, cross-module correctness, the fix loop) and cheaper models to
mechanical or single-lens passes.

| Agent | Model | Why |
|-------|-------|-----|
| Orchestrator / fixer | **opus** | Holds the whole picture, sequences fixes, resolves conflicts, writes tests. |
| Security + prompt-injection | **opus** | Highest stakes; needs adversarial, multi-step reasoning and threat modeling. |
| Software engineering / architecture / first-run correctness | **opus** | Cross-module contracts, race conditions, lifecycle — the costliest bugs. |
| Efficiency / performance | **sonnet** | Focused single-lens pass; strong enough to reason about hot paths. |
| Python (or language) idioms & correctness | **sonnet** | Well-scoped, pattern-based; Sonnet is reliable and cheaper. |
| Code quality / dead code / duplication | **haiku** | Largely mechanical: grep-verify usage, spot copy-paste, stale comments. Escalate a specific file to Sonnet if a judgment call is needed. |
| Test author (optional separate agent) | **sonnet** | Writes regression tests from the confirmed findings. |
| Docs / comments accuracy (optional) | **haiku** | Cheap pass for stale/misleading comments and READMEs. |

Rules of thumb:
- **Never** run the security or architecture pass on Haiku.
- If budget is tight, collapse Python + code-quality into one Sonnet agent.
- If the codebase is large (>50k LOC), split each reviewer by subsystem and run
  more agents rather than one agent over everything.

---

## Orchestrator prompt (paste this)

> You are the orchestrator of a comprehensive, multi-agent code review. Your goal
> is to find real problems, fix them safely, and leave the codebase in a
> genuinely good state with tests that make the next run succeed. Work until the
> findings are addressed and the suite is green, or until you run out of budget.
>
> ### Project context
> <!-- FILL THIS IN -->
> - What it does: `...`
> - Entry points / startup path: `...`
> - Stack: `...`
> - Tests: `...` (how to run, current pass/coverage baseline if known)
> - Caveats: e.g. "spec was written after the code — the code is the source of
>   truth"; "author has never run this — first-run success matters most".
>
> ### Ground rules
> 1. **Establish a baseline first**: run the test suite and linter, record
>    pass count + coverage + lint status. Do not start fixing until you have it.
> 2. **Create `REVIEW_LOG.md`** and keep it current: a findings table per reviewer
>    (id, severity, `file:line`, one-line issue), then a "Fixes applied" section
>    where each fix cites the finding id and a verification note. This is the
>    durable record — update it as you go, not at the end.
> 3. **Spawn the reviewer agents below in parallel.** Each reviewer ONLY reports
>    findings; it must not edit files. Use the model specified for each.
> 4. **Verify every finding against the actual code before acting on it** —
>    reviewers can be wrong or hallucinate line numbers. Skip or downgrade
>    findings that don't hold up; note that in the log.
> 5. **Fix in priority order**: (a) correctness / first-run blockers,
>    (b) security, (c) dead code + quality, (d) efficiency, (e) tests. Batch
>    related fixes; run the relevant tests after each batch; run the full suite +
>    lint before moving to the next batch. Never leave the suite red.
> 6. **Add regression tests** for every non-trivial bug you fix — especially ones
>    the existing suite missed. Prefer a test that would have failed before the
>    fix. Keep coverage at or above the project's gate.
> 7. **Match the surrounding code**: naming, comment density, error style, idioms.
>    Don't introduce new dependencies without noting why in the log.
> 8. **Smoke-test the real entry points** at the end (CLI/server/app), not just
>    unit tests, to catch first-run failures the tests can't see.
> 9. Track progress with a task list. When done, summarize: what was fixed, what
>    was deferred (and why), and anything that needs live credentials / a human to
>    verify.
>
> ### Reviewers to spawn (parallel; report-only)
>
> Spawn each as a subagent with the specified model. Give each the Project
> context, tell it to read every relevant source file, and to return findings
> ordered by severity in this format:
> `SEVERITY (critical/high/medium/low) — FILE:LINE — ISSUE (one sentence) — FIX (concrete)`,
> ending with a one-paragraph overall assessment. Tell each: "Do NOT edit files.
> Skip pure style nits the linter already enforces. Verify dead-code / unused
> claims with a grep across the repo before reporting."
>
> **1. Security & prompt-injection — model: opus**
> Defensive review of the author's own code. Focus on: secret exposure (keys /
> tokens logged, written to audit/telemetry, put in error messages, sent to the
> wrong endpoint, world-readable, or visible in `ps`/`/proc`); local attack
> surface (socket/file permissions, any bound HTTP server — CSRF, Host/Origin,
> auth, DNS-rebinding, bind address); command/tool execution safety (shell
> injection, path traversal, policy/approval bypass, env inheritance);
> supply-chain / SSRF. If the system feeds external or tool-produced content back
> to an LLM that can call tools, assess **prompt injection**: is untrusted output
> demarcated, sanitized (ANSI/control stripped), size-capped, and prevented from
> triggering auto-approved dangerous actions? Recommend concrete defenses.
> Give each finding an attack scenario.
>
> **2. Software engineering / architecture / first-run correctness — model: opus**
> Trace the real startup path end to end. Find: race conditions, deadlocks,
> resource leaks, state-machine holes, incorrect error propagation, lifecycle /
> shutdown / signal-handling bugs, leaky or inconsistent abstractions, and
> contract mismatches between modules. Pay special attention to **first-run
> failure risks** — places where the happy path works but the first real run
> would crash (missing device, dropped connection, malformed config, stale
> socket, wrong wire payload, unresolvable dependency). Flag features that are
> configured/advertised but never wired into the runtime path.
>
> **3. Efficiency / performance — model: sonnet**
> Prioritize hot paths and any always-on / per-frame / per-request loop. Find:
> per-iteration allocations or copies, busy-wait/polling with short sleeps,
> blocking I/O on an event loop, unbounded queues/caches/history (memory growth
> over uptime), repeated recomputation (recompiled regexes, re-read files,
> re-created clients), O(n^2) patterns, and chatty logging. Quantify impact
> (frequency × cost) and give a concrete cheaper approach. Ignore micro-opts on
> cold paths unless egregious.
>
> **4. Language idioms & correctness (e.g. Python) — model: sonnet**
> Review for idiomatic usage and correctness the linter won't catch: async
> pitfalls (blocking calls in async, un-awaited tasks, cancellation swallowing),
> exception anti-patterns (bare/over-broad except, exceptions built into invalid
> data such as f-string JSON), resource cleanup / context managers, typing gaps on
> public APIs, mutable defaults, module-level side effects, and reinventing stdlib
> or ecosystem tooling. Flag `assert` used for runtime invariants.
>
> **5. Code quality / dead code / duplication — model: haiku**
> Find, grep-verifying every claim across src + tests + entry points: unused
> functions/classes/constants, unreachable branches, modules nothing imports,
> config options nothing reads, half-wired features, copy-paste duplication,
> stale/misleading comments, TODO/FIXME/HACK worth resolving, magic numbers, and
> overly long multi-job functions. For each dead-code item, state whether it is
> safe to delete and who might depend on it. Escalate any judgment call to the
> orchestrator rather than guessing.
>
> ### Optional additional reviewers
> - **Docs & comments accuracy — haiku**: comments that lie vs. the code, stale
>   READMEs, wrong examples.
> - **Tests & coverage — sonnet**: weak/missing coverage on critical paths,
>   flaky patterns, tests that assert nothing, over-mocking that hides real bugs.
> - For large repos, duplicate reviewers 2 and 5 per subsystem.
>
> ### Finish
> When findings are addressed: confirm the full suite passes, lint is clean,
> coverage meets the gate, and the real entry points run. Then summarize results
> and list anything deferred or requiring human/live-credential verification.

---

## Notes

- **Parallelism**: launch all reviewers at once; collect results as they land.
  They're independent, so total wall-clock ≈ the slowest reviewer, not the sum.
- **Report-only reviewers** matter: it keeps findings clean and lets the
  orchestrator dedupe overlaps (the same bug often shows up in 2–3 reviews) and
  resolve conflicts before touching code.
- **Verify before fixing**: subagents occasionally cite wrong line numbers or
  flag intentional behavior. A quick read of the actual code before each fix
  prevents churn.
- **Scale the effort to the ask**: a quick "any obvious bugs?" needs 2–3
  reviewers on Sonnet; a "harden this before launch" warrants the full Opus-led
  panel plus the optional reviewers and a test-authoring pass.
