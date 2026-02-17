"""Tests for CustomPolicy."""

import pytest

from policy_manager import RequestContext
from policy_manager.policies import CustomPolicy


@pytest.fixture
async def cp_pre(store):
    policy = CustomPolicy(
        name="age_check",
        phase="pre",
        check=lambda ctx: ctx.input.get("age", 0) >= 18,
        deny_reason="Must be 18+",
    )
    await policy.setup(store)
    return policy


async def test_pre_allow(cp_pre):
    ctx = RequestContext(user_id="u", input={"age": 21})
    result = await cp_pre.pre_execute(ctx)
    assert result.allowed


async def test_pre_deny(cp_pre):
    ctx = RequestContext(user_id="u", input={"age": 15})
    result = await cp_pre.pre_execute(ctx)
    assert not result.allowed
    assert "18+" in result.reason


async def test_pre_phase_skips_post(cp_pre):
    ctx = RequestContext(user_id="u", input={"age": 15})
    result = await cp_pre.post_execute(ctx)
    assert result.allowed  # pre-only policy passes through on post


async def test_post_phase(store):
    policy = CustomPolicy(
        name="length",
        phase="post",
        check=lambda ctx: len(ctx.output.get("response", "")) < 100,
        deny_reason="Response too long",
    )
    await policy.setup(store)

    ctx = RequestContext(user_id="u", input={}, output={"response": "short"})
    assert (await policy.post_execute(ctx)).allowed

    ctx2 = RequestContext(user_id="u", input={}, output={"response": "x" * 200})
    assert not (await policy.post_execute(ctx2)).allowed


async def test_both_phase(store):
    policy = CustomPolicy(
        name="both",
        phase="both",
        check=lambda ctx: True,
        deny_reason="fail",
    )
    await policy.setup(store)
    ctx = RequestContext(user_id="u", input={})
    assert (await policy.pre_execute(ctx)).allowed
    assert (await policy.post_execute(ctx)).allowed


async def test_async_check(store):
    async def async_check(ctx):
        return ctx.input.get("flag", False)

    policy = CustomPolicy(name="async", phase="pre", check=async_check, deny_reason="no flag")
    await policy.setup(store)

    ctx_ok = RequestContext(user_id="u", input={"flag": True})
    assert (await policy.pre_execute(ctx_ok)).allowed

    ctx_fail = RequestContext(user_id="u", input={"flag": False})
    assert not (await policy.pre_execute(ctx_fail)).allowed
