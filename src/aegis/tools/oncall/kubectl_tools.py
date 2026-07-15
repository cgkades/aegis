"""Structured kubectl tool with verb/namespace/context matrix."""

from __future__ import annotations

import asyncio
import json
import re
import shutil
from typing import Any

from aegis.config.schema import ToolsConfig
from aegis.tools.executor import terminate_process
from aegis.tools.policy import scrubbed_env
from aegis.tools.types import ToolResult, ToolSpec
from aegis.util.logging import get_logger

log = get_logger("tools.kubectl")

# Verbs that are read-class when structured tool is used
_READ_VERBS = {"get", "describe", "logs", "top", "api-resources", "api-versions", "explain"}
_WRITE_VERBS = {"apply", "create", "delete", "patch", "scale", "rollout", "exec"}

# Flags that must never appear anywhere in the structured argv (not only in
# extra_args). Positionals (resource/name) must not smuggle these either.
_BANNED_FLAGS = frozenset(
    {
        "--token",
        "--as",
        "--as-group",
        "--kubeconfig",
        "--password",
        "--username",
        "-n",
        "--namespace",
        "-A",
        "--all-namespaces",
        "--context",
        "--server",
        "--cluster",
        "--user",
        "--client-key",
        "--client-certificate",
        "--certificate-authority",
        "--insecure-skip-tls-verify",
        "--token-file",
    }
)

# Resource types / object names are never flags. Reject leading '-' and keep a
# conservative charset so model-supplied strings cannot rewrite argv shape.
_RESOURCE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-/]*$")
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-]*$")

# `extra_args` exist for read-only presentation and selection flags, not to
# smuggle a second target cluster or namespace into an otherwise constrained
# structured invocation. Each flag that consumes a following value is listed so
# a value cannot be mistaken for a positional argument in future validation.
_SAFE_EXTRA_FLAGS = {
    "-o",
    "--output",
    "-l",
    "--selector",
    "--field-selector",
    "--show-labels",
    "--no-headers",
    "--sort-by",
    "--tail",
    "--previous",
    "--since",
    "--since-time",
    "--timestamps",
    "-c",
    "--container",
}
_EXTRA_FLAGS_WITH_VALUE = _SAFE_EXTRA_FLAGS - {
    "--show-labels",
    "--no-headers",
    "--previous",
    "--timestamps",
}


async def handle_kubectl(
    arguments: dict[str, Any],
    *,
    tools: ToolsConfig,
    approved: bool = False,
    spec: ToolSpec | None = None,
) -> ToolResult:
    kcfg = tools.kubectl
    if not kcfg.enabled:
        return ToolResult(
            output='{"error":"kubectl_disabled"}',
            is_error=True,
            risk="exec",
            decision="deny",
        )

    verb = str(arguments.get("verb") or "").strip()
    resource = str(arguments.get("resource") or "").strip()
    name = arguments.get("name")
    namespace = arguments.get("namespace")
    context = arguments.get("context")
    extra = arguments.get("extra_args") or []

    if verb not in kcfg.allowed_verbs:
        return ToolResult(
            output=json.dumps(
                {
                    "error": "verb_not_allowed",
                    "verb": verb,
                    "allowed": kcfg.allowed_verbs,
                }
            ),
            is_error=True,
            risk="exec",
            decision="deny",
        )

    risk = "destroy" if verb == "delete" else ("read" if verb in _READ_VERBS else "write")
    # Secret / sensitive resources are never auto-read into the cloud model.
    resource_l = resource.lower() if resource else ""
    if risk == "read" and _is_sensitive_resource(resource_l):
        risk = "secrets"

    # Validate shape/policy before approval prompts so bad requests deny without
    # user interaction (banned flags, sandbox, charset, allowlists).
    if kcfg.context_allowlist:
        if not isinstance(context, str) or not context or context not in kcfg.context_allowlist:
            return ToolResult(
                output=json.dumps(
                    {
                        "error": "context_not_allowed",
                        "context": context if isinstance(context, str) else "",
                        "allowed": kcfg.context_allowlist,
                    }
                ),
                is_error=True,
                risk=risk,
                decision="deny",
            )

    if resource:
        bad = _invalid_positional("resource", resource, _RESOURCE_RE, risk=risk)
        if bad is not None:
            return bad
    if isinstance(name, str) and name:
        bad = _invalid_positional("name", name, _NAME_RE, risk=risk)
        if bad is not None:
            return bad
    elif name is not None and name != "":
        return ToolResult(
            output=json.dumps({"error": "invalid_name", "name": name}),
            is_error=True,
            risk=risk,
            decision="deny",
        )

    if not isinstance(extra, list) or not all(isinstance(x, str) for x in extra):
        return ToolResult(output='{"error":"extra_args_must_be_string_array"}', is_error=True)
    # Defense in depth: these flags must never be accepted through `extra_args`,
    # even if a future safe-flag table change accidentally includes them.
    for a in extra:
        flag = a.split("=", 1)[0]
        if flag in _BANNED_FLAGS or (flag.startswith("-n") and flag != "-n"):
            return ToolResult(
                output=json.dumps({"error": "banned_flag", "flag": a}),
                is_error=True,
                risk=risk,
                decision="deny",
            )
    if not _safe_extra_args(extra):
        bad = next((a for a in extra if a.startswith("-")), "extra_args")
        return ToolResult(
            output=json.dumps({"error": "extra_arg_not_allowed", "argument": bad}),
            is_error=True,
            risk=risk,
            decision="deny",
        )

    if kcfg.allowed_namespaces and (
        not isinstance(namespace, str) or namespace not in kcfg.allowed_namespaces
    ):
        return ToolResult(
            output=json.dumps(
                {
                    "error": "namespace_not_allowed",
                    "namespace": namespace or "",
                    "allowed": kcfg.allowed_namespaces,
                }
            ),
            is_error=True,
            risk=risk,
            decision="deny",
        )

    if risk != "read" and not approved:
        return ToolResult(
            output='{"error":"approval_required","reason":"kubectl_mutating"}',
            is_error=True,
            risk=risk,
            decision="prompt",
            meta={"needs_approval": True, "arguments": arguments},
        )

    if not shutil.which("kubectl"):
        return ToolResult(
            output='{"error":"kubectl_not_found"}',
            is_error=True,
            risk=risk,
            decision="deny",
        )

    # Controlled flags first, then `--`, then positionals only. That way
    # resource/name can never be parsed as global flags even if validation
    # regresses; kubectl will treat them as operands.
    argv = ["kubectl", verb]
    if isinstance(namespace, str) and namespace:
        argv.extend(["-n", namespace])
    if isinstance(context, str) and context:
        argv.extend(["--context", context])
    argv.extend(extra)
    argv.append("--")
    if resource:
        argv.append(resource)
    if isinstance(name, str) and name:
        argv.append(name)

    env = scrubbed_env(tuple(kcfg.env_allowlist))
    proc: asyncio.subprocess.Process | None = None
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            start_new_session=True,
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(),
            timeout=tools.default_timeout_s,
        )
    except TimeoutError:
        if proc is not None:
            await terminate_process(proc)
        return ToolResult(output='{"error":"timeout"}', is_error=True, risk=risk)
    except asyncio.CancelledError:
        if proc is not None:
            await terminate_process(proc)
        raise
    except OSError as exc:
        return ToolResult(output=json.dumps({"error": str(exc)}), is_error=True, risk=risk)

    out = stdout.decode("utf-8", errors="replace")
    err = stderr.decode("utf-8", errors="replace")
    text = out
    if err:
        text = f"{out}\n[stderr]\n{err}" if out else err
    if len(text.encode()) > tools.max_output_bytes:
        text = text.encode()[: tools.max_output_bytes].decode("utf-8", errors="replace")
        text += "\n…[truncated]"
    return ToolResult(
        output=text or f"(exit {proc.returncode})",
        is_error=proc.returncode != 0,
        risk=risk,
        decision="auto",
        meta={"argv": argv, "exit_code": proc.returncode},
    )


