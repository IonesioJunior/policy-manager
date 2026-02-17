"""Tests for TransactionPolicy."""

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from policy_manager.context import RequestContext
from policy_manager.policies import TransactionPolicy


@pytest.fixture
def ledger_url():
    return "https://api.ledger.example.com"


@pytest.fixture
def api_token():
    return "at_test_token_123"


@pytest.fixture
async def tx(store, ledger_url, api_token):
    policy = TransactionPolicy(
        name="tx",
        ledger_url=ledger_url,
        api_token=api_token,
    )
    await policy.setup(store)
    return policy


@pytest.fixture
def valid_token():
    """Token format: transactionId.salt.expiresAt.signature"""
    return "txn_abc123.salt456.1699999999.sig789"


@pytest.fixture
def ctx_with_token(valid_token):
    return RequestContext(
        user_id="alice@acme.com",
        input={"query": "test", "transaction_token": valid_token},
    )


@pytest.fixture
def ctx_without_token():
    return RequestContext(
        user_id="alice@acme.com",
        input={"query": "test"},
    )


# ── Missing token tests ──────────────────────────────────────────


async def test_deny_missing_token(tx, ctx_without_token):
    result = await tx.post_execute(ctx_without_token)
    assert not result.allowed
    assert "transaction_token required" in result.reason


async def test_custom_token_field(store, ledger_url, api_token, valid_token):
    policy = TransactionPolicy(
        name="tx",
        ledger_url=ledger_url,
        api_token=api_token,
        token_field="payment_token",
    )
    await policy.setup(store)

    ctx = RequestContext(
        user_id="alice@acme.com",
        input={"payment_token": valid_token},
    )

    with patch("httpx.AsyncClient") as mock_client:
        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_client.return_value.__aenter__.return_value.post = AsyncMock(
            return_value=mock_response
        )

        result = await policy.post_execute(ctx)
        assert result.allowed


# ── Configuration tests ──────────────────────────────────────────


async def test_deny_missing_ledger_url(store, api_token, ctx_with_token):
    policy = TransactionPolicy(name="tx", ledger_url="", api_token=api_token)
    await policy.setup(store)

    result = await policy.post_execute(ctx_with_token)
    assert not result.allowed
    assert "Ledger URL not configured" in result.reason


async def test_deny_missing_api_token(store, ledger_url, ctx_with_token):
    policy = TransactionPolicy(name="tx", ledger_url=ledger_url, api_token="")
    await policy.setup(store)

    result = await policy.post_execute(ctx_with_token)
    assert not result.allowed
    assert "API token not configured" in result.reason


async def test_env_var_fallback(store, ctx_with_token, monkeypatch):
    monkeypatch.setenv("LEDGER_URL", "https://env.ledger.com")
    monkeypatch.setenv("LEDGER_API_TOKEN", "env_token")

    policy = TransactionPolicy(name="tx")
    await policy.setup(store)

    with patch("httpx.AsyncClient") as mock_client:
        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_client.return_value.__aenter__.return_value.post = AsyncMock(
            return_value=mock_response
        )

        result = await policy.post_execute(ctx_with_token)
        assert result.allowed

        # Verify the correct URL was called (includes transfer_id from token)
        call_args = mock_client.return_value.__aenter__.return_value.post.call_args
        assert call_args[0][0] == "https://env.ledger.com/v1/transfers/txn_abc123/confirm"


# ── Successful confirmation tests ────────────────────────────────


async def test_allow_successful_confirmation(tx, ctx_with_token, valid_token):
    with patch("httpx.AsyncClient") as mock_client:
        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_client.return_value.__aenter__.return_value.post = AsyncMock(
            return_value=mock_response
        )

        result = await tx.post_execute(ctx_with_token)

        assert result.allowed
        assert ctx_with_token.metadata["tx_confirmed"] is True

        # Verify correct request was made (URL includes transfer_id from token)
        call_args = mock_client.return_value.__aenter__.return_value.post.call_args
        assert call_args[0][0] == "https://api.ledger.example.com/v1/transfers/txn_abc123/confirm"
        assert call_args[1]["json"] == {"confirmation_token": valid_token}
        assert call_args[1]["headers"]["Authorization"] == "Bearer at_test_token_123"


async def test_pre_execute_passthrough(tx, ctx_with_token):
    """pre_execute should always allow (confirmation happens in post_execute)."""
    result = await tx.pre_execute(ctx_with_token)
    assert result.allowed


# ── HTTP error tests ─────────────────────────────────────────────


async def test_deny_http_error_status(tx, ctx_with_token):
    with patch("httpx.AsyncClient") as mock_client:
        mock_response = AsyncMock()
        mock_response.status_code = 400
        mock_client.return_value.__aenter__.return_value.post = AsyncMock(
            return_value=mock_response
        )

        result = await tx.post_execute(ctx_with_token)

        assert not result.allowed
        assert "HTTP 400" in result.reason


