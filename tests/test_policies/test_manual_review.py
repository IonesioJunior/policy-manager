"""Tests for ManualReviewPolicy."""

import sqlite3

import pytest

from policy_manager import RequestContext
from policy_manager.policies import ManualReviewPolicy
from policy_manager.stores import SQLiteStore


@pytest.fixture
async def mr(store, tmp_path):
    policy = ManualReviewPolicy(name="mr", db_path=str(tmp_path / "reviews.db"))
    await policy.setup(store)
    yield policy
    await policy.close()


async def test_post_substitutes_string_for_model(mr):
    ctx = RequestContext(
        user_id="alice", input={"type": "model"}, output={"response": "r"}
    )
    result = await mr.post_execute(ctx)

    # The request succeeds with a substituted placeholder body.
    assert result.allowed
    assert result.substituted
    assert "review_id" in result.metadata

    # model endpoints get a plain-string placeholder.
    assert isinstance(result.output, str)
    assert "Request submitted to manual review" in result.output
    assert result.metadata["review_id"] in result.output

    # post_execute does not mutate the real handler output.
    assert ctx.output == {"response": "r"}


async def test_post_substitutes_document_list_for_data_source(mr):
    ctx = RequestContext(
        user_id="alice", input={"type": "data_source"}, output={"response": "r"}
    )
    result = await mr.post_execute(ctx)

    assert result.substituted
    # data_source endpoints get a single-document placeholder list.
    assert isinstance(result.output, list)
    assert len(result.output) == 1
    doc = result.output[0]
    assert "Request submitted to manual review" in doc["content"]
    assert doc["metadata"]["review_id"] == result.metadata["review_id"]
    assert doc["metadata"]["status"] == "pending"


async def test_custom_placeholder_message(store, tmp_path):
    policy = ManualReviewPolicy(
        name="mr", db_path=str(tmp_path / "r.db"), placeholder_message="Hold tight"
    )
    await policy.setup(store)
    ctx = RequestContext(user_id="alice", input={"type": "model"}, output={"response": "r"})
    result = await policy.post_execute(ctx)
    assert "Hold tight" in result.output
    await policy.close()


async def test_real_output_persisted_not_placeholder(mr):
    ctx = RequestContext(user_id="alice", input={"q": 1}, output={"response": "secret"})
    result = await mr.post_execute(ctx)
    review_id = result.metadata["review_id"]

    pending = await mr.get_pending()
    assert len(pending) == 1
    entry = pending[0]
    assert entry["review_id"] == review_id
    # The stored output is the REAL handler output, not the placeholder.
    assert entry["output"] == {"response": "secret"}
    assert entry["input"] == {"q": 1}
    assert entry["status"] == "pending"
    assert entry["pending"] == 1


async def test_pre_execute_passthrough(mr, alice_ctx):
    result = await mr.pre_execute(alice_ctx)
    assert result.allowed


async def test_approve(mr):
    ctx = RequestContext(user_id="alice", input={}, output={"response": "r"})
    result = await mr.post_execute(ctx)
    review_id = result.metadata["review_id"]

    assert await mr.approve(review_id)
    assert await mr.get_pending() == []


async def test_reject(mr):
    ctx = RequestContext(user_id="alice", input={}, output={"response": "r"})
    result = await mr.post_execute(ctx)
    review_id = result.metadata["review_id"]

    assert await mr.reject(review_id, reason="inappropriate")

    # Resolved rows leave the pending set; the reason is recorded.
    assert await mr.get_pending() == []
    conn = sqlite3.connect(mr._db_path)
    status, reason = conn.execute(
        "SELECT status, reject_reason FROM manual_reviews WHERE review_id = ?",
        (review_id,),
    ).fetchone()
    conn.close()
    assert status == "rejected"
    assert reason == "inappropriate"


async def test_get_pending(mr):
    await mr.post_execute(RequestContext(user_id="a", input={}, output={"response": "r1"}))
    await mr.post_execute(RequestContext(user_id="b", input={}, output={"response": "r2"}))

    pending = await mr.get_pending()
    assert len(pending) == 2
    assert {e["user_id"] for e in pending} == {"a", "b"}


async def test_get_pending_excludes_resolved(mr):
    r1 = await mr.post_execute(RequestContext(user_id="a", input={}, output={}))
    await mr.post_execute(RequestContext(user_id="b", input={}, output={}))
    await mr.approve(r1.metadata["review_id"])

    pending = await mr.get_pending()
    assert len(pending) == 1
    assert pending[0]["user_id"] == "b"


async def test_auto_approve_callback(store, tmp_path):
    async def auto_approve(payload):
        return {"approved": True}

    policy = ManualReviewPolicy(
        name="auto_mr", db_path=str(tmp_path / "r.db"), review_callback=auto_approve
    )
    await policy.setup(store)

    ctx = RequestContext(user_id="alice", input={}, output={"response": "r"})
    result = await policy.post_execute(ctx)

    # Auto-approved: the real response flows through, request is allowed.
    assert result.allowed
    assert not result.pending
    assert ctx.output == {"response": "r"}
    # The row is recorded as already approved, so it is not pending.
    assert await policy.get_pending() == []
    await policy.close()


async def test_approve_nonexistent(mr):
    assert not await mr.approve("nonexistent")


async def test_reject_nonexistent(mr):
    assert not await mr.reject("nonexistent")


async def test_reuses_sqlite_store_db_file(tmp_path):
    """When db_path is omitted, the policy uses the executor's SQLite store file."""
    db_file = str(tmp_path / "shared.db")
    store = SQLiteStore(db_file)
    await store.set("ns", "k", {"v": 1})  # ensure the store's own table exists

    policy = ManualReviewPolicy(name="mr")  # no db_path -> reuse the store file
    await policy.setup(store)
    await policy.post_execute(
        RequestContext(user_id="alice", input={}, output={"response": "r"})
    )

    # Both tables coexist in the single shared database file.
    conn = sqlite3.connect(db_file)
    tables = {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    conn.close()
    assert "manual_reviews" in tables
    assert "policy_store" in tables

    await policy.close()
    await store.close()


async def test_pending_column_visible_to_external_reader(mr):
    """An external process can filter unresolved reviews on the pending column."""
    result = await mr.post_execute(RequestContext(user_id="a", input={}, output={}))
    review_id = result.metadata["review_id"]

    def count_pending() -> int:
        conn = sqlite3.connect(mr._db_path)
        (n,) = conn.execute(
            "SELECT COUNT(*) FROM manual_reviews WHERE pending = 1"
        ).fetchone()
        conn.close()
        return n

    assert count_pending() == 1
    await mr.approve(review_id)
    assert count_pending() == 0
