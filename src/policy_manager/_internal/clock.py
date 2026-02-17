"""Clock abstraction for testable time-dependent logic."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol


class Clock(Protocol):
    """Protocol for getting the current time.  Inject a fake in tests."""

    def now(self) -> datetime: ...


class SystemClock:
    """Default clock backed by the real system time."""

    def now(self) -> datetime:
        return datetime.now(UTC)
