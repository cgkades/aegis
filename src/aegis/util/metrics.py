"""Local metrics and session cost estimation."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from aegis.voice.protocol import UsageSnapshot

# Provisional list prices USD per 1M tokens (gpt-realtime-2.x family) — calibrate in PR 19.
_RATES = {
    "mini": {
        "input_audio": 10.0,
        "output_audio": 20.0,
        "input_text": 0.6,
        "output_text": 2.4,
        "cached_input": 0.1,
    },
    "full": {
        "input_audio": 32.0,
        "output_audio": 64.0,
        "input_text": 4.0,
        "output_text": 16.0,
        "cached_input": 0.4,
    },
}


def rate_tier(model: str) -> str:
    return "mini" if "mini" in model.lower() else "full"


def estimate_cost_usd(usage: UsageSnapshot, model: str) -> float:
    """Estimate session spend in USD.

    Cached input is re-priced per modality rather than discounted at a flat
    rate: cached tokens are mostly *text* (instructions, tool schemas, history),
    so discounting them all at the audio differential subtracts far more than
    they ever cost and drives the estimate to the zero floor — taking
    ``max_session_cost_usd`` enforcement down with it.
    """
    rates = _RATES[rate_tier(model)]
    in_audio = max(0, usage.input_audio_tokens)
    in_text = max(0, usage.input_text_tokens)
    cached_total = max(0, usage.cached_input_tokens)
    cached_audio = max(0, usage.cached_input_audio_tokens)
    cached_text = max(0, usage.cached_input_text_tokens)

    if cached_total and not (cached_audio or cached_text):
        # Server did not break the cached total down. Attribute it to text
        # first: text is the cheaper input class, so this under-discounts
        # rather than under-reporting spend.
        cached_text = min(cached_total, in_text)
        cached_audio = min(cached_total - cached_text, in_audio)
    cached_audio = min(cached_audio, in_audio)
    cached_text = min(cached_text, in_text)

    cost = 0.0
    cost += ((in_audio - cached_audio) / 1_000_000.0) * rates["input_audio"]
    cost += ((in_text - cached_text) / 1_000_000.0) * rates["input_text"]
    cost += ((cached_audio + cached_text) / 1_000_000.0) * rates["cached_input"]
    cost += (usage.output_audio_tokens / 1_000_000.0) * rates["output_audio"]
    cost += (usage.output_text_tokens / 1_000_000.0) * rates["output_text"]
    return max(0.0, cost)


@dataclass
class SessionMetrics:
    model: str
    started_monotonic: float = field(default_factory=time.monotonic)
    first_audio_at: float | None = None
    usage: UsageSnapshot = field(default_factory=UsageSnapshot)
    estimated_cost_usd: float = 0.0
    tool_calls: int = 0
    errors: int = 0

    def mark_first_audio(self) -> None:
        if self.first_audio_at is None:
            self.first_audio_at = time.monotonic()

    def add_usage(self, snap: UsageSnapshot) -> float:
        self.usage = self.usage.merge(snap)
        self.estimated_cost_usd = estimate_cost_usd(self.usage, self.model)
        return self.estimated_cost_usd

    @property
    def ttfa_s(self) -> float | None:
        if self.first_audio_at is None:
            return None
        return self.first_audio_at - self.started_monotonic

    @property
    def duration_s(self) -> float:
        return time.monotonic() - self.started_monotonic

    def exceeds_cost_cap(self, cap: float) -> bool:
        return cap > 0 and self.estimated_cost_usd >= cap

    def report(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "duration_s": round(self.duration_s, 3),
            "ttfa_s": None if self.ttfa_s is None else round(self.ttfa_s, 3),
            "estimated_cost_usd": round(self.estimated_cost_usd, 6),
            "usage": {
                "input_audio_tokens": self.usage.input_audio_tokens,
                "output_audio_tokens": self.usage.output_audio_tokens,
                "input_text_tokens": self.usage.input_text_tokens,
                "output_text_tokens": self.usage.output_text_tokens,
                "cached_input_tokens": self.usage.cached_input_tokens,
            },
            "tool_calls": self.tool_calls,
            "errors": self.errors,
        }
