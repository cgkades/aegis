"""Audit logger tests."""

from __future__ import annotations

import json
from pathlib import Path

from aegis.audit import AuditEvent, AuditLogger


def test_audit_writes_jsonl(tmp_path: Path) -> None:
    logger = AuditLogger(tmp_path)
    path = logger.log(
        "tool_call",
        session_id="s1",
        tool_name="read_file",
        decision="auto",
        risk="read",
        args_summary="path=/tmp/x",
    )
    assert path is not None
    assert path.is_file()
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["event_type"] == "tool_call"
    assert record["tool_name"] == "read_file"
    assert record["session_id"] == "s1"


def test_audit_redacts_secrets(tmp_path: Path) -> None:
    logger = AuditLogger(tmp_path, redact=True)
    logger.log(
        "tool_call",
        args_summary="api_key=sk-abcdefghijklmnopqrstuvwxyz",
    )
    path = next(tmp_path.glob("*.jsonl"))
    content = path.read_text(encoding="utf-8")
    assert "sk-abcdefghijklmnopqrstuvwxyz" not in content
    assert "REDACTED" in content


def test_audit_disabled(tmp_path: Path) -> None:
    logger = AuditLogger(tmp_path, enabled=False)
    assert logger.log("noop") is None
    assert list(tmp_path.glob("*.jsonl")) == []


def test_event_to_dict_drops_nulls() -> None:
    event = AuditEvent(event_type="session_start", session_id="abc")
    data = event.to_dict()
    assert "tool_name" not in data
    assert data["session_id"] == "abc"
