"""policy_manager — A general-purpose policy enforcement framework.

Everything is a policy.  Policies chain in registration order, mutating
the context as they go.  First denial stops the chain.
"""

from policy_manager.context import RequestContext
from policy_manager.exceptions import (
    AccessDeniedError,
    PolicyConfigError,
    PolicyError,
    PolicyPendingError,
    StoreError,
)
from policy_manager.manager import PolicyManager
from policy_manager.result import PolicyResult

__all__ = [
    "AccessDeniedError",
    "PolicyConfigError",
    "PolicyError",
    "PolicyManager",
    "PolicyPendingError",
    "PolicyResult",
    "RequestContext",
    "StoreError",
]
