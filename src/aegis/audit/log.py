"""JSONL audit log for tool invocations and session events."""

from __future__ import annotations

import json
import threading
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from aegis.util.secrets import redact_secrets


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


@dataclass(slots=True)
class AuditEvent:
    """Single audit record written as one JSON line."""

    event_type: str
    timestamp: str = field(default_factory=_utc_now_iso)
    session_id: str | None = None
    tool_name: str | None = None
    decision: str | None = None
    risk: str | None = None
    args_summary: str | None = None
    result_summary: str | None = None
    error: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self, *, redact: bool = True) -> dict[str, Any]:
        data = asdict(self)
        extra = data.pop("extra", {}) or {}
        # Flatten extra at top level without clobbering core keys
        for key, value in extra.items():
            if key not in data:
                data[key] = value
        if redact:
            for key in ("args_summary", "result_summary", "error"):
                if isinstance(data.get(key), str):
                    data[key] = redact_secrets(data[key])
        # Drop nulls for compact lines
        return {k: v for k, v in data.items() if v is not None}


class AuditLogger:
    """Append-only JSONL audit logger, thread-safe."""

    def __init__(
        self,
        directory: Path,
        *,
        redact: bool = True,
        enabled: bool = True,
    ) -> None:
        self.directory = directory
        self.redact = redact
        self.enabled = enabled
        self._lock = threading.Lock()

    def _path_for_today(self) -> Path:
        day = datetime.now(UTC).strftime("%Y-%m-%d")
        return self.directory / f"{day}.jsonl"

    def write(self, event: AuditEvent) -> Path | None:
        if not self.enabled:
            return None
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self._path_for_today()
        line = json.dumps(event.to_dict(redact=self.redact), ensure_ascii=False)
        with self._lock:
            with path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
            try:
                path.chmod(0o600)
            except OSError:
                pass
        return path

    def log(
        self,
        event_type: str,
        *,
        session_id: str | None = None,
        tool_name: str | None = None,
        decision: str | None = None,
        risk: str | None = None,
        args_summary: str | None = None,
        result_summary: str | None = None,
        error: str | None = None,
        **extra: Any,
    ) -> Path | None:
        return self.write(
            AuditEvent(
                event_type=event_type,
                session_id=session_id,
                tool_name=tool_name,
                decision=decision,
                risk=risk,
                args_summary=args_summary,
                result_summary=result_summary,
                error=error,
                extra=extra,
            )
        )
