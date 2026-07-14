"""Cost estimation and context manager tests."""

from __future__ import annotations

from aegis.config.schema import SessionContextConfig
from aegis.session.context import ContextManager
from aegis.util.metrics import SessionMetrics, estimate_cost_usd
from aegis.voice.protocol import UsageSnapshot


def test_estimate_cost_positive() -> None:
    u = UsageSnapshot(input_audio_tokens=100_000, output_audio_tokens=50_000)
    cost = estimate_cost_usd(u, "gpt-realtime-2.1-mini")
    assert cost > 0


def test_cost_cap() -> None:
    m = SessionMetrics(model="gpt-realtime-2.1-mini")
    m.add_usage(UsageSnapshot(input_audio_tokens=10_000_000, output_audio_tokens=1))
    assert m.exceeds_cost_cap(0.01)


def test_context_retention() -> None:
    cm = ContextManager(
        SessionContextConfig(max_transcript_turns=5, keep_last_n_tool_results=2)
    )
    for i in range(10):
        cm.add_transcript("user", f"msg {i}")
    assert len(cm.turns) == 5
    cm.add_tool_result("read_file", "x" * 100)
    cm.add_tool_result("list_dir", "y")
    cm.add_tool_result("git_status", "z")
    assert len(cm.tool_results) == 2
