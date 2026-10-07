"""Injectable clocks so tests can simulate the passage of time."""

from __future__ import annotations

import time
from typing import Protocol as TypingProtocol


class Clock(TypingProtocol):
    """Anything that can report the current time in seconds."""

    def now(self) -> float:
        ...


class SystemClock:
    """Wall-clock time; used when real elapsed time is desired."""

    def now(self) -> float:
        return time.monotonic()


class ManualClock:
    """Deterministic clock driven explicitly by the caller."""

    def __init__(self, start: float = 0.0) -> None:
        self._now = float(start)

    def now(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        if seconds < 0:
            raise ValueError("cannot move the clock backwards")
        self._now += seconds
