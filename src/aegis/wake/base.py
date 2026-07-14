"""Wake-word engine protocol."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

import numpy as np


@dataclass(frozen=True, slots=True)
class WakeEvent:
    """Fired when a wake phrase is detected."""

    phrase: str
    score: float
    engine: str


@runtime_checkable
class WakeEngine(Protocol):
    """Streaming wake-word detector consuming 16 kHz mono int16 PCM."""

    name: str

    def start(self) -> None:
        """Load models / allocate resources."""
        ...

    def stop(self) -> None:
        """Release resources."""
        ...

    def process(self, pcm_16k: np.ndarray) -> WakeEvent | None:
        """Process a chunk of 16 kHz mono int16 audio; return event if triggered."""
        ...

    def reset(self) -> None:
        """Clear internal buffers after a session ends."""
        ...
