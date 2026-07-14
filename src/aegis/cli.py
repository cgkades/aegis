"""Aegis command-line interface."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from aegis import __version__
from aegis.config import (
    ConfigError,
    config_to_display_dict,
    default_paths,
    load_config,
    validate_config_file,
)


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(__version__, prog_name="aegis")
@click.option(
    "--config",
    "config_path",
    type=click.Path(path_type=Path, dir_okay=False),
    default=None,
    help="Path to config.toml (default: $XDG_CONFIG_HOME/aegis/config.toml).",
)
@click.option(
    "--profile",
    type=click.Choice(["mvp", "standard", "oncall"], case_sensitive=False),
    default=None,
    help="Override profile name for this invocation.",
)
@click.pass_context
def main(
    ctx: click.Context,
    config_path: Path | None,
    profile: str | None,
) -> None:
    """Aegis — local-first voice agent (wake word + OpenAI Realtime + tools)."""
    # Load project/.config .env for local LLM testing (never override shell exports)
    from aegis.config.env import load_dotenv

    load_dotenv()
    ctx.ensure_object(dict)
    ctx.obj["config_path"] = config_path
    ctx.obj["profile"] = profile
    ctx.obj["paths"] = default_paths()


@main.command("version")
def version_cmd() -> None:
    """Print the Aegis version and exit."""
    click.echo(f"aegis {__version__}")


@main.command("status")
def status_cmd() -> None:
    """Show daemon status via unix socket."""
    import asyncio

    from aegis.ipc import pid_alive, read_pid, send_request

    paths = default_paths()
    pid = read_pid(paths.pid_file)
    if not paths.socket_path.exists():
        click.echo("daemon: not running", err=True)
        if pid:
            click.echo(f"  stale pid file: {pid} (alive={pid_alive(pid)})", err=True)
        sys.exit(1)
    try:
        resp = asyncio.run(send_request(paths.socket_path, "status"))
    except Exception as exc:
        click.echo(f"daemon: socket error: {exc}", err=True)
        sys.exit(1)
    if not resp.ok:
        click.echo(f"daemon: error: {resp.error}", err=True)
        sys.exit(1)
    r = resp.result or {}
    click.echo(f"daemon: running pid={r.get('pid')}")
    click.echo(f"  state:      {r.get('state')}")
    click.echo(f"  session_id: {r.get('session_id')}")
    click.echo(f"  wake:       {r.get('wake_enabled')}")
    click.echo(f"  cloud:      {'open' if r.get('cloud_open') else 'closed'}")


@main.command("daemon")
@click.option("--foreground", "foreground", is_flag=True, default=True, help="Run in foreground.")
@click.pass_context
def daemon_cmd(ctx: click.Context, foreground: bool) -> None:
    """Start the always-on aegisd process."""
    from aegis.daemon import run_daemon

    code = run_daemon(
        config_path=str(ctx.obj["config_path"]) if ctx.obj.get("config_path") else None,
        profile=ctx.obj.get("profile"),
    )
    sys.exit(code)


@main.group("session")
def session_group() -> None:
    """Interactive voice session commands."""


@session_group.command("once")
@click.option(
    "--backend",
    type=click.Choice(
        [
            "realtime",
            "openai_api",
            "chatgpt_oauth",
            "litellm",
            "ollama",
            "azure_openai",
            "bedrock",
            "mock",
            "gpt_live",
            "text_fallback",
        ],
        case_sensitive=False,
    ),
    default="realtime",
    help="LLM / voice backend.",
)
@click.option(
    "--max-seconds",
    type=float,
    default=None,
    help="Hard cap on session length (default: config session.max_duration_s).",
)
@click.pass_context
def session_once(ctx: click.Context, backend: str, max_seconds: float | None) -> None:
    """Foreground one-shot conversation (mic → voice backend → speakers)."""
    from aegis.session.runner import run_session_once_sync

    code = run_session_once_sync(
        config_path=str(ctx.obj["config_path"]) if ctx.obj.get("config_path") else None,
        profile=ctx.obj.get("profile"),
        backend=backend,  # type: ignore[arg-type]
        max_seconds=max_seconds,
    )
    sys.exit(code)


@session_group.command("start")
def session_start_cmd() -> None:
    """Ask a running daemon to start a voice session."""
    import asyncio

    from aegis.ipc import send_request

    paths = default_paths()
    if not paths.socket_path.exists():
        click.echo("daemon not running — start with: aegis daemon", err=True)
        sys.exit(1)
    try:
        resp = asyncio.run(
            send_request(paths.socket_path, "session.start", {"source": "cli"})
        )
    except Exception as exc:
        click.echo(f"ipc error: {exc}", err=True)
        sys.exit(1)
    if not resp.ok:
        click.echo(f"error: {resp.error}", err=True)
        sys.exit(1)
    click.echo(resp.result)


@main.group("config")
def config_group() -> None:
    """Configuration helpers."""


@config_group.command("path")
@click.pass_context
def config_path_cmd(ctx: click.Context) -> None:
    """Print config directory and resolved config file path."""
    paths = ctx.obj["paths"]
    click.echo(f"config_dir={paths.config_dir}")
    click.echo(f"config_file={paths.config_file}")
    click.echo(f"state_dir={paths.state_dir}")
    click.echo(f"data_dir={paths.data_dir}")
    click.echo(f"cache_dir={paths.cache_dir}")


@config_group.command("show")
@click.option(
    "--format",
    "fmt",
    type=click.Choice(["json", "summary"], case_sensitive=False),
    default="summary",
    help="Output format.",
)
@click.pass_context
def config_show_cmd(ctx: click.Context, fmt: str) -> None:
    """Load config (with profile expansion) and print it."""
    try:
        cfg = load_config(
            ctx.obj.get("config_path"),
            paths=ctx.obj["paths"],
            profile=ctx.obj.get("profile"),
            missing_ok=True,
        )
    except ConfigError as exc:
        click.echo(str(exc), err=True)
        sys.exit(1)

    if fmt == "json":
        click.echo(json.dumps(config_to_display_dict(cfg), indent=2, sort_keys=True))
        return

    click.echo(f"profile:              {cfg.profile.name.value}")
    click.echo(f"wake.enabled:         {cfg.wake.enabled}")
    click.echo(f"wake.engine:          {cfg.wake.engine.value}")
    click.echo(f"wake.phrase:          {cfg.wake.phrase}")
    click.echo(f"session.provider:     {cfg.session.provider.value}")
    click.echo(f"session.model:        {cfg.session.model}")
    click.echo(f"session.cost_cap_usd: {cfg.session.max_session_cost_usd}")
    click.echo(f"tools.enabled:        {', '.join(cfg.tools.enabled)}")
    click.echo(f"tools.shell.enabled:  {cfg.tools.shell.enabled}")
    click.echo(f"tools.git.enabled:    {cfg.tools.git.enabled}")
    click.echo(f"tools.kubectl.enabled:{cfg.tools.kubectl.enabled}")
    click.echo(f"audio.local_vad:      {cfg.audio.local_vad_enabled}")
    click.echo(f"log_level:            {cfg.app.log_level}")


@config_group.command("validate")
@click.pass_context
def config_validate_cmd(ctx: click.Context) -> None:
    """Validate the config file and exit non-zero on error."""
    path = ctx.obj.get("config_path") or ctx.obj["paths"].config_file
    if not path.is_file():
        # Validate defaults when no file exists
        try:
            cfg = load_config(path, paths=ctx.obj["paths"], profile=ctx.obj.get("profile"))
        except ConfigError as exc:
            click.echo(str(exc), err=True)
            sys.exit(1)
        click.echo(f"ok: no file at {path}; defaults valid (profile={cfg.profile.name.value})")
        return

    try:
        cfg = validate_config_file(path)
        if ctx.obj.get("profile"):
            cfg = load_config(path, profile=ctx.obj["profile"], missing_ok=False)
    except ConfigError as exc:
        click.echo(str(exc), err=True)
        sys.exit(1)

    click.echo(f"ok: {path} (profile={cfg.profile.name.value}, model={cfg.session.model})")


@config_group.command("init")
@click.option("--force", is_flag=True, help="Overwrite existing config.toml.")
@click.pass_context
def config_init_cmd(ctx: click.Context, force: bool) -> None:
    """Create XDG dirs and a starter config.toml from the mvp profile."""
    paths = ctx.obj["paths"]
    paths.ensure_dirs()
    dest = paths.config_file
    if dest.exists() and not force:
        click.echo(f"already exists: {dest} (use --force to overwrite)", err=True)
        sys.exit(1)

    example = _starter_toml()
    dest.write_text(example, encoding="utf-8")
    try:
        dest.chmod(0o600)
    except OSError:
        pass
    click.echo(f"wrote {dest}")
    click.echo("edit and run: aegis config validate")


@main.command("doctor")
@click.option("--idle-profile", is_flag=True, help="Sample idle CPU/RSS briefly.")
@click.option("--seconds", default=3.0, show_default=True, help="Idle profile duration.")
@click.pass_context
def doctor_cmd(ctx: click.Context, idle_profile: bool, seconds: float) -> None:
    """Environment diagnostics and readiness checks."""
    import os
    import shutil
    import time

    from aegis.activation import detect_hotkey_backend, detect_session_type
    from aegis.audio import list_devices, sounddevice_available
    from aegis.tools.factory import build_registry
    from aegis.util.secrets import resolve_api_key
    from aegis.voice.factory import provider_status
    from aegis.voice.gateway import default_gateway

    paths = ctx.obj["paths"]
    exists = "exists" if paths.config_file.is_file() else "missing"
    click.echo("aegis doctor")
    click.echo(f"  version:     {__version__}")
    click.echo("  python:      ok")
    click.echo(f"  config_dir:  {paths.config_dir}")
    click.echo(f"  config_file: {paths.config_file} ({exists})")
    try:
        cfg = load_config(
            ctx.obj.get("config_path"),
            paths=paths,
            profile=ctx.obj.get("profile"),
            missing_ok=True,
        )
        click.echo(f"  config:      ok (profile={cfg.profile.name.value})")
        click.echo(f"  model:       {cfg.session.model}")
        click.echo(f"  provider:    {cfg.session.provider.value}")
        click.echo(f"  shell:       {'enabled' if cfg.tools.shell.enabled else 'disabled'}")
        click.echo(f"  kubectl:     {'enabled' if cfg.tools.kubectl.enabled else 'disabled'}")
    except ConfigError as exc:
        click.echo(f"  config:      ERROR: {exc}")
        sys.exit(1)

    key = resolve_api_key(env_var=cfg.openai.api_key_env, secrets_file=paths.secrets_env)
    click.echo(f"  api_key:     {'set' if key else 'MISSING (' + cfg.openai.api_key_env + ')'}")

    if sounddevice_available():
        devices = list_devices()
        inputs = [d for d in devices if d.max_input_channels > 0]
        outputs = [d for d in devices if d.max_output_channels > 0]
        click.echo(f"  sounddevice: ok ({len(devices)} devices)")
        click.echo(f"  inputs:      {len(inputs)}")
        click.echo(f"  outputs:     {len(outputs)}")
    else:
        click.echo("  sounddevice: not installed (uv sync --extra audio)")

    # Wake
    try:
        import openwakeword  # noqa: F401

        click.echo("  openwakeword: installed")
    except Exception:
        click.echo("  openwakeword: not installed (optional)")

    reg = build_registry(cfg)
    click.echo(f"  tools:       {', '.join(reg.names()) or '(none)'}")

    info = detect_hotkey_backend(cfg.activation)
    click.echo(f"  session:     {detect_session_type()}")
    click.echo(f"  hotkey:      {info.backend.value} — {info.notes}")

    prov = provider_status(cfg)
    click.echo(f"  gpt_live:    {'available' if prov['gpt_live_available'] else 'stub only'}")

    try:
        default_gateway.assert_idle_has_no_cloud()
        click.echo("  cloud_idle:  ok (no open cloud audio)")
    except Exception as exc:
        click.echo(f"  cloud_idle:  FAIL {exc}")

    click.echo(f"  kubectl_bin: {'yes' if shutil.which('kubectl') else 'no'}")
    click.echo(f"  git_bin:     {'yes' if shutil.which('git') else 'no'}")

    if idle_profile:
        click.echo(f"  idle_profile sampling {seconds}s…")
        try:
            import resource

            start = time.monotonic()
            # Busy-wait lightly reading self
            while time.monotonic() - start < seconds:
                time.sleep(0.05)
            rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            # Linux ru_maxrss is KB
            click.echo(f"  rss_max_kb:  {rss}")
            click.echo(f"  pid:         {os.getpid()}")
        except Exception as exc:
            click.echo(f"  idle_profile error: {exc}")


@main.command("activation")
@click.pass_context
def activation_cmd(ctx: click.Context) -> None:
    """Show how to activate Aegis (hotkey / DE keybind / CLI)."""
    from aegis.activation import print_activation_help

    cfg = load_config(
        ctx.obj.get("config_path"),
        paths=ctx.obj["paths"],
        profile=ctx.obj.get("profile"),
        missing_ok=True,
    )
    print_activation_help(cfg.activation)


@main.command("settings")
@click.option("--host", default="127.0.0.1", show_default=True, help="Bind address.")
@click.option("--port", default=8765, show_default=True, type=int, help="HTTP port.")
@click.option(
    "--no-browser",
    is_flag=True,
    help="Do not open a browser automatically.",
)
def settings_cmd(host: str, port: int, no_browser: bool) -> None:
    """Open the local LLM settings page (http://127.0.0.1:8765)."""
    from aegis.ui.settings_server import run_settings_server

    raise SystemExit(
        run_settings_server(host=host, port=port, open_browser=not no_browser)
    )


@main.group("auth")
def auth_group() -> None:
    """ChatGPT OAuth and credential helpers."""


@auth_group.command("status")
@click.pass_context
def auth_status_cmd(ctx: click.Context) -> None:
    """Show ChatGPT OAuth sign-in status."""
    from aegis.llm.chatgpt_oauth import status_dict

    cfg = load_config(
        ctx.obj.get("config_path"),
        paths=ctx.obj["paths"],
        profile=ctx.obj.get("profile"),
        missing_ok=True,
    )
    st = status_dict(cfg.llm.chatgpt_oauth.token_path)
    if st.get("signed_in"):
        click.echo(f"signed_in: yes ({st.get('email') or 'account'})")
    else:
        click.echo("signed_in: no")
        if st.get("expired") and st.get("email"):
            click.echo("  token present but expired — run: aegis auth login")
    click.echo(f"token_path: {cfg.llm.chatgpt_oauth.token_path}")


@auth_group.command("login")
@click.option("--no-browser", is_flag=True, help="Print URL only; do not open browser.")
@click.pass_context
def auth_login_cmd(ctx: click.Context, no_browser: bool) -> None:
    """Sign in with ChatGPT (device code / browser)."""
    from aegis.llm.chatgpt_oauth import login_with_device_code

    cfg = load_config(
        ctx.obj.get("config_path"),
        paths=ctx.obj["paths"],
        profile=ctx.obj.get("profile"),
        missing_ok=True,
    )
    try:
        result = login_with_device_code(
            cfg.llm.chatgpt_oauth.token_path,
            auth_base=cfg.llm.chatgpt_oauth.auth_base_url,
            client_id=cfg.llm.chatgpt_oauth.client_id,
            open_browser=not no_browser,
        )
    except Exception as exc:
        click.echo(f"login failed: {exc}", err=True)
        click.echo(
            "Fallback: open Settings and paste an access token, "
            "or use OPENAI_API_KEY for API access.",
            err=True,
        )
        sys.exit(1)
    click.echo(f"user_code: {result.get('user_code')}")
    click.echo(f"verification_url: {result.get('verification_url')}")
    click.echo(f"signed in as: {result.get('email') or '(ok)'}")
    click.echo(f"saved: {result.get('token_path')}")


@auth_group.command("logout")
@click.pass_context
def auth_logout_cmd(ctx: click.Context) -> None:
    """Remove stored ChatGPT OAuth tokens."""
    from aegis.llm.chatgpt_oauth import clear_tokens

    cfg = load_config(
        ctx.obj.get("config_path"),
        paths=ctx.obj["paths"],
        profile=ctx.obj.get("profile"),
        missing_ok=True,
    )
    clear_tokens(cfg.llm.chatgpt_oauth.token_path)
    click.echo("signed out")


def _starter_toml() -> str:
    return """# Aegis config — see DESIGN.md and configs/aegis.example.toml

[profile]
name = "mvp"

[app]
log_level = "info"

[wake]
enabled = true
engine = "openwakeword"
phrase = "hey_aegis"
confirm_speech_timeout_s = 1.5

[session]
provider = "realtime"
# model comes from profile (mvp → gpt-realtime-2.1-mini) unless set here
# model = "gpt-realtime-2.1-mini"
max_session_cost_usd = 2.0

[tools.shell]
enabled = false

[tools.kubectl]
enabled = false

[privacy]
store_transcripts = true
store_audio = false
"""


if __name__ == "__main__":
    raise SystemExit(main())
