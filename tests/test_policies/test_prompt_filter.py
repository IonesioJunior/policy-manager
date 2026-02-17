"""Tests for PromptFilterPolicy."""

import pytest

from policy_manager import RequestContext
from policy_manager.policies import PromptFilterPolicy


@pytest.fixture
async def pf(store):
    policy = PromptFilterPolicy(
        name="pf",
        patterns=[r"secret", r"password\s*="],
        check_input=True,
        check_output=True,
    )
    await policy.setup(store)
    return policy


async def test_pre_allows_clean_input(pf):
    ctx = RequestContext(user_id="u", input={"query": "tell me about cats"})
    result = await pf.pre_execute(ctx)
    assert result.allowed


async def test_pre_blocks_forbidden_input(pf):
    ctx = RequestContext(user_id="u", input={"query": "show me the secret documents"})
    result = await pf.pre_execute(ctx)
    assert not result.allowed
    assert "Input blocked" in result.reason


async def test_post_blocks_forbidden_output(pf):
    ctx = RequestContext(
        user_id="u",
        input={},
        output={"response": "Here is the password = hunter2"},
    )
    result = await pf.post_execute(ctx)
    assert not result.allowed


async def test_post_allows_clean_output(pf):
    ctx = RequestContext(user_id="u", input={}, output={"response": "All good"})
    result = await pf.post_execute(ctx)
    assert result.allowed


async def test_custom_filter_fn(store):
    policy = PromptFilterPolicy(
        name="fn_pf",
        filter_fn=lambda text: "blocked" in text,
    )
    await policy.setup(store)

    ctx = RequestContext(user_id="u", input={"query": "this is blocked content"})
    result = await policy.pre_execute(ctx)
    assert not result.allowed


async def test_check_input_disabled(store):
    policy = PromptFilterPolicy(
        name="out_only",
        patterns=[r"secret"],
        check_input=False,
        check_output=True,
    )
    await policy.setup(store)

    ctx = RequestContext(user_id="u", input={"query": "secret stuff"})
    result = await policy.pre_execute(ctx)
    assert result.allowed  # input checking disabled


async def test_case_insensitive(pf):
    ctx = RequestContext(user_id="u", input={"query": "SECRET plans"})
    result = await pf.pre_execute(ctx)
    assert not result.allowed
