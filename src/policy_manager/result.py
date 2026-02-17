"""PolicyResult — the outcome of a single policy evaluation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class PolicyResult:
    """Immutable result returned by a policy's ``pre_execute`` or ``post_execute``.

    Attributes:
        allowed:     ``True`` if the policy permits the request.
        policy_name: Name of the policy that produced this result.
        reason:      Human-readable explanation (mainly useful on denial).
        pending:     ``True`` when the request is not denied but awaiting
                     asynchronous resolution (e.g. manual review).
        metadata:    Arbitrary extra data the policy wants to surface
                     (remaining credits, review ticket id, etc.).
    """

    allowed: bool
    policy_name: str = ""
    reason: str = ""
    pending: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    # ── Factory helpers ──────────────────────────────────────

    @staticmethod
    def allow(policy_name: str = "") -> PolicyResult:
        return PolicyResult(allowed=True, policy_name=policy_name)

    @staticmethod
    def deny(policy_name: str, reason: str, **meta: Any) -> PolicyResult:
        return PolicyResult(
            allowed=False,
            policy_name=policy_name,
            reason=reason,
            metadata=meta,
        )

    @staticmethod
    def pend(policy_name: str, reason: str = "", **meta: Any) -> PolicyResult:
        return PolicyResult(
            allowed=False,
            pending=True,
            policy_name=policy_name,
            reason=reason,
            metadata=meta,
        )
