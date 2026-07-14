"""Kubectl structured tool policy tests."""

from __future__ import annotations

import pytest

from aegis.config.schema import ToolsConfig, ToolsKubectlConfig
from aegis.tools.oncall.kubectl_tools import handle_kubectl


@pytest.mark.asyncio
async def test_kubectl_disabled() -> None:
    tools = ToolsConfig(kubectl=ToolsKubectlConfig(enabled=False))
    r = await handle_kubectl({"verb": "get", "resource": "pods"}, tools=tools)
    assert r.is_error
    assert "disabled" in r.output


@pytest.mark.asyncio
async def test_kubectl_verb_not_allowed() -> None:
    tools = ToolsConfig(
        kubectl=ToolsKubectlConfig(
            enabled=True,
            allowed_verbs=["get", "describe"],
        )
    )
    r = await handle_kubectl({"verb": "delete", "resource": "pod", "name": "x"}, tools=tools)
    assert r.is_error
    assert "verb_not_allowed" in r.output


@pytest.mark.asyncio
async def test_kubectl_namespace_not_allowed() -> None:
    tools = ToolsConfig(
        kubectl=ToolsKubectlConfig(
            enabled=True,
            allowed_verbs=["get"],
            allowed_namespaces=["staging"],
        )
    )
    r = await handle_kubectl(
        {"verb": "get", "resource": "pods", "namespace": "production"},
        tools=tools,
    )
    assert r.is_error
    assert "namespace_not_allowed" in r.output


@pytest.mark.asyncio
async def test_kubectl_mutating_needs_approval() -> None:
    tools = ToolsConfig(
        kubectl=ToolsKubectlConfig(
            enabled=True,
            allowed_verbs=["get", "apply"],
            allowed_namespaces=["staging"],
        )
    )
    r = await handle_kubectl(
        {"verb": "apply", "resource": "-f", "extra_args": ["manifest.yaml"]},
        tools=tools,
        approved=False,
    )
    # apply is write/destroy class → prompt
    assert r.decision == "prompt" or "approval" in r.output
