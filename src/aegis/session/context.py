"""Conversation context retention for long sessions."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from aegis.config.schema import SessionContextConfig
from aegis.util.logging import get_logger

log = get_logger("session.context")


@dataclass
class ContextManager:
    """Track transcript turns and tool digests; signal when summarization needed."""

    config: SessionContextConfig
    turns: list[dict[str, Any]] = field(default_factory=list)
    tool_results: list[dict[str, Any]] = field(default_factory=list)
    summary: str = ""

    def add_transcript(self, role: str, text: str) -> None:
        if not text:
            return
        self.turns.append({"role": role, "text": text[:4000]})
        max_turns = self.config.max_transcript_turns
        if len(self.turns) > max_turns:
            self.turns = self.turns[-max_turns:]

    def add_tool_result(self, name: str, output: str) -> None:
        cap = self.config.max_tool_result_chars_retained
        digest = output[:cap]
        if len(output) > cap:
            digest += "…[truncated]"
        self.tool_results.append({"name": name, "output": digest})
        keep = self.config.keep_last_n_tool_results
        if len(self.tool_results) > keep:
            self.tool_results = self.tool_results[-keep:]

    @property
    def needs_summary(self) -> bool:
        return len(self.turns) >= self.config.summarize_when_turns_exceed

    def pressure_report(self) -> dict[str, Any]:
        return {
            "turns": len(self.turns),
            "tool_results": len(self.tool_results),
            "needs_summary": self.needs_summary,
            "max_turns": self.config.max_transcript_turns,
        }

    def snapshot_for_prompt(self) -> str:
        parts = []
        if self.summary:
            parts.append(f"## Prior summary\n{self.summary}")
        if self.turns:
            parts.append("## Recent transcript")
            for t in self.turns[-12:]:
                parts.append(f"{t['role']}: {t['text']}")
        if self.tool_results:
            parts.append("## Recent tool results")
            for tr in self.tool_results[-5:]:
                parts.append(f"### {tr['name']}\n{tr['output'][:1500]}")
        return "\n".join(parts)
