"""Tests for BundleSubscriptionPolicy."""

import pytest

from policy_manager import RequestContext
from policy_manager.policies import BundleSubscriptionPolicy


@pytest.fixture
async def policy(store):
    p = BundleSubscriptionPolicy(
        name="pro",
        users=["alice@acme.com", "bob@acme.com"],
        plan_name="Pro",
        price=29.99,
        currency="USD",
        billing_cycle="monthly",
        invoice_url="https://billing.example.com/subscribe",
    )
    await p.setup(store)
    return p


async def test_allow_subscriber(policy, alice_ctx):
    result = await policy.pre_execute(alice_ctx)
    assert result.allowed


async def test_deny_non_subscriber(policy, unknown_ctx):
    result = await policy.pre_execute(unknown_ctx)
    assert not result.allowed
    assert "eve@external.com" in result.reason
    assert "Pro" in result.reason


async def test_deny_message_without_plan_name(store, unknown_ctx):
    p = BundleSubscriptionPolicy(name="basic", users=[])
    await p.setup(store)
    result = await p.pre_execute(unknown_ctx)
    assert not result.allowed
    assert "eve@external.com" in result.reason


async def test_add_users(policy, store):
    ctx = RequestContext(user_id="charlie@acme.com", input={})
    result = await policy.pre_execute(ctx)
    assert not result.allowed

    await policy.add_users(["charlie@acme.com"])
    result = await policy.pre_execute(ctx)
    assert result.allowed


async def test_remove_users(policy, alice_ctx):
    await policy.remove_users(["alice@acme.com"])
    result = await policy.pre_execute(alice_ctx)
    assert not result.allowed


async def test_get_users(policy):
    users = policy.get_users()
    assert "alice@acme.com" in users
    assert "bob@acme.com" in users


async def test_store_persistence(store):
    """Subscriber list written to store on setup is loaded back on demand."""
    p1 = BundleSubscriptionPolicy(name="plan", users=["alice@acme.com"])
    await p1.setup(store)

    # New instance pointing at the same store, no initial users
    p2 = BundleSubscriptionPolicy(name="plan", users=[])
    await p2.setup(store)  # overwrites with empty list — expected

    # Simulate a policy that hasn't been synced (load-from-store path)
    p3 = BundleSubscriptionPolicy(name="plan", users=["alice@acme.com"])
    p3.store = store  # inject store without calling setup
    p3._synced = False  # type: ignore[attr-defined]
    ctx = RequestContext(user_id="alice@acme.com", input={})
    result = await p3.pre_execute(ctx)
    # p2 wrote an empty list to the store, so alice is not found
    assert not result.allowed


async def test_post_execute_passthrough(policy, alice_ctx):
    result = await policy.post_execute(alice_ctx)
    assert result.allowed


async def test_export_shape(policy):
    data = policy.export()
    assert data["type"] == "bundle_subscription"
    assert data["config"]["plan_name"] == "Pro"
    assert data["config"]["price"] == 29.99
    assert data["config"]["currency"] == "USD"
    assert data["config"]["billing_cycle"] == "monthly"
    assert data["config"]["invoice_url"] == "https://billing.example.com/subscribe"
    # Subscriber list is internal — must NOT appear in the export
    assert "users" not in data["config"]


async def test_export_defaults(store):
    p = BundleSubscriptionPolicy(name="basic")
    await p.setup(store)
    data = p.export()
    assert data["config"]["plan_name"] == ""
    assert data["config"]["price"] == 0.0
    assert data["config"]["currency"] == "USD"
    assert data["config"]["billing_cycle"] == ""
    assert data["config"]["invoice_url"] == ""
