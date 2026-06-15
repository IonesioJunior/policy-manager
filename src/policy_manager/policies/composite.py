"""Composite policies — boolean combinators for composing policies."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from policy_manager.policies.base import Policy
from policy_manager.result import PolicyResult

if TYPE_CHECKING:
    from policy_manager.context import RequestContext
    from policy_manager.stores.base import Store


class AllOf(Policy):
    """Passes only if **all** child policies pass.  Short-circuits on first denial."""

    _policy_type = "all_of"
    _policy_description = "Composite policy requiring all child policies to pass"

    def __init__(self, *policies: Policy, name: str = "") -> None:
        self._policies = list(policies)
        self._name = name or f"all_of({','.join(p.name for p in self._policies)})"

    @property
    def name(self) -> str:
        return self._name

    def export(self) -> dict[str, Any]:
        data = super().export()
        data["config"] = {
            "operator": "all_of",
            "policies": [p.export() for p in self._policies],
        }
        return data

    async def setup(self, store: Store) -> None:
        await super().setup(store)
        for p in self._policies:
            await p.setup(store)

    async def pre_execute(self, context: RequestContext) -> PolicyResult:
        for p in self._policies:
            result = await p.pre_execute(context)
            if not result.allowed:
                return result
        return PolicyResult.allow(self.name)

    async def post_execute(self, context: RequestContext) -> PolicyResult:
        for p in self._policies:
            result = await p.post_execute(context)
            # Stop on the first terminal result — a denial or a substitution.
            # A substitution must be returned as-is: collapsing it to a plain
            # allow at the end of the loop would drop the substituted flag and
            # body, so the executor would fall back to delivering the real
            # handler output (a leak). Treating substitution as terminal also
            # keeps AllOf identical to the flat PolicyManager chain, which uses
            # the same predicate — a later child's denial is intentionally NOT
            # evaluated once a child has substituted (the substituting policy,
            # e.g. manual_review, is responsible for withholding the real
            # output). See test_allof_substitution_short_circuits_denial.
            if result.is_terminal():
                return result
        return PolicyResult.allow(self.name)


class AnyOf(Policy):
    """Passes if **at least one** child policy passes."""

    _policy_type = "any_of"
    _policy_description = "Composite policy requiring at least one child policy to pass"

    def __init__(self, *policies: Policy, name: str = "") -> None:
        self._policies = list(policies)
        self._name = name or f"any_of({','.join(p.name for p in self._policies)})"

    @property
    def name(self) -> str:
        return self._name

    def export(self) -> dict[str, Any]:
        data = super().export()
        data["config"] = {
            "operator": "any_of",
            "policies": [p.export() for p in self._policies],
        }
        return data

    async def setup(self, store: Store) -> None:
        await super().setup(store)
        for p in self._policies:
            await p.setup(store)

    async def _evaluate(
        self,
        context: RequestContext,
        method: str,
    ) -> PolicyResult:
        last_denial: PolicyResult | None = None
        for p in self._policies:
            result = await getattr(p, method)(context)
            if result.allowed:
                # First passing child wins (short-circuit) — OR semantics: once
                # any child is satisfied the composite is satisfied, so later
                # children (including a hold/substitution that comes after a
                # plain pass) are intentionally not evaluated. Preserve the
                # winning child's substitution so its replaced body (e.g.
                # manual_review's placeholder) is not discarded; a plain pass
                # collapses to the composite's own identity.
                return result if result.substituted else PolicyResult.allow(self.name)
            last_denial = result

        return last_denial or PolicyResult.deny(self.name, "No child policies configured")

    async def pre_execute(self, context: RequestContext) -> PolicyResult:
        return await self._evaluate(context, "pre_execute")

    async def post_execute(self, context: RequestContext) -> PolicyResult:
        return await self._evaluate(context, "post_execute")


class Not(Policy):
    """Inverts a policy's result — allow becomes deny and vice versa."""

    _policy_type = "not"
    _policy_description = "Composite policy that inverts child policy result"

    def __init__(self, policy: Policy, *, name: str = "", deny_reason: str = "") -> None:
        self._policy = policy
        self._name = name or f"not({policy.name})"
        self._deny_reason = (
            deny_reason or f"Inverted policy '{policy.name}' passed (expected denial)"
        )

    @property
    def name(self) -> str:
        return self._name

    def export(self) -> dict[str, Any]:
        data = super().export()
        data["config"] = {
            "operator": "not",
            "policy": self._policy.export(),
            "deny_reason": self._deny_reason,
        }
        return data

    async def setup(self, store: Store) -> None:
        await super().setup(store)
        await self._policy.setup(store)

    def _invert(self, result: PolicyResult) -> PolicyResult:
        # A pending child has not reached a verdict yet — there is nothing to
        # invert. Preserve the pending state instead of fabricating a clean
        # allow, which would silently convert an awaiting-resolution hold into
        # an unconditional pass.
        if result.pending:
            return result
        if result.allowed:
            return PolicyResult.deny(self.name, self._deny_reason)
        return PolicyResult.allow(self.name)

    async def pre_execute(self, context: RequestContext) -> PolicyResult:
        return self._invert(await self._policy.pre_execute(context))

    async def post_execute(self, context: RequestContext) -> PolicyResult:
        return self._invert(await self._policy.post_execute(context))
