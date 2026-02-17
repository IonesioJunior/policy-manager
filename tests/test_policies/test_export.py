"""Tests for Policy.export() across all policy types."""

from __future__ import annotations

import json

from policy_manager.policies import (
    AccessGroupPolicy,
    AllOf,
    AnyOf,
    AttributionPolicy,
    CustomPolicy,
    ManualReviewPolicy,
    Not,
    PromptFilterPolicy,
    RateLimitPolicy,
    TokenLimitPolicy,
    TransactionPolicy,
)

# ── helpers ──────────────────────────────────────────────────


def _assert_base_shape(data: dict, *, name: str, type_name: str, phases: list[str]) -> None:
    """Verify the common envelope produced by Policy.export()."""
    assert data["name"] == name
    assert data["type"] == type_name
    assert data["phase"] == phases
    assert isinstance(data["config"], dict)


def _assert_json_roundtrip(data: dict) -> None:
    """Verify the export dict survives JSON serialization."""
    raw = json.dumps(data)
    assert json.loads(raw) == data


# ── RateLimitPolicy ──────────────────────────────────────────


def test_rate_limit_export():
    p = RateLimitPolicy(name="rl", max_requests=100, window_seconds=3600)
    data = p.export()
    _assert_base_shape(data, name="rl", type_name="RateLimitPolicy", phases=["pre"])
    assert data["config"] == {"max_requests": 100, "window_seconds": 3600}
    _assert_json_roundtrip(data)


# ── TokenLimitPolicy ─────────────────────────────────────────


def test_token_limit_export_defaults():
    p = TokenLimitPolicy(name="tl", max_input_tokens=500, max_output_tokens=1000)
    data = p.export()
    _assert_base_shape(data, name="tl", type_name="TokenLimitPolicy", phases=["pre", "post"])
    assert data["config"]["max_input_tokens"] == 500
    assert data["config"]["max_output_tokens"] == 1000
    assert data["config"]["input_path"] == "query"
    assert data["config"]["output_path"] == "response"
    assert data["config"]["has_custom_counter"] is False
    _assert_json_roundtrip(data)