async def test_deny_http_500(tx, ctx_with_token):
    with patch("httpx.AsyncClient") as mock_client:
        mock_response = AsyncMock()
        mock_response.status_code = 500
        mock_client.return_value.__aenter__.return_value.post = AsyncMock(
            return_value=mock_response
        )

        result = await tx.post_execute(ctx_with_token)

        assert not result.allowed
        assert "HTTP 500" in result.reason


async def test_deny_timeout(tx, ctx_with_token):
    with patch("httpx.AsyncClient") as mock_client:
        mock_client.return_value.__aenter__.return_value.post = AsyncMock(
            side_effect=httpx.TimeoutException("timeout")
        )

        result = await tx.post_execute(ctx_with_token)

        assert not result.allowed
        assert "timed out" in result.reason


async def test_deny_connection_error(tx, ctx_with_token):
    with patch("httpx.AsyncClient") as mock_client:
        mock_client.return_value.__aenter__.return_value.post = AsyncMock(
            side_effect=httpx.ConnectError("connection refused")
        )

        result = await tx.post_execute(ctx_with_token)

        assert not result.allowed
        assert "Could not connect" in result.reason


async def test_deny_generic_exception(tx, ctx_with_token):
    with patch("httpx.AsyncClient") as mock_client:
        mock_client.return_value.__aenter__.return_value.post = AsyncMock(
            side_effect=RuntimeError("unexpected error")
        )

        result = await tx.post_execute(ctx_with_token)

        assert not result.allowed
        assert "unexpected error" in result.reason


# ── Export tests ─────────────────────────────────────────────────


async def test_export(tx, ledger_url):
    data = tx.export()

    assert data["name"] == "tx"
    assert data["type"] == "TransactionPolicy"
    assert data["phase"] == ["post"]
    assert data["config"]["ledger_url"] == ledger_url
    assert data["config"]["token_field"] == "transaction_token"
    assert data["config"]["timeout"] == 30.0
    assert data["config"]["has_api_token"] is True
    assert data["config"]["price_per_request"] == 0.0


async def test_export_no_api_token(store, ledger_url):
    policy = TransactionPolicy(name="tx", ledger_url=ledger_url, api_token="")
    await policy.setup(store)

    data = policy.export()
    assert data["config"]["has_api_token"] is False


# ── Custom timeout tests ─────────────────────────────────────────


async def test_custom_timeout(store, ledger_url, api_token, ctx_with_token):
    policy = TransactionPolicy(
        name="tx",
        ledger_url=ledger_url,
        api_token=api_token,
        timeout=5.0,
    )
    await policy.setup(store)

    with patch("httpx.AsyncClient") as mock_client:
        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_client.return_value.__aenter__.return_value.post = AsyncMock(
            return_value=mock_response
        )

        await policy.post_execute(ctx_with_token)

        # Verify timeout was passed
        call_args = mock_client.return_value.__aenter__.return_value.post.call_args
        assert call_args[1]["timeout"] == 5.0


# ── Price per request tests ──────────────────────────────────────


async def test_price_per_request_export(store, ledger_url, api_token):
    policy = TransactionPolicy(
        name="tx",
        ledger_url=ledger_url,
        api_token=api_token,
        price_per_request=0.05,
    )
    await policy.setup(store)

    data = policy.export()
    assert data["config"]["price_per_request"] == 0.05


# ── Token format tests ────────────────────────────────────────────


def test_extract_transfer_id_valid():
    """Test extraction of transfer ID from valid token format."""
    token = "txn_abc123.salt456.1699999999.sig789"
    assert TransactionPolicy._extract_transfer_id(token) == "txn_abc123"


def test_extract_transfer_id_invalid_format():
    """Test extraction fails for tokens without 4 parts."""
    assert TransactionPolicy._extract_transfer_id("tok_abc123") is None
    assert TransactionPolicy._extract_transfer_id("a.b.c") is None
    assert TransactionPolicy._extract_transfer_id("a.b.c.d.e") is None
    assert TransactionPolicy._extract_transfer_id("") is None


def test_extract_transfer_id_empty_first_part():
    """Test extraction fails when transfer ID part is empty."""
    assert TransactionPolicy._extract_transfer_id(".salt.expires.sig") is None


async def test_deny_invalid_token_format(tx):
    """Test that invalid token format is rejected."""
    ctx = RequestContext(
        user_id="alice@acme.com",
        input={"query": "test", "transaction_token": "invalid_token_format"},
    )
    result = await tx.post_execute(ctx)
    assert not result.allowed
    assert "Invalid token format" in result.reason
