"""Structured kubectl tool with verb/namespace/context matrix."""

from __future__ import annotations

import asyncio
import contextlib
import json
import shutil
from typing import Any

from aegis.config.schema import ToolsConfig
from aegis.tools.executor import _kill_process_group
from aegis.tools.policy import scrubbed_env
from aegis.tools.types import ToolResult, ToolSpec
from aegis.util.logging import get_logger

log = get_logger("tools.kubectl")

# Verbs that are read-class when structured tool is used
_READ_VERBS = {"get", "describe", "logs", "top", "api-resources", "api-versions", "explain"}
_WRITE_VERBS = {"apply", "create", "delete", "patch", "scale", "rollout", "exec"}


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

    if risk != "read" and not approved:
        return ToolResult(
            output='{"error":"approval_required","reason":"kubectl_mutating"}',
            is_error=True,
            risk=risk,
            decision="prompt",
            meta={"needs_approval": True, "arguments": arguments},
        )

    if namespace and kcfg.allowed_namespaces and namespace not in kcfg.allowed_namespaces:
        return ToolResult(
            output=json.dumps(
                {
                    "error": "namespace_not_allowed",
                    "namespace": namespace,
                    "allowed": kcfg.allowed_namespaces,
                }
            ),
            is_error=True,
            risk=risk,
            decision="deny",
        )

    if context and kcfg.context_allowlist and context not in kcfg.context_allowlist:
        return ToolResult(
            output=json.dumps(
                {
                    "error": "context_not_allowed",
                    "context": context,
                    "allowed": kcfg.context_allowlist,
                }
            ),
            is_error=True,
            risk=risk,
            decision="deny",
        )

    if not isinstance(extra, list) or not all(isinstance(x, str) for x in extra):
        return ToolResult(output='{"error":"extra_args_must_be_string_array"}', is_error=True)
    # Block dangerous extra flags
    banned = {"--token", "--as", "--as-group", "--kubeconfig", "--password", "--username"}
    for a in extra:
        if a.split("=")[0] in banned:
            return ToolResult(
                output=json.dumps({"error": "banned_flag", "flag": a}),
                is_error=True,
                risk=risk,
                decision="deny",
            )

    if not shutil.which("kubectl"):
        return ToolResult(
            output='{"error":"kubectl_not_found"}',
            is_error=True,
            risk=risk,
            decision="deny",
        )

    argv = ["kubectl", verb]
    if resource:
        argv.append(resource)
    if isinstance(name, str) and name:
        argv.append(name)
    if isinstance(namespace, str) and namespace:
        argv.extend(["-n", namespace])
    if isinstance(context, str) and context:
        argv.extend(["--context", context])
    argv.extend(extra)

    env = scrubbed_env(tuple(kcfg.env_allowlist))
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
        _kill_process_group(proc.pid)
        with contextlib.suppress(Exception):
            await proc.wait()
        return ToolResult(output='{"error":"timeout"}', is_error=True, risk=risk)
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
