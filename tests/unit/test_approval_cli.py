"""Approval prompt CLI paths."""

from __future__ import annotations

import io
from unittest.mock import patch

import pytest

from aegis.approval.modes import ApprovalRequest, prompt_cli_approval
from aegis.config.schema import ToolsApprovalConfig


@pytest.mark.asyncio
async def test_prompt_yes() -> None:
    cfg = ToolsApprovalConfig(timeout_s=5)
    req = ApprovalRequest("read_file", "path=.env", "secrets", "c1")
    with patch("sys.stdin", io.StringIO("y\n")):
        resp = await prompt_cli_approval(req, cfg)
    assert resp.allowed is True
    assert resp.grant_scope == "once"


@pytest.mark.asyncio
async def test_prompt_session_grant() -> None:
    cfg = ToolsApprovalConfig(timeout_s=5)
    req = ApprovalRequest("write_file", "path=a", "write", "c1")
    with patch("sys.stdin", io.StringIO("s\n")):
        resp = await prompt_cli_approval(req, cfg)
    assert resp.allowed is True
    assert resp.grant_scope == "same_tool"


@pytest.mark.asyncio
async def test_prompt_no() -> None:
    cfg = ToolsApprovalConfig(timeout_s=5)
    req = ApprovalRequest("t", "x", "exec", "c1")
    with patch("sys.stdin", io.StringIO("n\n")):
        resp = await prompt_cli_approval(req, cfg)
    assert resp.allowed is False
