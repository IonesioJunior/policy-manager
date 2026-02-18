"""RateLimitPolicy — sliding-window request rate limiter."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from policy_manager._internal.clock import Clock, SystemClock
from policy_manager.policies.base import Policy
from policy_manager.result import PolicyResult

if TYPE_CHECKING:
    from policy_manager.context import RequestContext

_NS_PREFIX = "rate_limit"


class RateLimitPolicy(Policy):
    """Limits the number of requests a user can make within a time window.

    Uses a sliding-window counter stored in the policy store.  On each
    ``pre_execute`` call the policy prunes expired timestamps, checks the
    count, and either allows (incrementing the counter) or denies.

    Parameters:
        name:            Unique policy name.
        max_requests:    Maximum allowed requests per window.
        window_seconds:  Length of the sliding window in seconds.
        clock:           Injectable clock for testing.
    """

    _policy_type = "rate_limit"
    _policy_description = "Limits request rate per user within a time window"

    def __init__(
        self,
        *,
        name: str = "rate_limit",
        max_requests: int,
        window_seconds: int,
        clock: Clock | None = None,
    ) -> None:
        self._name = name
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._clock = clock or SystemClock()

    @property
    def name(self) -> str:
        return self._name

    @property
    def namespace(self) -> str:
        return f"{_NS_PREFIX}:{self._name}"

    def export(self) -> dict[str, Any]:
        data = super().export()
        data["config"] = {
            "max_requests": self.max_requests,
            "window_seconds": self.window_seconds,
        }
        return data

    async def pre_execute(self, context: RequestContext) -> PolicyResult:
        now = self._clock.now()
        cutoff = now.timestamp() - self.window_seconds

        state = await self.store.get(self.namespace, context.user_id)
        timestamps: list[float] = state.get("timestamps", []) if state else []

        # Prune expired entries
        timestamps = [ts for ts in timestamps if ts > cutoff]

        if len(timestamps) >= self.max_requests:
            return PolicyResult.deny(
                self.name,
                f"Rate limit exceeded: {self.max_requests} requests per {self.window_seconds}s",
                remaining=0,
                reset_at=timestamps[0] + self.window_seconds,
            )

        timestamps.append(now.timestamp())
        await self.store.set(self.namespace, context.user_id, {"timestamps": timestamps})

        remaining = self.max_requests - len(timestamps)
        context.metadata[f"{self.name}_remaining"] = remaining

        return PolicyResult.allow(self.name)
