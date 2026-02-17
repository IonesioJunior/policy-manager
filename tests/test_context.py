"""Tests for RequestContext."""

from datetime import UTC, datetime

from policy_manager import RequestContext


def test_defaults():
    ctx = RequestContext(user_id="alice")
    assert ctx.user_id == "alice"
    assert ctx.input == {}
    assert ctx.output == {}
    assert ctx.metadata == {}
    assert isinstance(ctx.timestamp, datetime)
    assert ctx.timestamp.tzinfo == UTC


def test_custom_fields():
    ctx = RequestContext(
        user_id="bob",
        input={"query": "hello"},
        output={"response": "world"},
        metadata={"key": "val"},
    )
    assert ctx.input["query"] == "hello"
    assert ctx.output["response"] == "world"
    assert ctx.metadata["key"] == "val"


def test_mutable():
    ctx = RequestContext(user_id="alice")
    ctx.output = {"result": 42}
    ctx.metadata["flag"] = True
    assert ctx.output["result"] == 42
    assert ctx.metadata["flag"] is True
