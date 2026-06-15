"""Tests for composite policies: AllOf, AnyOf, Not."""

import pytest

from policy_manager import RequestContext
from policy_manager.policies import AllOf, AnyOf, CustomPolicy, Not
from policy_manager.policies.base import Policy
from policy_manager.result import PolicyResult


def _allow_policy(name="ok"):
    return CustomPolicy(name=name, phase="both", check=lambda ctx: True, deny_reason="")


def _deny_policy(name="no", reason="denied"):
    return CustomPolicy(name=name, phase="both", check=lambda ctx: False, deny_reason=reason)


class _SubstitutingPolicy(Policy):
    """A minimal post-execution policy that replaces the response body — the
    shape ManualReviewPolicy uses to hold a reply (allowed, but substituted)."""

    _policy_type = "custom"

    def __init__(self, name="sub", body="PLACEHOLDER"):
        self._name = name
        self._body = body

    @property
    def name(self):
        return self._name

    async def post_execute(self, context):
        return PolicyResult.substitute(
            self.name, output=self._body, reason="held for review", review_id="rid-1"
        )


@pytest.fixture
def ctx():
    return RequestContext(user_id="u", input={})


# ── AllOf ────────────────────────────────────────────────────


async def test_allof_all_pass(store, ctx):
    policy = AllOf(_allow_policy("a"), _allow_policy("b"))
    await policy.setup(store)
    assert (await policy.pre_execute(ctx)).allowed
    assert (await policy.post_execute(ctx)).allowed


async def test_allof_one_fails(store, ctx):
    policy = AllOf(_allow_policy("a"), _deny_policy("b"))
    await policy.setup(store)
    result = await policy.pre_execute(ctx)
    assert not result.allowed
    assert result.policy_name == "b"


async def test_allof_short_circuits(store, ctx):
    """If first denies, second should not run (no side effects)."""
    calls = []

    async def tracking_check(c):
        calls.append("called")
        return True

    p1 = _deny_policy("first")
    p2 = CustomPolicy(name="second", phase="both", check=tracking_check, deny_reason="")

    policy = AllOf(p1, p2)
    await policy.setup(store)
    await policy.pre_execute(ctx)
    assert len(calls) == 0


# ── AnyOf ────────────────────────────────────────────────────


async def test_anyof_one_passes(store, ctx):
    policy = AnyOf(_deny_policy("a"), _allow_policy("b"))
    await policy.setup(store)
    result = await policy.pre_execute(ctx)
    assert result.allowed


async def test_anyof_all_fail(store, ctx):
    policy = AnyOf(_deny_policy("a"), _deny_policy("b"))
    await policy.setup(store)
    result = await policy.pre_execute(ctx)
    assert not result.allowed


async def test_anyof_no_policies(store, ctx):
    policy = AnyOf()
    await policy.setup(store)
    result = await policy.pre_execute(ctx)
    assert not result.allowed


# ── Not ──────────────────────────────────────────────────────


async def test_not_inverts_allow(store, ctx):
    policy = Not(_allow_policy())
    await policy.setup(store)
    result = await policy.pre_execute(ctx)
    assert not result.allowed


async def test_not_inverts_deny(store, ctx):
    policy = Not(_deny_policy())
    await policy.setup(store)
    result = await policy.pre_execute(ctx)
    assert result.allowed


async def test_not_works_on_post(store, ctx):
    policy = Not(_allow_policy())
    await policy.setup(store)
    result = await policy.post_execute(ctx)
    assert not result.allowed


# ── substitution propagation (regression) ───────────────────
#
# A substituting post policy (e.g. manual_review) returns allowed=True with
# substituted=True. Composites used to branch only on `allowed`, so they
# collapsed the result to a plain allow — discarding the placeholder and
# leaking the real handler output. These pin the fix.


async def test_allof_propagates_substitution(store, ctx):
    policy = AllOf(_allow_policy("a"), _SubstitutingPolicy("review"))
    await policy.setup(store)
    result = await policy.post_execute(ctx)
    assert result.allowed
    assert result.substituted, "AllOf must not discard a child's substitution"
    assert result.output == "PLACEHOLDER"
    assert result.policy_name == "review"


async def test_allof_substitution_is_terminal(store, ctx):
    """A substituting child stops the chain — later children never run, just as
    PolicyManager.check_post_exec_policies stops on a substitution."""
    calls = []

    class _Tracking(Policy):
        _policy_type = "custom"

        @property
        def name(self):
            return "after"

        async def post_execute(self, context):
            calls.append("called")
            return PolicyResult.allow(self.name)

    policy = AllOf(_SubstitutingPolicy("review"), _Tracking())
    await policy.setup(store)
    result = await policy.post_execute(ctx)
    assert result.substituted
    assert calls == [], "policies after a substitution must not run"


async def test_anyof_preserves_substitution(store, ctx):
    # First child denies, the substituting child then "passes" with a replaced
    # body — AnyOf must surface that substitution, not a bare allow.
    policy = AnyOf(_deny_policy("a"), _SubstitutingPolicy("review"))
    await policy.setup(store)
    result = await policy.post_execute(ctx)
    assert result.allowed
    assert result.substituted
    assert result.output == "PLACEHOLDER"
    # AnyOf returns the winning child's result verbatim, so its provenance
    # (policy_name) is the child's, not the composite's — pin that contract.
    assert result.policy_name == "review"


async def test_allof_substitution_short_circuits_denial(store, ctx):
    """A substituting child is terminal: AllOf returns it and does NOT evaluate
    a later child that would deny. This mirrors the flat PolicyManager post
    chain (test_post_chain_short_circuits_on_substitution) — the two chains
    terminate identically. The substituting policy (e.g. manual_review) is
    responsible for withholding the real handler output, so the bypassed
    denial does not leak anything."""
    deny_ran = []

    class _TrackingDeny(Policy):
        _policy_type = "custom"

        @property
        def name(self):
            return "deny_after"

        async def post_execute(self, context):
            deny_ran.append("called")
            return PolicyResult.deny(self.name, "would block")

    policy = AllOf(_SubstitutingPolicy("review"), _TrackingDeny())
    await policy.setup(store)
    result = await policy.post_execute(ctx)
    assert result.allowed
    assert result.substituted
    assert result.output == "PLACEHOLDER"
    assert deny_ran == [], "a denial after a substitution is intentionally not evaluated"


async def test_anyof_plain_pass_collapses_to_identity(store, ctx):
    # A non-substituting pass still collapses to the composite's identity.
    policy = AnyOf(_allow_policy("a"), _SubstitutingPolicy("review"), name="either")
    await policy.setup(store)
    result = await policy.post_execute(ctx)
    assert result.allowed
    assert not result.substituted
    assert result.policy_name == "either"


async def test_not_preserves_pending(store, ctx):
    """Inverting a pending (awaiting-resolution) child must not fabricate a
    clean allow — the undecided state is preserved, so a held request is not
    silently turned into an unconditional pass."""

    class _PendingPolicy(Policy):
        _policy_type = "custom"

        @property
        def name(self):
            return "held"

        async def post_execute(self, context):
            return PolicyResult.pend(self.name, "awaiting review")

    policy = Not(_PendingPolicy(), name="not_held")
    await policy.setup(store)
    result = await policy.post_execute(ctx)
    assert not result.allowed
    assert result.pending, "Not must preserve a pending child's undecided state"
    assert result.policy_name == "held"
