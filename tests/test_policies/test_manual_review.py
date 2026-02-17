"""Tests for ManualReviewPolicy."""

import pytest

from policy_manager import RequestContext
from policy_manager.policies import ManualReviewPolicy


@pytest.fixture
async def mr(store):
    policy = ManualReviewPolicy(name="mr")
    await policy.setup(store)
    return policy


async def test_post_returns_pending(mr):
    ctx = RequestContext(user_id="alice", input={"query": "q"}, output={"response": "r"})
    result = await mr.post_execute(ctx)
    assert not result.allowed
    assert result.pending
    assert "review_id" in result.metadata


async def test_pre_execute_passthrough(mr, alice_ctx):
    result = await mr.pre_execute(alice_ctx)
    assert result.allowed


async def test_approve(mr):
    ctx = RequestContext(user_id="alice", input={}, output={"response": "r"})
    result = await mr.post_execute(ctx)
    review_id = result.metadata["review_id"]

    assert await mr.approve(review_id)

    entry = await mr.store.get(mr.namespace, review_id)
    assert entry["status"] == "approved"


async def test_reject(mr):
    ctx = RequestContext(user_id="alice", input={}, output={"response": "r"})
    result = await mr.post_execute(ctx)
    review_id = result.metadata["review_id"]

    assert await mr.reject(review_id, reason="inappropriate")

    entry = await mr.store.get(mr.namespace, review_id)
    assert entry["status"] == "rejected"
    assert entry["reject_reason"] == "inappropriate"


async def test_get_pending(mr):
    ctx1 = RequestContext(user_id="a", input={}, output={"response": "r1"})
    ctx2 = RequestContext(user_id="b", input={}, output={"response": "r2"})
    await mr.post_execute(ctx1)
    await mr.post_execute(ctx2)

    pending = await mr.get_pending()
    assert len(pending) == 2


async def test_auto_approve_callback(store):
    async def auto_approve(payload):
        return {"approved": True}

    policy = ManualReviewPolicy(name="auto_mr", review_callback=auto_approve)
    await policy.setup(store)

    ctx = RequestContext(user_id="alice", input={}, output={"response": "r"})
    result = await policy.post_execute(ctx)
    assert result.allowed


async def test_approve_nonexistent(mr):
    assert not await mr.approve("nonexistent")


async def test_reject_nonexistent(mr):
    assert not await mr.reject("nonexistent")
