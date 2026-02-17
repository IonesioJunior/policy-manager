"""Tests for TokenLimitPolicy."""

import pytest

from policy_manager import RequestContext
from policy_manager.policies import TokenLimitPolicy


@pytest.fixture
async def tl(store):
    policy = TokenLimitPolicy(
        name="tl",
        max_input_tokens=20,
        max_output_tokens=50,
    )
    await policy.setup(store)
    return policy


async def test_pre_allows_short_input(tl):
    ctx = RequestContext(user_id="u", input={"query": "short"})
    result = await tl.pre_execute(ctx)
    assert result.allowed


async def test_pre_denies_long_input(tl):
    ctx = RequestContext(user_id="u", input={"query": "x" * 21})
    result = await tl.pre_execute(ctx)
    assert not result.allowed
    assert "Input tokens" in result.reason


async def test_post_allows_short_output(tl):
    ctx = RequestContext(user_id="u", input={}, output={"response": "ok"})
    result = await tl.post_execute(ctx)
    assert result.allowed


async def test_post_denies_long_output(tl):
    ctx = RequestContext(user_id="u", input={}, output={"response": "x" * 51})
    result = await tl.post_execute(ctx)
    assert not result.allowed
    assert "Output tokens" in result.reason


async def test_no_limit_set(store):
    policy = TokenLimitPolicy(name="uncapped")
    await policy.setup(store)
    ctx = RequestContext(user_id="u", input={"query": "x" * 10000})
    assert (await policy.pre_execute(ctx)).allowed
    ctx.output = {"response": "y" * 10000}
    assert (await policy.post_execute(ctx)).allowed


async def test_custom_counter(store):
    policy = TokenLimitPolicy(
        name="custom",
        max_input_tokens=5,
        token_counter=lambda s: len(s.split()),
    )
    await policy.setup(store)
    ctx = RequestContext(user_id="u", input={"query": "one two three"})
    assert (await policy.pre_execute(ctx)).allowed

    ctx2 = RequestContext(user_id="u", input={"query": "one two three four five six"})
    result = await policy.pre_execute(ctx2)
    assert not result.allowed


async def test_metadata_populated(tl):
    ctx = RequestContext(user_id="u", input={"query": "hello"})
    await tl.pre_execute(ctx)
    assert ctx.metadata["tl_input_tokens"] == 5
