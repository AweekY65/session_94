"""Injectable clock used for idle-timeout bookkeeping."""

from __future__ import annotations

import time
from typing import Protocol as TypingProtocol


class Clock(TypingProtocol):
    """Anything that reports the current time in seconds."""

    def now(self) -> float: ...


class SystemClock:
    def now(self) -> float:
        return time.monotonic()


class ManualClock:
    """Deterministic clock for tests; time only moves when told to."""

    def __init__(self, start: float = 0.0) -> None:
        self._now = start

    def now(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        if seconds < 0:
            raise ValueError("cannot move time backwards")
        self._now += seconds
