"""ManualReviewPolicy — post-execution hold for human review."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from policy_manager.policies.base import Policy
from policy_manager.result import PolicyResult

if TYPE_CHECKING:
    from policy_manager.context import RequestContext


_NS_PREFIX = "manual_review"

# Callback signature: (review_payload) -> {"approved": bool}
ReviewCallback = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


class ManualReviewPolicy(Policy):
    """Holds responses for manual review before they reach the end user.

    On ``post_execute`` the policy always returns a **pending** result,
    stashing the full request/response in the store for later resolution.

    A ``review_callback`` (if provided) is called immediately — if it
    returns ``{"approved": True}`` the policy short-circuits to allow.

    Parameters:
        name:             Unique policy name.
        review_callback:  Optional async callable for immediate automated review.
    """

    _policy_type = "manual_review"
    _policy_description = "Holds responses for manual review before delivery"

    def __init__(
        self,
        *,
        name: str = "manual_review",
        review_callback: ReviewCallback | None = None,
    ) -> None:
        self._name = name
        self._review_cb = review_callback

    @property
    def name(self) -> str:
        return self._name

    @property
    def namespace(self) -> str:
        return f"{_NS_PREFIX}:{self._name}"

    def export(self) -> dict[str, Any]:
        data = super().export()
        data["config"] = {
            "has_review_callback": self._review_cb is not None,
        }
        return data

    async def post_execute(self, context: RequestContext) -> PolicyResult:
        review_id = uuid.uuid4().hex[:12]

        payload: dict[str, Any] = {
            "review_id": review_id,
            "user_id": context.user_id,
            "input": context.input,
            "output": context.output,
            "timestamp": context.timestamp.isoformat(),
            "status": "pending",
        }

        # Try automated review first
        if self._review_cb:
            result = await self._review_cb(payload)
            if result.get("approved", False):
                payload["status"] = "approved"
                await self.store.set(self.namespace, review_id, payload)
                return PolicyResult.allow(self.name)

        # Otherwise hold for manual review
        await self.store.set(self.namespace, review_id, payload)

        return PolicyResult.pend(
            self.name,
            reason="Response held for manual review",
            review_id=review_id,
        )

    # ── management helpers ───────────────────────────────────

    async def approve(self, review_id: str) -> bool:
        """Mark a pending review as approved.  Returns False if not found."""
        payload = await self.store.get(self.namespace, review_id)
        if not payload:
            return False
        payload["status"] = "approved"
        await self.store.set(self.namespace, review_id, payload)
        return True

    async def reject(self, review_id: str, reason: str = "") -> bool:
        """Mark a pending review as rejected.  Returns False if not found."""
        payload = await self.store.get(self.namespace, review_id)
        if not payload:
            return False
        payload["status"] = "rejected"
        payload["reject_reason"] = reason
        await self.store.set(self.namespace, review_id, payload)
        return True

    async def get_pending(self) -> list[dict[str, Any]]:
        """Return all pending review entries."""
        keys = await self.store.list_keys(self.namespace)
        results = []
        for key in keys:
            entry = await self.store.get(self.namespace, key)
            if entry and entry.get("status") == "pending":
                results.append(entry)
        return results
