"""Tests for X402PayPerRequestPolicy."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from policy_manager import RequestContext
from policy_manager.policies import X402PayPerRequestPolicy
from policy_manager.runner.factory import PolicyFactory
from policy_manager.runner.schema import PolicyConfigSchema


def _make_policy(
    tmp_path,
    *,
    name: str = "x402",
    allow_listed_payers: list[str] | None = None,
    max_pending_settlements_per_payer: int = 16,
    challenge_ttl_seconds: int = 300,
    price: str = "0.01",
) -> X402PayPerRequestPolicy:
    p = X402PayPerRequestPolicy(
        name=name,
        pay_to="0xPayTo",
        price=price,
        currency="0xCurrency",
        decimals=6,
        chain_id=42431,
        realm="syfthub:endpoint:test:x402",
        hmac_secret_kid="default-kid",
        challenge_ttl_seconds=challenge_ttl_seconds,
        max_pending_settlements_per_payer=max_pending_settlements_per_payer,
        allow_listed_payers=allow_listed_payers,
    )
    # Test owns the DB file; avoid leaking into the working dir.
    p._db_path = str(tmp_path / f"{name}.db")
    return p


@pytest.fixture
async def policy(store, tmp_path):
    p = _make_policy(tmp_path)
    await p.setup(store)
    yield p
    await p.close()


# ── pre_execute: round 1 (no credential) ──────────────────────


async def test_pre_execute_without_credential_returns_pending_with_spec_and_no_db_write(
    policy,
):
    ctx = RequestContext(user_id="alice", input={"q": 1})
    result = await policy.pre_execute(ctx)

    assert not result.allowed
    assert result.pending
    assert result.policy_name == policy.name
    assert result.reason == "payment_required"

    spec = result.metadata["x402_challenge_spec"]
    assert spec["pay_to"] == "0xPayTo"
    assert spec["currency"] == "0xCurrency"
    assert spec["decimals"] == 6
    assert spec["chain_id"] == 42431
    # 0.01 USDC at 6 decimals -> "10000" base units
    assert spec["amount"] == "10000"
    assert spec["realm"] == "syfthub:endpoint:test:x402"
    # expires_at must parse and be in the future.
    parsed = datetime.fromisoformat(spec["expires_at_iso"])
    assert parsed > datetime.now(UTC)

    # The secret kid must NOT leak into the spec sent to the consumer.
    assert "hmac_secret_kid" not in spec
    # No challenge id is invented by Python.
    assert "challenge_id_hint" not in spec
    assert "challenge_id" not in spec

    # Critical: no DB row was written on the first call.
    rows = await policy.list_transactions()
    assert rows == []


async def test_pre_execute_with_allow_listed_payer_returns_allow_and_no_db_write(
    store, tmp_path
):
    p = _make_policy(tmp_path, allow_listed_payers=["vip@example.com"])
    await p.setup(store)
    try:
        ctx = RequestContext(user_id="vip@example.com", input={})
        result = await p.pre_execute(ctx)
        assert result.allowed
        assert not result.pending
        assert result.metadata == {}
        # allow-listed payer must NOT create a row.
        rows = await p.list_transactions()
        assert rows == []
    finally:
        await p.close()


# ── pre_execute: round 2 (verified credential) ────────────────


async def test_pre_execute_with_payment_verified_inserts_verified_row_with_canonical_id(
    policy,
):
    canonical_id = "canon-1234567890abcdef"
    ctx = RequestContext(
        user_id="alice",
        input={},
        metadata={
            "payment_verified": True,
            "payment_nonce": 42,
            "payment_challenge_id": canonical_id,
        },
    )
    result = await policy.pre_execute(ctx)

    assert result.allowed
    assert not result.pending
    # The settlement id surfaced to the caller IS the canonical id.
    assert result.metadata["x402_settlement_id"] == canonical_id

    row = await policy.get_transaction(canonical_id)
    assert row is not None
    assert row["id"] == canonical_id
    assert row["payer"] == "alice"
    assert row["status"] == "verified"
    assert row["nonce"] == 42
    assert row["amount"] == "10000"
    assert row["pay_to"] == "0xPayTo"
    assert row["currency"] == "0xCurrency"
    assert row["chain_id"] == 42431
    assert row["tx_hash"] is None
    assert row["settled_at"] is None


async def test_pre_execute_with_payment_verified_but_missing_challenge_id_denies(
    policy,
):
    ctx = RequestContext(
        user_id="alice",
        input={},
        metadata={"payment_verified": True, "payment_nonce": 1},
    )
    result = await policy.pre_execute(ctx)
    assert not result.allowed
    assert not result.pending
    assert "payment_challenge_id" in result.reason
    # No row was inserted.
    assert await policy.list_transactions() == []


async def test_pre_execute_enforces_max_pending_settlements_per_payer(
    store, tmp_path
):
    p = _make_policy(tmp_path, max_pending_settlements_per_payer=2)
    await p.setup(store)
    try:
        # Seed two verified rows for alice via the public path.
        for i in range(2):
            r = await p.pre_execute(
                RequestContext(
                    user_id="alice",
                    input={},
                    metadata={
                        "payment_verified": True,
                        "payment_nonce": i,
                        "payment_challenge_id": f"canon-{i}",
                    },
                )
            )
            assert r.allowed

        # Third verified credential for alice hits the cap → DENY.
        third = await p.pre_execute(
            RequestContext(
                user_id="alice",
                input={},
                metadata={
                    "payment_verified": True,
                    "payment_nonce": 99,
                    "payment_challenge_id": "canon-99",
                },
            )
        )
        assert not third.allowed
        assert not third.pending
        assert "unsettled" in third.reason.lower()
        assert third.metadata["pending_settlements"] == 2
        assert third.metadata["max_pending_settlements_per_payer"] == 2

        # And the third row is NOT inserted.
        assert await p.get_transaction("canon-99") is None

        # Other payers are unaffected.
        bob = await p.pre_execute(
            RequestContext(
                user_id="bob",
                input={},
                metadata={
                    "payment_verified": True,
                    "payment_nonce": 1,
                    "payment_challenge_id": "canon-bob-1",
                },
            )
        )
        assert bob.allowed

        # Round-1 (no credential) is also unaffected by the cap.
        spec_call = await p.pre_execute(RequestContext(user_id="alice", input={}))
        assert spec_call.pending
    finally:
        await p.close()


async def test_replay_same_challenge_id_is_idempotent(policy):
    """Re-presenting the same canonical id must not create a second row
    and must not consume a settlement slot.
    """
    meta = {
        "payment_verified": True,
        "payment_nonce": 7,
        "payment_challenge_id": "canon-replay",
    }
    first = await policy.pre_execute(RequestContext(user_id="alice", input={}, metadata=meta))
    second = await policy.pre_execute(
        RequestContext(user_id="alice", input={}, metadata=meta)
    )

    assert first.allowed
    assert second.allowed
    assert first.metadata["x402_settlement_id"] == "canon-replay"
    assert second.metadata["x402_settlement_id"] == "canon-replay"

    rows = await policy.list_transactions(payer="alice")
    assert len(rows) == 1
    assert rows[0]["id"] == "canon-replay"


# ── post_execute ───────────────────────────────────────────────


async def test_post_execute_with_receipt_updates_row_to_settled(policy):
    canonical_id = "canon-settle-1"
    await policy.pre_execute(
        RequestContext(
            user_id="alice",
            input={},
            metadata={
                "payment_verified": True,
                "payment_nonce": 1,
                "payment_challenge_id": canonical_id,
            },
        )
    )

    ctx = RequestContext(
        user_id="alice",
        input={},
        output={"answer": "ok"},
        metadata={
            "payment_challenge_id": canonical_id,
            "payment_receipt": {"reference": "0xdeadbeef"},
        },
    )
    result = await policy.post_execute(ctx)
    assert result.allowed

    row = await policy.get_transaction(canonical_id)
    assert row is not None
    assert row["status"] == "settled"
    assert row["tx_hash"] == "0xdeadbeef"
    assert row["settled_at"] is not None
    assert row["failure_reason"] is None


async def test_post_execute_with_failure_updates_row_to_failed(policy):
    canonical_id = "canon-fail-1"
    await policy.pre_execute(
        RequestContext(
            user_id="alice",
            input={},
            metadata={
                "payment_verified": True,
                "payment_nonce": 2,
                "payment_challenge_id": canonical_id,
            },
        )
    )

    ctx = RequestContext(
        user_id="alice",
        input={},
        output={"answer": "ok"},
        metadata={
            "payment_challenge_id": canonical_id,
            "payment_failure": {"reason": "insufficient funds"},
        },
    )
    result = await policy.post_execute(ctx)
    assert result.allowed

    row = await policy.get_transaction(canonical_id)
    assert row is not None
    assert row["status"] == "failed"
    assert row["failure_reason"] == "insufficient funds"
    assert row["settled_at"] is not None
    assert row["tx_hash"] is None


async def test_post_execute_without_payment_metadata_is_noop(policy):
    """post_execute fires for every request, including those that hit the
    allow-listed-payer path where no payment metadata exists.  Must be a
    quiet allow that touches no rows.
    """
    ctx = RequestContext(user_id="alice", input={}, output={}, metadata={})
    result = await policy.post_execute(ctx)
    assert result.allowed
    assert await policy.list_transactions() == []


async def test_post_execute_with_receipt_but_no_challenge_id_is_silent_noop(policy):
    """If the Go gate forgot to thread payment_challenge_id we can't
    pinpoint the row; the policy must NOT guess and must NOT raise.
    """
    canonical_id = "canon-orphan"
    await policy.pre_execute(
        RequestContext(
            user_id="alice",
            input={},
            metadata={
                "payment_verified": True,
                "payment_nonce": 3,
                "payment_challenge_id": canonical_id,
            },
        )
    )
    ctx = RequestContext(
        user_id="alice",
        input={},
        output={},
        metadata={"payment_receipt": {"reference": "0xnope"}},
    )
    result = await policy.post_execute(ctx)
    assert result.allowed
    # Row is unchanged.
    row = await policy.get_transaction(canonical_id)
    assert row is not None
    assert row["status"] == "verified"
    assert row["tx_hash"] is None


# ── export ─────────────────────────────────────────────────────


async def test_export_includes_hmac_secret_kid(store, tmp_path):
    """``hmac_secret_kid`` is a key id, not a secret, and is part of the
    exported config so consumers know which key the Go gate will look up.
    """
    p = _make_policy(tmp_path, allow_listed_payers=["vip@example.com"])
    await p.setup(store)
    try:
        data = p.export()
        assert data["type"] == "mpp"
        assert data["name"] == "x402"
        cfg = data["config"]
        assert cfg["pay_to"] == "0xPayTo"
        assert cfg["price"] == "0.01"
        assert cfg["currency"] == "0xCurrency"
        assert cfg["decimals"] == 6
        assert cfg["chain_id"] == 42431
        assert cfg["realm"] == "syfthub:endpoint:test:x402"
        assert cfg["hmac_secret_kid"] == "default-kid"
        assert cfg["challenge_ttl_seconds"] == 300
        assert cfg["max_pending_settlements_per_payer"] == 16
        assert cfg["allow_listed_payers"] == ["vip@example.com"]
        # The retired knob must NOT be present.
        assert "max_open_challenges_per_payer" not in cfg
    finally:
        await p.close()


# ── factory registration ───────────────────────────────────────


def test_factory_registers_x402_pay_per_request():
    factory = PolicyFactory()
    policies = factory.create_all(
        [
            PolicyConfigSchema(
                name="x402-test",
                type="mpp",
                config={
                    "pay_to": "0xPayTo",
                    "price": "0.05",
                    "currency": "0xCurrency",
                    "realm": "syfthub:endpoint:foo:x402-test",
                    "hmac_secret_kid": "kid-1",
                    "challenge_ttl_seconds": 120,
                    "max_pending_settlements_per_payer": 4,
                    "allow_listed_payers": ["alice"],
                },
            )
        ]
    )
    assert len(policies) == 1
    assert isinstance(policies[0], X402PayPerRequestPolicy)
    assert policies[0].name == "x402-test"
    assert "mpp" in PolicyFactory.registered_types()
