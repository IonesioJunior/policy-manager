"""Tests for InMemoryStore."""

import pytest

from policy_manager.stores import InMemoryStore


@pytest.fixture
def store():
    return InMemoryStore()


async def test_get_nonexistent(store):
    assert await store.get("ns", "key") is None


async def test_set_and_get(store):
    await store.set("ns", "k", {"val": 1})
    assert await store.get("ns", "k") == {"val": 1}


async def test_overwrite(store):
    await store.set("ns", "k", {"a": 1})
    await store.set("ns", "k", {"a": 2})
    assert (await store.get("ns", "k"))["a"] == 2


async def test_delete(store):
    await store.set("ns", "k", {"v": 1})
    await store.delete("ns", "k")
    assert await store.get("ns", "k") is None


async def test_delete_nonexistent(store):
    await store.delete("ns", "nope")  # should not raise


async def test_exists(store):
    assert not await store.exists("ns", "k")
    await store.set("ns", "k", {})
    assert await store.exists("ns", "k")


async def test_list_keys(store):
    await store.set("ns", "a", {})
    await store.set("ns", "b", {})
    await store.set("other", "c", {})
    keys = await store.list_keys("ns")
    assert sorted(keys) == ["a", "b"]


async def test_list_keys_empty(store):
    assert await store.list_keys("ns") == []


async def test_clear_namespace(store):
    await store.set("ns", "a", {"v": 1})
    await store.set("ns", "b", {"v": 2})
    await store.set("other", "c", {"v": 3})

    await store.clear_namespace("ns")
    assert await store.list_keys("ns") == []
    assert await store.get("other", "c") == {"v": 3}


async def test_namespace_isolation(store):
    await store.set("ns1", "k", {"val": 1})
    await store.set("ns2", "k", {"val": 2})
    assert (await store.get("ns1", "k"))["val"] == 1
    assert (await store.get("ns2", "k"))["val"] == 2
