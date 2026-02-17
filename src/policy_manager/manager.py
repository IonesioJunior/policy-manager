"""PolicyManager — the central orchestrator."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from policy_manager.result import PolicyResult
from policy_manager.stores.memory import InMemoryStore

if TYPE_CHECKING:
    from policy_manager.context import RequestContext
    from policy_manager.policies.base import Policy
    from policy_manager.stores.base import Store


class PolicyManager:
    """Holds an ordered chain of policies and evaluates them against a context.

    Policies execute in **registration order**.  During pre-execution the
    chain short-circuits on the first denial.  The same applies to
    post-execution.

    Parameters:
        store: Persistence backend shared by all policies.  Defaults to
               :class:`InMemoryStore` when omitted.
    """

    def __init__(self, store: Store | None = None) -> None:
        self._store: Store = store or InMemoryStore()
        self._policies: list[Policy] = []

    # ── registration ─────────────────────────────────────────

    async def add_policy(self, policy: Policy) -> None:
        """Append *policy* to the chain and inject the shared store."""
        await policy.setup(self._store)
        self._policies.append(policy)

    # ── evaluation ───────────────────────────────────────────

    async def check_pre_exec_policies(
        self,
        context: RequestContext,
    ) -> PolicyResult:
        """Run every policy's ``pre_execute`` in registration order.

        * First **deny** or **pending** result stops the chain immediately.
        * Returns ``PolicyResult.allow()`` only when *all* policies pass.
        """
        for policy in self._policies:
            result = await policy.pre_execute(context)
            if not result.allowed:
                return result
        return PolicyResult.allow()

    async def check_post_exec_policies(
        self,
        context: RequestContext,
    ) -> PolicyResult:
        """Run every policy's ``post_execute`` in registration order.

        Same short-circuit semantics as the pre-execution chain.
        """
        for policy in self._policies:
            result = await policy.post_execute(context)
            if not result.allowed:
                return result
        return PolicyResult.allow()

    # ── introspection ────────────────────────────────────────

    def get_policy(self, name: str) -> Policy | None:
        """Look up a registered policy by its ``name``."""
        for policy in self._policies:
            if policy.name == name:
                return policy
        return None

    def list_policies(self) -> list[str]:
        """Return the names of all registered policies in chain order."""
        return [p.name for p in self._policies]

    def export(self) -> dict[str, Any]:
        """Return a JSON-serializable snapshot of all registered policies."""
        policies = [p.export() for p in self._policies]
        return {
            "policies": policies,
            "policy_count": len(policies),
        }

    @property
    def store(self) -> Store:
        return self._store
