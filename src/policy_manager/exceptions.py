"""Custom exceptions for the policy_manager package."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from policy_manager.result import PolicyResult


class PolicyError(Exception):
    """Base exception for all policy-related errors."""


class AccessDeniedError(PolicyError):
    """Raised when a policy denies access."""

    def __init__(self, result: PolicyResult) -> None:
        self.result = result
        super().__init__(f"Access denied by policy '{result.policy_name}': {result.reason}")


class PolicyPendingError(PolicyError):
    """Raised when a policy returns a pending verdict (async resolution needed)."""

    def __init__(self, result: PolicyResult) -> None:
        self.result = result
        super().__init__(f"Policy '{result.policy_name}' is pending: {result.reason}")


class PolicyConfigError(PolicyError):
    """Raised when a policy is misconfigured."""

    def __init__(self, policy_name: str, message: str) -> None:
        self.policy_name = policy_name
        super().__init__(f"Policy '{policy_name}' misconfigured: {message}")


class StoreError(PolicyError):
    """Raised when a store operation fails."""

    def __init__(self, operation: str, detail: str = "") -> None:
        self.operation = operation
        msg = f"Store error during '{operation}'"
        if detail:
            msg += f": {detail}"
        super().__init__(msg)
