"""CustomPolicy — wrap any callable as a policy without subclassing."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from policy_manager.policies.base import Policy
from policy_manager.result import PolicyResult

if TYPE_CHECKING:
    from policy_manager.context import RequestContext

# The check callable can be sync or async.
# It receives a RequestContext and returns bool (True = allow).
CheckFn = Callable[["RequestContext"], bool] | Callable[["RequestContext"], Any]


class CustomPolicy(Policy):
    """Wraps a plain callable as a policy — no subclassing required.

    Parameters:
        name:        Unique policy name.
        phase:       ``"pre"``, ``"post"``, or ``"both"``.
        check:       Callable ``(context) -> bool``.  ``True`` = allow.
                     May be sync or async.
        deny_reason: Message returned when the check returns ``False``.
    """

    _policy_type = "custom"
    _policy_description = "Custom callable-based policy"

    def __init__(
        self,
        *,
        name: str,
        phase: str = "pre",
        check: CheckFn,
        deny_reason: str = "Custom policy check failed",
    ) -> None:
        self._name = name
        self._phase = phase
        self._check = check
        self._deny_reason = deny_reason

    @property
    def name(self) -> str:
        return self._name

    def export(self) -> dict[str, Any]:
        data = super().export()
        data["config"] = {
            "phase": self._phase,
            "deny_reason": self._deny_reason,
            "has_check": self._check is not None,
        }
        return data

    async def _run_check(self, context: RequestContext) -> PolicyResult:
        result = self._check(context)
        if asyncio.iscoroutine(result):
            result = await result

        if result:
            return PolicyResult.allow(self.name)
        return PolicyResult.deny(self.name, self._deny_reason)

    async def pre_execute(self, context: RequestContext) -> PolicyResult:
        if self._phase in ("pre", "both"):
            return await self._run_check(context)
        return PolicyResult.allow(self.name)

    async def post_execute(self, context: RequestContext) -> PolicyResult:
        if self._phase in ("post", "both"):
            return await self._run_check(context)
        return PolicyResult.allow(self.name)
