"""Tests for composite policies: AllOf, AnyOf, Not."""

import pytest

from policy_manager import RequestContext
from policy_manager.policies import AllOf, AnyOf, CustomPolicy, Not


def _allow_policy(name="ok"):
    return CustomPolicy(name=name, phase="both", check=lambda ctx: True, deny_reason="")


def _deny_policy(name="no", reason="denied"):
    return CustomPolicy(name=name, phase="both", check=lambda ctx: False, deny_reason=reason)


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
