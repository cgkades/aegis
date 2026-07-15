"""IPC approval broker tests."""

from __future__ import annotations

import asyncio

import pytest

from aegis.approval.broker import ApprovalBroker
from aegis.approval.modes import ApprovalRequest


@pytest.mark.asyncio
async def test_broker_allow() -> None:
    broker = ApprovalBroker(timeout_s=5)
    req = ApprovalRequest(tool_name="write_file", summary="path=/tmp/x", risk="write", call_id="c1")

    async def approve() -> None:
        await asyncio.sleep(0.05)
        assert broker.respond("c1", allowed=True, grant_scope="once")

    task = asyncio.create_task(approve())
    resp = await broker.request(req)
    await task
    assert resp.allowed is True
    assert resp.grant_scope == "once"


@pytest.mark.asyncio
async def test_broker_deny_and_list() -> None:
    broker = ApprovalBroker(timeout_s=5)
    req = ApprovalRequest(tool_name="read_file", summary="path=.env", risk="secrets", call_id="c2")

    async def deny() -> None:
        await asyncio.sleep(0.02)
        pending = broker.list_pending()
        assert any(p["call_id"] == "c2" for p in pending)
        assert broker.respond("c2", allowed=False, reason="nope")

    task = asyncio.create_task(deny())
    resp = await broker.request(req)
    await task
    assert resp.allowed is False
    assert resp.reason in {"nope", "user_denied"}


@pytest.mark.asyncio
async def test_broker_timeout() -> None:
    broker = ApprovalBroker(timeout_s=0.05)
    req = ApprovalRequest(tool_name="x", summary="y", risk="write", call_id="c3")
    resp = await broker.request(req)
    assert resp.allowed is False
    assert resp.reason == "timeout"


@pytest.mark.asyncio
async def test_broker_cancel_all() -> None:
    broker = ApprovalBroker(timeout_s=5)
    req = ApprovalRequest(tool_name="x", summary="y", risk="write", call_id="c4")

    async def cancel() -> None:
        await asyncio.sleep(0.02)
        broker.cancel_all(reason="session_ended")

    task = asyncio.create_task(cancel())
    resp = await broker.request(req)
    await task
    assert resp.allowed is False
    assert resp.reason == "session_ended"
