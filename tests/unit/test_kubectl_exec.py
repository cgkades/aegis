"""Kubectl tool more branches (mocked binary)."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from aegis.config.schema import ToolsConfig, ToolsKubectlConfig
from aegis.tools.oncall.kubectl_tools import handle_kubectl, kubectl_tool_specs


@pytest.mark.asyncio
async def test_kubectl_get_success() -> None:
    tools = ToolsConfig(
        kubectl=ToolsKubectlConfig(
            enabled=True,
            allowed_verbs=["get"],
            allowed_namespaces=["staging"],
        )
    )

    class Proc:
        returncode = 0

        async def communicate(self):
            return b"NAME ready\n", b""

    async def fake_exec(*args, **kwargs):
        return Proc()

    with (
        patch("aegis.tools.oncall.kubectl_tools.shutil.which", return_value="/usr/bin/kubectl"),
        patch("asyncio.create_subprocess_exec", side_effect=fake_exec),
    ):
        r = await handle_kubectl(
            {
                "verb": "get",
                "resource": "pods",
                "namespace": "staging",
                "extra_args": ["-o", "name"],
            },
            tools=tools,
            approved=True,
        )
    assert not r.is_error
    assert "NAME" in r.output


@pytest.mark.asyncio
async def test_kubectl_banned_flag() -> None:
    tools = ToolsConfig(
        kubectl=ToolsKubectlConfig(enabled=True, allowed_verbs=["get"])
    )
    with patch("aegis.tools.oncall.kubectl_tools.shutil.which", return_value="/usr/bin/kubectl"):
        r = await handle_kubectl(
            {"verb": "get", "resource": "pods", "extra_args": ["--token=abc"]},
            tools=tools,
        )
    assert r.is_error
    assert "banned_flag" in r.output


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("extra_args"),
    [
        ["--namespace=production"],
        ["-n", "production"],
        ["-A"],
        ["--server=https://bad.example"],
    ],
)
async def test_kubectl_rejects_extra_target_override(extra_args: list[str]) -> None:
    tools = ToolsConfig(
        kubectl=ToolsKubectlConfig(
            enabled=True,
            allowed_verbs=["get"],
            allowed_namespaces=["staging"],
        )
    )
    with patch("aegis.tools.oncall.kubectl_tools.shutil.which", return_value="/usr/bin/kubectl"):
        result = await handle_kubectl(
            {
                "verb": "get",
                "resource": "secrets",
                "namespace": "staging",
                "extra_args": extra_args,
            },
            tools=tools,
        )
    assert result.is_error
    assert result.decision == "deny"


@pytest.mark.asyncio
async def test_kubectl_context_denied() -> None:
    tools = ToolsConfig(
        kubectl=ToolsKubectlConfig(
            enabled=True,
            allowed_verbs=["get"],
            context_allowlist=["dev"],
        )
    )
    r = await handle_kubectl(
        {"verb": "get", "resource": "pods", "context": "prod"},
        tools=tools,
    )
    assert "context_not_allowed" in r.output


def test_kubectl_specs() -> None:
    assert kubectl_tool_specs()[0].name == "kubectl"