def test_token_limit_export_custom_counter():
    p = TokenLimitPolicy(name="tl2", max_input_tokens=10, token_counter=lambda s: len(s) // 4)
    data = p.export()
    assert data["config"]["has_custom_counter"] is True
    _assert_json_roundtrip(data)


# ── AccessGroupPolicy ────────────────────────────────────────


def test_access_group_export():
    p = AccessGroupPolicy(
        name="eng",
        owner="admin@acme.com",
        users=["bob@acme.com", "alice@acme.com"],
        documents=["doc_a", "doc_b"],
    )
    data = p.export()
    _assert_base_shape(data, name="eng", type_name="AccessGroupPolicy", phases=["pre"])
    assert data["config"]["owner"] == "admin@acme.com"
    assert data["config"]["users"] == ["alice@acme.com", "bob@acme.com"]  # sorted
    assert data["config"]["documents"] == ["doc_a", "doc_b"]
    _assert_json_roundtrip(data)


# ── AttributionPolicy ────────────────────────────────────────


def test_attribution_export_no_callback():
    p = AttributionPolicy(name="attr")
    data = p.export()
    _assert_base_shape(data, name="attr", type_name="AttributionPolicy", phases=["pre"])
    assert data["config"]["url_input_key"] == "attribution_url"
    assert data["config"]["has_verify_callback"] is False
    _assert_json_roundtrip(data)


def test_attribution_export_with_callback():
    async def _verify(user_id: str, url: str) -> bool:
        return True

    p = AttributionPolicy(name="attr2", verify_callback=_verify, url_input_key="source_url")
    data = p.export()
    assert data["config"]["has_verify_callback"] is True
    assert data["config"]["url_input_key"] == "source_url"
    _assert_json_roundtrip(data)


# ── ManualReviewPolicy ───────────────────────────────────────


def test_manual_review_export_no_callback():
    p = ManualReviewPolicy(name="review")
    data = p.export()
    _assert_base_shape(data, name="review", type_name="ManualReviewPolicy", phases=["post"])
    assert data["config"]["has_review_callback"] is False
    _assert_json_roundtrip(data)


def test_manual_review_export_with_callback():
    async def _review(payload: dict) -> dict:
        return {"approved": True}

    p = ManualReviewPolicy(name="review2", review_callback=_review)
    data = p.export()
    assert data["config"]["has_review_callback"] is True
    _assert_json_roundtrip(data)


# ── TransactionPolicy ────────────────────────────────────────


def test_transaction_export_with_config():
    p = TransactionPolicy(
        name="txn",
        ledger_url="https://api.ledger.example.com",
        api_token="at_xxx",
        token_field="payment_token",
        timeout=15.0,
        price_per_request=0.10,
    )
    data = p.export()
    _assert_base_shape(data, name="txn", type_name="TransactionPolicy", phases=["post"])
    assert data["config"]["ledger_url"] == "https://api.ledger.example.com"
    assert data["config"]["token_field"] == "payment_token"
    assert data["config"]["timeout"] == 15.0
    assert data["config"]["has_api_token"] is True
    assert data["config"]["price_per_request"] == 0.10
    _assert_json_roundtrip(data)


def test_transaction_export_defaults():
    p = TransactionPolicy(name="txn2", ledger_url="https://ledger.test", api_token="tok")
    data = p.export()
    assert data["config"]["token_field"] == "transaction_token"
    assert data["config"]["timeout"] == 30.0
    assert data["config"]["price_per_request"] == 0.0
    _assert_json_roundtrip(data)


# ── PromptFilterPolicy ───────────────────────────────────────


def test_prompt_filter_export_patterns():
    p = PromptFilterPolicy(
        name="pf",
        patterns=["secret", r"\bpassword\b"],
        input_path="prompt",
        output_path="answer",
        check_input=True,
        check_output=False,
    )
    data = p.export()
    _assert_base_shape(data, name="pf", type_name="PromptFilterPolicy", phases=["pre", "post"])
    assert data["config"]["patterns"] == ["secret", r"\bpassword\b"]
    assert data["config"]["has_filter_fn"] is False
    assert data["config"]["input_path"] == "prompt"
    assert data["config"]["output_path"] == "answer"
    assert data["config"]["check_input"] is True
    assert data["config"]["check_output"] is False
    _assert_json_roundtrip(data)


def test_prompt_filter_export_with_filter_fn():
    p = PromptFilterPolicy(name="pf2", filter_fn=lambda text: "bad" in text)
    data = p.export()
    assert data["config"]["has_filter_fn"] is True
    assert data["config"]["patterns"] == []
    _assert_json_roundtrip(data)


# ── CustomPolicy ─────────────────────────────────────────────


def test_custom_export_pre():
    p = CustomPolicy(name="c1", phase="pre", check=lambda ctx: True, deny_reason="nope")
    data = p.export()
    _assert_base_shape(data, name="c1", type_name="CustomPolicy", phases=["pre", "post"])
    assert data["config"]["phase"] == "pre"
    assert data["config"]["deny_reason"] == "nope"
    assert data["config"]["has_check"] is True
    _assert_json_roundtrip(data)


def test_custom_export_both():
    p = CustomPolicy(name="c2", phase="both", check=lambda ctx: False, deny_reason="fail")
    data = p.export()
    assert data["config"]["phase"] == "both"
    _assert_json_roundtrip(data)


# ── Composite: AllOf ─────────────────────────────────────────


def test_allof_export():
    child1 = RateLimitPolicy(name="rl", max_requests=10, window_seconds=60)
    child2 = TokenLimitPolicy(name="tl", max_input_tokens=100)
    comp = AllOf(child1, child2, name="both_limits")
    data = comp.export()

    _assert_base_shape(data, name="both_limits", type_name="AllOf", phases=["pre", "post"])
    cfg = data["config"]
    assert cfg["operator"] == "all_of"
    assert len(cfg["policies"]) == 2
    assert cfg["policies"][0]["name"] == "rl"
    assert cfg["policies"][1]["name"] == "tl"
    _assert_json_roundtrip(data)


# ── Composite: AnyOf ─────────────────────────────────────────


def test_anyof_export():
    child1 = CustomPolicy(name="a", phase="pre", check=lambda c: True, deny_reason="")
    child2 = CustomPolicy(name="b", phase="pre", check=lambda c: False, deny_reason="no")
    comp = AnyOf(child1, child2, name="either")
    data = comp.export()

    _assert_base_shape(data, name="either", type_name="AnyOf", phases=["pre", "post"])
    cfg = data["config"]
    assert cfg["operator"] == "any_of"
    assert len(cfg["policies"]) == 2
    _assert_json_roundtrip(data)


# ── Composite: Not ───────────────────────────────────────────


def test_not_export():
    child = CustomPolicy(name="inner", phase="pre", check=lambda c: True, deny_reason="x")
    comp = Not(child, name="inverted", deny_reason="should have failed")
    data = comp.export()

    _assert_base_shape(data, name="inverted", type_name="Not", phases=["pre", "post"])
    cfg = data["config"]
    assert cfg["operator"] == "not"
    assert cfg["policy"]["name"] == "inner"
    assert cfg["deny_reason"] == "should have failed"
    _assert_json_roundtrip(data)


# ── Nested composites ────────────────────────────────────────


def test_nested_composite_export():
    rl = RateLimitPolicy(name="rl", max_requests=5, window_seconds=60)
    custom = CustomPolicy(name="ok", phase="pre", check=lambda c: True, deny_reason="")
    inner = AllOf(rl, custom, name="inner_all")
    outer = Not(inner, name="not_inner")
    data = outer.export()

    assert data["config"]["operator"] == "not"
    inner_data = data["config"]["policy"]
    assert inner_data["config"]["operator"] == "all_of"
    assert len(inner_data["config"]["policies"]) == 2
    assert inner_data["config"]["policies"][0]["config"]["max_requests"] == 5
    _assert_json_roundtrip(data)
