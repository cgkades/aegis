"""Daemon IPC approval.list / approval.respond."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from aegis.approval.modes import ApprovalRequest
from aegis.config import build_config
from aegis.config.paths import AegisPaths
from aegis.daemon import AegisDaemon
from aegis.ipc import send_request


@pytest.fixture
def paths(tmp_path: Path) -> AegisPaths:
    p = AegisPaths(
        config_dir=tmp_path / "cfg",
        state_dir=tmp_path / "state",
        data_dir=tmp_path / "data",
        cache_dir=tmp_path / "cache",
    )
    p.ensure_dirs()
    return p


@pytest.mark.asyncio
async def test_approval_ipc_respond(paths: AegisPaths) -> None:
    cfg = build_config({"wake": {"enabled": False}})
    daemon = AegisDaemon(cfg, paths)
    task = asyncio.create_task(daemon.start())
    for _ in range(50):
        if paths.socket_path.exists():
            break
        await asyncio.sleep(0.05)

    req = ApprovalRequest(
        tool_name="write_file",
        summary="path=x",
        risk="write",
        call_id="ipc-1",
    )

    async def resolve() -> None:
        await asyncio.sleep(0.1)
        listed = await send_request(paths.socket_path, "approval.list")
        assert listed.ok
        assert listed.result and any(
            p.get("call_id") == "ipc-1" for p in listed.result.get("pending", [])
        )
        resp = await send_request(
            paths.socket_path,
            "approval.respond",
            {"call_id": "ipc-1", "allowed": True, "grant_scope": "once"},
        )
        assert resp.ok

    waiter = asyncio.create_task(daemon.approvals.request(req))
    resolver = asyncio.create_task(resolve())
    result = await waiter
    await resolver
    assert result.allowed is True

    st = await send_request(paths.socket_path, "status")
    assert st.ok
    assert "pending_approvals" in (st.result or {})

    reloaded = await send_request(paths.socket_path, "config.reload")
    assert reloaded.ok

    await send_request(paths.socket_path, "shutdown")
    await asyncio.wait_for(task, timeout=3)


@pytest.mark.asyncio
async def test_reload_keeps_pending_broker_and_approval_input_is_typed(paths: AegisPaths) -> None:
    daemon = AegisDaemon(build_config({"wake": {"enabled": False}}), paths)
    broker = daemon.approvals
    req = ApprovalRequest("write_file", "path=x", "write", "reload-1")
    waiter = asyncio.create_task(broker.request(req))
    await asyncio.sleep(0)

    reloaded = daemon._reload_config()
    assert reloaded.error is None
    assert daemon.approvals is broker
    assert daemon.approvals.list_pending()

    bad = await daemon._dispatch_ipc(
        "approval.respond",
        "request-1",
        {"call_id": "reload-1", "allowed": "false"},
    )
    assert bad.ok is False
    assert bad.error == "allowed_must_be_boolean"

    legacy = await daemon._dispatch_ipc(
        "approval.respond",
        "reload-1",
        {"allow": True, "scope": "tool"},
    )
    assert legacy.ok
    assert (await waiter).allowed is True

    paths.config_file.write_text("[[[invalid", encoding="utf-8")
    invalid_reload = await daemon._dispatch_ipc("config.reload", "reload-invalid", {})
    assert invalid_reload.ok is False
