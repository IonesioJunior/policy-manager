"""Shared test fixtures."""

import pytest

from policy_manager import PolicyManager, RequestContext
from policy_manager.stores import InMemoryStore


@pytest.fixture
def store():
    return InMemoryStore()


@pytest.fixture
def pm(store):
    return PolicyManager(store=store)


@pytest.fixture
def alice_ctx():
    return RequestContext(
        user_id="alice@acme.com",
        input={"query": "system architecture"},
    )


@pytest.fixture
def bob_ctx():
    return RequestContext(
        user_id="bob@acme.com",
        input={"query": "budget forecast"},
    )


@pytest.fixture
def unknown_ctx():
    return RequestContext(
        user_id="eve@external.com",
        input={"query": "secrets"},
    )
