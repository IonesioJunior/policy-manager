"""Tests for AttributionPolicy."""

import pytest

from policy_manager import RequestContext
from policy_manager.policies import AttributionPolicy


@pytest.fixture
async def attr(store):
    policy = AttributionPolicy(name="attr")
    await policy.setup(store)
    return policy


async def test_deny_no_url(attr):
    ctx = RequestContext(user_id="alice", input={})
    result = await attr.pre_execute(ctx)
    assert not result.allowed


async def test_deny_unknown_url(attr):
    ctx = RequestContext(user_id="alice", input={"attribution_url": "https://example.com"})
    result = await attr.pre_execute(ctx)
    assert not result.allowed


async def test_allow_verified_url(attr):
    await attr.add_verified_url("alice", "https://example.com/credit")
    ctx = RequestContext(user_id="alice", input={"attribution_url": "https://example.com/credit"})
    result = await attr.pre_execute(ctx)
    assert result.allowed
    assert ctx.metadata["attr_verified"] is True


async def test_custom_callback(store):
    async def check(user_id, url):
        return url == "https://good.com"

    policy = AttributionPolicy(name="cb_attr", verify_callback=check)
    await policy.setup(store)

    ctx_good = RequestContext(user_id="u", input={"attribution_url": "https://good.com"})
    assert (await policy.pre_execute(ctx_good)).allowed

    ctx_bad = RequestContext(user_id="u", input={"attribution_url": "https://bad.com"})
    assert not (await policy.pre_execute(ctx_bad)).allowed


async def test_post_execute_passthrough(attr):
    ctx = RequestContext(user_id="u", input={})
    result = await attr.post_execute(ctx)
    assert result.allowed