_SENSITIVE_RESOURCES = frozenset(
    {
        "secret",
        "secrets",
        "secrets.v1",
        "secret.v1",
    }
)


def _is_sensitive_resource(resource: str) -> bool:
    if not resource:
        return False
    # Accept "secrets", "secret", "secrets.v1", "v1/secrets", etc.
    base = resource.split("/")[-1].lower()
    return base in _SENSITIVE_RESOURCES or base.startswith("secret")


def _invalid_positional(
    field: str,
    value: str,
    pattern: re.Pattern[str],
    *,
    risk: str,
) -> ToolResult | None:
    """Return a deny ToolResult if resource/name is a flag or bad charset."""
    if value.startswith("-") or not pattern.fullmatch(value):
        return ToolResult(
            output=json.dumps({"error": f"invalid_{field}", field: value}),
            is_error=True,
            risk=risk,
            decision="deny",
        )
    return None


def _safe_extra_args(extra: list[str]) -> bool:
    """Validate a small, presentation-only kubectl flag language."""
    index = 0
    while index < len(extra):
        arg = extra[index]
        if not arg.startswith("-"):
            return False
        flag, separator, value = arg.partition("=")
        if flag not in _SAFE_EXTRA_FLAGS:
            return False
        if flag in _EXTRA_FLAGS_WITH_VALUE:
            if separator:
                if not value or value.startswith("-"):
                    return False
            else:
                index += 1
                if index >= len(extra) or extra[index].startswith("-"):
                    return False
        elif separator:
            return False
        index += 1
    return True


def kubectl_tool_specs() -> list[ToolSpec]:
    return [
        ToolSpec(
            name="kubectl",
            description=(
                "Run an allowlisted kubectl verb (get/describe/logs/top by default). "
                "Namespaces and contexts are constrained by config. Mutating verbs need approval."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "verb": {
                        "type": "string",
                        "description": "kubectl verb, e.g. get, describe, logs",
                    },
                    "resource": {
                        "type": "string",
                        "description": "Resource type, e.g. pods, deployments",
                    },
                    "name": {"type": "string"},
                    "namespace": {"type": "string"},
                    "context": {"type": "string"},
                    "extra_args": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Additional safe flags, e.g. [\"-o\",\"yaml\"]",
                    },
                },
                "required": ["verb"],
                "additionalProperties": False,
            },
            risk="read",
            handler=handle_kubectl,
            env_allowlist=("KUBECONFIG", "KUBECTL_CONTEXT", "KUBERNETES_MASTER"),
        )
    ]
