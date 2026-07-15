"""In-process approval broker for daemon IPC (and tests)."""

from __future__ import annotations

import asyncio
from typing import Any

from aegis.approval.modes import ApprovalRequest, ApprovalResponse, GrantScope
from aegis.util.logging import get_logger

log = get_logger("approval.broker")


class ApprovalBroker:
    """Queue tool approvals for an external UI (IPC / tray) to resolve.

    The session task awaits :meth:`request`; the control plane calls
    :meth:`respond` with the same ``call_id``.
    """

    def __init__(self, *, timeout_s: float = 60.0) -> None:
        self._timeout_s = float(timeout_s)
        self._pending: dict[str, tuple[ApprovalRequest, asyncio.Future[ApprovalResponse]]] = {}
        self._lock = asyncio.Lock()

    def set_timeout(self, timeout_s: float) -> None:
        """Apply a reloaded timeout to approvals created from now on."""
        self._timeout_s = float(timeout_s)

    async def request(self, req: ApprovalRequest) -> ApprovalResponse:
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[ApprovalResponse] = loop.create_future()
        async with self._lock:
            # Replace any stale pending entry for the same id.
            old = self._pending.pop(req.call_id, None)
            if old is not None and not old[1].done():
                old[1].set_result(ApprovalResponse(False, reason="superseded"))
            self._pending[req.call_id] = (req, fut)
        log.info(
            "approval pending call_id=%s tool=%s risk=%s",
            req.call_id,
            req.tool_name,
            req.risk,
        )
        try:
            return await asyncio.wait_for(asyncio.shield(fut), timeout=self._timeout_s)
        except TimeoutError:
            log.warning("approval timeout call_id=%s", req.call_id)
            return ApprovalResponse(False, reason="timeout")
        finally:
            async with self._lock:
                cur = self._pending.get(req.call_id)
                if cur is not None and cur[1] is fut:
                    self._pending.pop(req.call_id, None)

    def respond(
        self,
        call_id: str,
        *,
        allowed: bool,
        grant_scope: GrantScope = "once",
        reason: str = "",
    ) -> bool:
        item = self._pending.get(call_id)
        if item is None:
            return False
        _req, fut = item
        if fut.done():
            return False
        if allowed:
            fut.set_result(ApprovalResponse(True, grant_scope=grant_scope))
        else:
            fut.set_result(
                ApprovalResponse(False, reason=reason or "user_denied")
            )
        return True

    def list_pending(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for call_id, (req, fut) in list(self._pending.items()):
            if fut.done():
                continue
            out.append(
                {
                    "call_id": call_id,
                    "tool_name": req.tool_name,
                    "summary": req.summary,
                    "risk": req.risk,
                }
            )
        return out

    def cancel_all(self, reason: str = "session_ended") -> None:
        for _call_id, (_req, fut) in list(self._pending.items()):
            if not fut.done():
                fut.set_result(ApprovalResponse(False, reason=reason))
        self._pending.clear()
