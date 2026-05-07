"""Tests for MppAccountingPolicy."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock

import pytest

import policy_manager.policies.mpp_accounting as _mpp_mod
from policy_manager.context import RequestContext
from policy_manager.exceptions import PaymentRequiredError, PolicyConfigError
from policy_manager.policies.mpp_accounting import MppAccountingPolicy, _coerce_price

# ── Fixtures ──────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def mpp_cls(monkeypatch):
    """Simulate pympp being installed for all tests in this module.

    Returns the mock Mpp class so individual tests can configure it.
    """
    mock_cls = MagicMock()
    monkeypatch.setattr(_mpp_mod, "_MPP_AVAILABLE", True)
    monkeypatch.setattr(_mpp_mod, "Mpp", mock_cls, raising=False)
    return mock_cls


@pytest.fixture
async def paid_policy(store):
    p = MppAccountingPolicy(
        name="mpp",
        wallet_address="0xAbc123",
        realm="test-endpoint",
        pricing_tiers=[{"price": 0.05, "applied_to": ["*"]}],
        secret_key="test_secret",
    )
    await p.setup(store)
    return p


@pytest.fixture
def ctx_no_payment():
    return RequestContext(user_id="alice@acme.com", input={"query": "hello"})


@pytest.fixture
def ctx_with_payment():
    return RequestContext(
        user_id="alice@acme.com",
        input={"query": "hello", "x_payment": "cred_abc123"},
    )


# ── Constructor ───────────────────────────────────────────────────


def test_raises_without_pympp(monkeypatch):
    monkeypatch.setattr(_mpp_mod, "_MPP_AVAILABLE", False)
    with pytest.raises(RuntimeError, match="pympp is required"):
        MppAccountingPolicy(name="mpp", wallet_address="0x1", realm="r")


def test_paid_tier_without_secret_raises(monkeypatch):
    """Any tier with price > 0 requires secret_key — fail fast at __init__."""
    monkeypatch.delenv("MPP_SECRET_KEY", raising=False)
    with pytest.raises(PolicyConfigError, match="secret_key"):
        MppAccountingPolicy(
            name="mpp",
            wallet_address="0xAbc",
            realm="r",
            pricing_tiers=[{"price": 0.05, "applied_to": ["*"]}],
        )


def test_free_only_does_not_require_secret(monkeypatch):
    """A policy with only free tiers does not require secret_key."""
    monkeypatch.delenv("MPP_SECRET_KEY", raising=False)
    p = MppAccountingPolicy(
        name="mpp",
        wallet_address="0xAbc",
        realm="r",
        pricing_tiers=[{"price": 0, "applied_to": ["*"]}],
    )
    assert p is not None  # construction succeeded


def test_secret_key_from_env(monkeypatch):
    """MPP_SECRET_KEY env var satisfies the guard."""
    monkeypatch.setenv("MPP_SECRET_KEY", "env_secret")
    p = MppAccountingPolicy(
        name="mpp",
        wallet_address="0xAbc",
        realm="r",
        pricing_tiers=[{"price": 0.05, "applied_to": ["*"]}],
    )
    assert p._secret_key == "env_secret"


# ── Decimal pricing ───────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw, expected",
    [
        (0, Decimal("0")),
        (0.0, Decimal("0")),
        (0.05, Decimal("0.05")),
        ("0.05", Decimal("0.05")),
        (Decimal("0.05"), Decimal("0.05")),
        # The classic float-arithmetic trap: 0.1 must NOT become 0.1000…0055
        (0.1, Decimal("0.1")),
    ],
)
def test_coerce_price_accepts_numeric_types(raw, expected):
    assert _coerce_price(raw) == expected


def test_coerce_price_rejects_bool():
    with pytest.raises(ValueError, match="Boolean"):
        _coerce_price(True)  # type: ignore[arg-type]


def test_coerce_price_rejects_garbage():
    with pytest.raises(ValueError, match="Cannot interpret"):
        _coerce_price("not a number")


async def test_decimal_price_round_trip_through_metadata(store, mpp_cls):
    """A float-configured tier surfaces as a clean ``str(Decimal)`` in metadata."""
    p = MppAccountingPolicy(
        name="mpp",
        wallet_address="0xAbc",
        realm="r",
        pricing_tiers=[{"price": 0.1, "applied_to": ["*"]}],  # the fp-trap
        secret_key="test_secret",
    )
    await p.setup(store)
    mpp_cls.return_value.charge.return_value = ("cred", "receipt")

    ctx = RequestContext(user_id="alice@acme.com", input={"x_payment": "cred"})
    await p.pre_execute(ctx)

    assert ctx.metadata["mpp_price_charged"] == "0.1"


# ── Free tier ─────────────────────────────────────────────────────


async def test_free_tier_allows_without_credential(store):
    p = MppAccountingPolicy(
        name="mpp",
        wallet_address="0xAbc",
        realm="test",
        pricing_tiers=[{"price": 0.0, "applied_to": ["*"]}],
    )
    await p.setup(store)

    ctx = RequestContext(user_id="alice@acme.com", input={})
    result = await p.pre_execute(ctx)
    assert result.allowed


async def test_free_tier_does_not_call_mpp_charge(store, mpp_cls):
    p = MppAccountingPolicy(
        name="mpp",
        wallet_address="0xAbc",
        realm="test",
        pricing_tiers=[{"price": 0.0, "applied_to": ["*"]}],
    )
    await p.setup(store)

    ctx = RequestContext(user_id="alice@acme.com", input={})
    await p.pre_execute(ctx)
    mpp_cls.return_value.charge.assert_not_called()


# ── Tier matching ─────────────────────────────────────────────────


async def test_no_matching_tier_denies(store):
    p = MppAccountingPolicy(
        name="mpp",
        wallet_address="0xAbc",
        realm="test",
        pricing_tiers=[{"price": 0.05, "applied_to": ["admin@*"]}],
        secret_key="test_secret",
    )
    await p.setup(store)

    ctx = RequestContext(user_id="alice@acme.com", input={})
    result = await p.pre_execute(ctx)
    assert not result.allowed
    assert "No pricing tier" in result.reason


async def test_most_specific_tier_wins(store):
    """Exact user match (no wildcard) beats wildcard — price 0.0 wins for alice."""
    p = MppAccountingPolicy(
        name="mpp",
        wallet_address="0xAbc",
        realm="test",
        pricing_tiers=[
            {"price": 0.05, "applied_to": ["*"]},
            {"price": 0.0, "applied_to": ["alice@acme.com"]},
        ],
        secret_key="test_secret",
    )
    await p.setup(store)

    ctx = RequestContext(user_id="alice@acme.com", input={})
    result = await p.pre_execute(ctx)
    assert result.allowed  # free tier selected


async def test_empty_tiers_allows(store):
    p = MppAccountingPolicy(
        name="mpp",
        wallet_address="0xAbc",
        realm="test",
        pricing_tiers=[],
    )
    await p.setup(store)

    ctx = RequestContext(user_id="alice@acme.com", input={})
    result = await p.pre_execute(ctx)
    assert result.allowed


# ── Configuration validation ──────────────────────────────────────


async def test_missing_wallet_denies(store):
    p = MppAccountingPolicy(
        name="mpp",
        wallet_address="",
        realm="test",
        pricing_tiers=[{"price": 0.05, "applied_to": ["*"]}],
        secret_key="test_secret",
    )
    await p.setup(store)

    ctx = RequestContext(user_id="alice@acme.com", input={})
    result = await p.pre_execute(ctx)
    assert not result.allowed
    assert "wallet address" in result.reason


async def test_missing_realm_denies(store):
    p = MppAccountingPolicy(
        name="mpp",
        wallet_address="0xAbc",
        realm="",
        pricing_tiers=[{"price": 0.05, "applied_to": ["*"]}],
        secret_key="test_secret",
    )
    await p.setup(store)

    ctx = RequestContext(user_id="alice@acme.com", input={})
    result = await p.pre_execute(ctx)
    assert not result.allowed
    assert "realm" in result.reason


# ── Payment flow: challenge / credential ──────────────────────────


async def test_challenge_object_raises_payment_required(paid_policy, ctx_no_payment, mpp_cls):
    """mpp.charge() returning a non-tuple (Challenge object) triggers PaymentRequiredError."""
    challenge_obj = MagicMock()
    challenge_obj.www_authenticate = "MPP realm=test-endpoint, challenge=xyz"
    mpp_cls.return_value.charge.return_value = challenge_obj

    with pytest.raises(PaymentRequiredError) as exc_info:
        await paid_policy.pre_execute(ctx_no_payment)

    err = exc_info.value
    assert err.realm == "test-endpoint"
    assert "test-endpoint" in err.challenge or "xyz" in err.challenge


async def test_charge_exception_with_challenge_attr_raises_payment_required(
    paid_policy, ctx_no_payment, mpp_cls
):
    """mpp.charge() raising with a .challenge attribute triggers PaymentRequiredError."""
    exc = Exception("payment required")
    exc.challenge = "MPP realm=test-endpoint, challenge=abc123"  # type: ignore[attr-defined]
    mpp_cls.return_value.charge.side_effect = exc

    with pytest.raises(PaymentRequiredError) as exc_info:
        await paid_policy.pre_execute(ctx_no_payment)

    assert exc_info.value.realm == "test-endpoint"
    assert "abc123" in exc_info.value.challenge


async def test_valid_credential_allows_and_stores_receipt(
    paid_policy, ctx_with_payment, mpp_cls
):
    """mpp.charge() returning (credential, receipt) allows and stores the receipt."""
    mpp_cls.return_value.charge.return_value = ("cred_abc123", "receipt_xyz")

    result = await paid_policy.pre_execute(ctx_with_payment)

    assert result.allowed
    assert ctx_with_payment.metadata["mpp_payment_receipt"] == "receipt_xyz"
    # Price is stored as the string form of the Decimal so it's JSON-safe.
    assert ctx_with_payment.metadata["mpp_price_charged"] == "0.05"


async def test_charge_passes_decimal_string_to_mpp(paid_policy, ctx_with_payment, mpp_cls):
    """The charge() call must receive the price as ``str(Decimal)``, not a float."""
    mpp_cls.return_value.charge.return_value = ("cred", "receipt")

    await paid_policy.pre_execute(ctx_with_payment)

    _, kwargs = mpp_cls.return_value.charge.call_args
    assert kwargs["price"] == "0.05"
    assert isinstance(kwargs["price"], str)


async def test_charge_infrastructure_error_propagates(paid_policy, ctx_with_payment, mpp_cls):
    """A non-challenge exception (network, RPC) must propagate, not deny.

    Silently turning infra errors into policy denials hides real problems and
    lets the orchestrator believe the user was rejected when the chain was
    simply unreachable.
    """
    mpp_cls.return_value.charge.side_effect = RuntimeError("network timeout")

    with pytest.raises(RuntimeError, match="network timeout"):
        await paid_policy.pre_execute(ctx_with_payment)


# ── Post-execute ──────────────────────────────────────────────────


async def test_post_execute_injects_receipt_into_output(paid_policy):
    ctx = RequestContext(
        user_id="alice@acme.com",
        input={},
        output={"answer": "42"},
        metadata={"mpp_payment_receipt": "receipt_xyz"},
    )
    result = await paid_policy.post_execute(ctx)
    assert result.allowed
    assert ctx.output["_payment"]["receipt"] == "receipt_xyz"


async def test_post_execute_no_op_when_no_receipt(paid_policy):
    ctx = RequestContext(user_id="alice@acme.com", input={}, output={})
    result = await paid_policy.post_execute(ctx)
    assert result.allowed
    assert "_payment" not in ctx.output


# ── Mpp instance caching ──────────────────────────────────────────


async def test_mpp_instance_is_reused_across_calls(store, mpp_cls):
    """A single policy instance reuses one Mpp across consecutive requests."""
    p = MppAccountingPolicy(
        name="mpp",
        wallet_address="0xAbc",
        realm="endpoint-1",
        pricing_tiers=[{"price": 0.05, "applied_to": ["*"]}],
        secret_key="test_secret",
    )
    await p.setup(store)

    mpp_cls.return_value.charge.return_value = ("cred", "receipt")
    ctx = RequestContext(user_id="alice@acme.com", input={"x_payment": "cred"})

    await p.pre_execute(ctx)
    await p.pre_execute(ctx)

    mpp_cls.assert_called_once()


async def test_mpp_cache_is_per_instance_not_class_level(store, mpp_cls):
    """Two separate policy instances must NOT share an Mpp cache.

    The previous class-level cache made wallet rotation impossible without
    process restart. With an instance-level cache, each policy owns its
    Mpp objects and ``invalidate_mpp_cache()`` works deterministically.
    """
    p1 = MppAccountingPolicy(
        name="mpp",
        wallet_address="0xAbc",
        realm="ep",
        pricing_tiers=[{"price": 0.05, "applied_to": ["*"]}],
        secret_key="test_secret",
    )
    p2 = MppAccountingPolicy(
        name="mpp",
        wallet_address="0xAbc",
        realm="ep",
        pricing_tiers=[{"price": 0.05, "applied_to": ["*"]}],
        secret_key="test_secret",
    )
    await p1.setup(store)
    await p2.setup(store)

    mpp_cls.return_value.charge.return_value = ("cred", "receipt")
    ctx = RequestContext(user_id="alice@acme.com", input={"x_payment": "cred"})

    await p1.pre_execute(ctx)
    await p2.pre_execute(ctx)

    # Each instance built its own Mpp.
    assert mpp_cls.call_count == 2
    assert not hasattr(MppAccountingPolicy, "_mpp_instances")


async def test_invalidate_mpp_cache_forces_rebuild(store, mpp_cls):
    """Calling invalidate_mpp_cache() drops cached Mpp instances."""
    p = MppAccountingPolicy(
        name="mpp",
        wallet_address="0xAbc",
        realm="ep",
        pricing_tiers=[{"price": 0.05, "applied_to": ["*"]}],
        secret_key="test_secret",
    )
    await p.setup(store)

    mpp_cls.return_value.charge.return_value = ("cred", "receipt")
    ctx = RequestContext(user_id="alice@acme.com", input={"x_payment": "cred"})

    await p.pre_execute(ctx)
    p.invalidate_mpp_cache()
    await p.pre_execute(ctx)

    assert mpp_cls.call_count == 2


# ── Export ────────────────────────────────────────────────────────


async def test_export_shape(paid_policy):
    data = paid_policy.export()

    assert data["name"] == "mpp"
    assert data["type"] == "mpp_accounting"
    assert data["version"] == "1.0"
    assert data["enabled"] is True
    assert "description" in data
    assert set(data["phase"]) == {"pre", "post"}
    assert data["config"]["wallet_address"] == "0xAbc123"
    assert data["config"]["realm"] == "test-endpoint"
    assert data["config"]["testnet"] is True
    assert data["config"]["has_secret_key"] is True
    # Prices export as strings (Decimal-preserving, JSON-safe).
    assert data["config"]["pricing_tiers"] == [{"price": "0.05", "applied_to": ["*"]}]


async def test_export_no_wallet_or_secret(store):
    p = MppAccountingPolicy(
        name="mpp",
        wallet_address="",
        realm="ep",
        pricing_tiers=[{"price": 0.0, "applied_to": ["*"]}],
    )
    await p.setup(store)
    data = p.export()

    assert data["config"]["wallet_address"] is None
    assert data["config"]["has_secret_key"] is False
