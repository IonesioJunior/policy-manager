"""Tests for AccessGroupPolicy."""

import pytest

from policy_manager import RequestContext
from policy_manager.policies import AccessGroupPolicy


@pytest.fixture
async def ag(store):
    policy = AccessGroupPolicy(
        name="eng",
        owner="admin@acme.com",
        users=["alice@acme.com", "bob@acme.com"],
        documents=["doc_a", "doc_b"],
    )
    await policy.setup(store)
    return policy


async def test_allow_member(ag, alice_ctx):
    result = await ag.pre_execute(alice_ctx)
    assert result.allowed
    assert alice_ctx.metadata["resolved_documents"] == ["doc_a", "doc_b"]


async def test_deny_non_member(ag, unknown_ctx):
    result = await ag.pre_execute(unknown_ctx)
    assert not result.allowed
    assert "eve@external.com" in result.reason


async def test_add_users(ag, store):
    ctx = RequestContext(user_id="charlie@acme.com", input={})
    result = await ag.pre_execute(ctx)
    assert not result.allowed

    await ag.add_users(["charlie@acme.com"])
    result = await ag.pre_execute(ctx)
    assert result.allowed


async def test_remove_users(ag, alice_ctx):
    await ag.remove_users(["alice@acme.com"])
    result = await ag.pre_execute(alice_ctx)
    assert not result.allowed


async def test_add_documents(ag, alice_ctx):
    await ag.add_documents(["doc_c"])
    result = await ag.pre_execute(alice_ctx)
    assert result.allowed
    assert "doc_c" in alice_ctx.metadata["resolved_documents"]


async def test_remove_documents(ag, alice_ctx):
    await ag.remove_documents(["doc_b"])
    result = await ag.pre_execute(alice_ctx)
    assert result.allowed
    assert "doc_b" not in alice_ctx.metadata["resolved_documents"]


async def test_documents_accumulate_across_policies(store, alice_ctx):
    ag1 = AccessGroupPolicy(name="g1", users=["alice@acme.com"], documents=["doc_1"])
    ag2 = AccessGroupPolicy(name="g2", users=["alice@acme.com"], documents=["doc_2"])
    await ag1.setup(store)
    await ag2.setup(store)

    await ag1.pre_execute(alice_ctx)
    await ag2.pre_execute(alice_ctx)
    assert alice_ctx.metadata["resolved_documents"] == ["doc_1", "doc_2"]


async def test_documents_deduplicate(store, alice_ctx):
    ag1 = AccessGroupPolicy(name="g1", users=["alice@acme.com"], documents=["doc_a"])
    ag2 = AccessGroupPolicy(name="g2", users=["alice@acme.com"], documents=["doc_a", "doc_b"])
    await ag1.setup(store)
    await ag2.setup(store)

    await ag1.pre_execute(alice_ctx)
    await ag2.pre_execute(alice_ctx)
    assert alice_ctx.metadata["resolved_documents"] == ["doc_a", "doc_b"]


async def test_post_execute_passthrough(ag, alice_ctx):
    result = await ag.post_execute(alice_ctx)
    assert result.allowed


async def test_get_users_and_documents(ag):
    assert "alice@acme.com" in ag.get_users()
    assert "doc_a" in ag.get_documents()
