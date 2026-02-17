"""Tests for RateLimitPolicy."""

from datetime import UTC, datetime

import pytest

from policy_manager.policies import RateLimitPolicy


class FakeClock:
    def __init__(self, start: float = 1000.0):
        self._now = start

    def now(self) -> datetime:
        return datetime.fromtimestamp(self._now, tz=UTC)

    def advance(self, seconds: float) -> None:
        self._now += seconds


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
async def rl(store, clock):
    policy = RateLimitPolicy(
        name="rl",
        max_requests=3,
        window_seconds=60,
        clock=clock,
    )
    await policy.setup(store)
    return policy


async def test_allows_under_limit(rl, alice_ctx):
    for _ in range(3):
        result = await rl.pre_execute(alice_ctx)
        assert result.allowed


async def test_denies_over_limit(rl, alice_ctx):
    for _ in range(3):
        await rl.pre_execute(alice_ctx)
    result = await rl.pre_execute(alice_ctx)
    assert not result.allowed
    assert "Rate limit exceeded" in result.reason


async def test_window_resets(rl, alice_ctx, clock):
    for _ in range(3):
        await rl.pre_execute(alice_ctx)

    clock.advance(61)  # past the window
    result = await rl.pre_execute(alice_ctx)
    assert result.allowed


async def test_per_user_isolation(rl, alice_ctx, bob_ctx):
    for _ in range(3):
        await rl.pre_execute(alice_ctx)

    # alice is exhausted, bob should still be fine
    result = await rl.pre_execute(bob_ctx)
    assert result.allowed


async def test_remaining_in_metadata(rl, alice_ctx):
    await rl.pre_execute(alice_ctx)
    assert alice_ctx.metadata["rl_remaining"] == 2


async def test_post_execute_passthrough(rl, alice_ctx):
    result = await rl.post_execute(alice_ctx)
    assert result.allowed
