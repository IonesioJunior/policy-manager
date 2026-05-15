"""Tests for PolicyManager — full chain integration."""

import json

from policy_manager import PolicyManager, PolicyResult, RequestContext
from policy_manager.policies import (
    AccessGroupPolicy,
    CustomPolicy,
    Policy,
    RateLimitPolicy,
    TokenLimitPolicy,
)


class _SubstitutingPolicy(Policy):
    """Test policy that substitutes the response body during post_execute."""

    def __init__(self, name: str) -> None:
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    async def post_execute(self, context):  # type: ignore[no-untyped-def]
        return PolicyResult.substitute(self._name, output="substituted body")

# ── basic chain behaviour ────────────────────────────────────


async def test_empty_manager_allows(pm, alice_ctx):
    result = await pm.check_pre_exec_policies(alice_ctx)
    assert result.allowed

    result = await pm.check_post_exec_policies(alice_ctx)
    assert result.allowed


async def test_single_allow_policy(pm, alice_ctx):
    await pm.add_policy(CustomPolicy(name="ok", phase="pre", check=lambda c: True, deny_reason=""))
    result = await pm.check_pre_exec_policies(alice_ctx)
    assert result.allowed


async def test_single_deny_policy(pm, alice_ctx):
    await pm.add_policy(
        CustomPolicy(name="no", phase="pre", check=lambda c: False, deny_reason="nope")
    )
    result = await pm.check_pre_exec_policies(alice_ctx)
    assert not result.allowed
    assert result.policy_name == "no"


# ── chain order and short-circuit ────────────────────────────


async def test_chain_order(pm, alice_ctx):
    """Policies execute in registration order."""
    order = []

    def make_check(label):
        def check(ctx):
            order.append(label)
            return True

        return check

    await pm.add_policy(CustomPolicy(name="a", phase="pre", check=make_check("a"), deny_reason=""))
    await pm.add_policy(CustomPolicy(name="b", phase="pre", check=make_check("b"), deny_reason=""))
    await pm.add_policy(CustomPolicy(name="c", phase="pre", check=make_check("c"), deny_reason=""))

    await pm.check_pre_exec_policies(alice_ctx)
    assert order == ["a", "b", "c"]


async def test_chain_short_circuits_on_deny(pm, alice_ctx):
    calls = []

    def make_check(label, result):
        def check(ctx):
            calls.append(label)
            return result

        return check

    await pm.add_policy(
        CustomPolicy(name="a", phase="pre", check=make_check("a", True), deny_reason="")
    )
    await pm.add_policy(
        CustomPolicy(name="b", phase="pre", check=make_check("b", False), deny_reason="b failed")
    )
    await pm.add_policy(
        CustomPolicy(name="c", phase="pre", check=make_check("c", True), deny_reason="")
    )

    result = await pm.check_pre_exec_policies(alice_ctx)
    assert not result.allowed
    assert result.policy_name == "b"
    assert calls == ["a", "b"]  # c was never called


# ── context mutation chains ──────────────────────────────────


async def test_context_mutation_propagates(pm, alice_ctx):
    """Upstream policy mutates metadata; downstream policy reads it."""

    def policy_a_check(ctx):
        ctx.metadata["enriched"] = True
        return True

    def policy_b_check(ctx):
        return ctx.metadata.get("enriched", False)

    await pm.add_policy(CustomPolicy(name="a", phase="pre", check=policy_a_check, deny_reason=""))
    await pm.add_policy(
        CustomPolicy(name="b", phase="pre", check=policy_b_check, deny_reason="not enriched")
    )

    result = await pm.check_pre_exec_policies(alice_ctx)
    assert result.allowed


# ── full pre → function → post lifecycle ─────────────────────


async def test_full_lifecycle():
    pm = PolicyManager()

    await pm.add_policy(
        AccessGroupPolicy(
            name="eng",
            users=["alice@acme.com"],
            documents=["doc_a", "doc_b"],
        )
    )
    await pm.add_policy(RateLimitPolicy(name="rl", max_requests=10, window_seconds=60))
    await pm.add_policy(TokenLimitPolicy(name="tl", max_output_tokens=100))

    ctx = RequestContext(user_id="alice@acme.com", input={"query": "hello"})

    # Pre
    pre = await pm.check_pre_exec_policies(ctx)
    assert pre.allowed
    assert ctx.metadata["resolved_documents"] == ["doc_a", "doc_b"]

    # User function
    ctx.output = {"response": "Short answer."}

    # Post
    post = await pm.check_post_exec_policies(ctx)
    assert post.allowed


async def test_full_lifecycle_denied_pre():
    pm = PolicyManager()
    await pm.add_policy(
        AccessGroupPolicy(
            name="eng",
            users=["alice@acme.com"],
            documents=["doc_a"],
        )
    )

    ctx = RequestContext(user_id="eve@external.com", input={"query": "hack"})
    pre = await pm.check_pre_exec_policies(ctx)
    assert not pre.allowed
    assert pre.policy_name == "eng"


async def test_full_lifecycle_denied_post():
    pm = PolicyManager()
    await pm.add_policy(TokenLimitPolicy(name="tl", max_output_tokens=10))

    ctx = RequestContext(user_id="u", input={"query": "q"})
    assert (await pm.check_pre_exec_policies(ctx)).allowed

    ctx.output = {"response": "x" * 50}
    post = await pm.check_post_exec_policies(ctx)
    assert not post.allowed


# ── substitution short-circuit ───────────────────────────────


async def test_post_chain_short_circuits_on_substitution(pm, alice_ctx):
    """A substituted result is terminal — later post policies never run."""
    ran: list[str] = []

    class _Recorder(Policy):
        def __init__(self, name: str) -> None:
            self._name = name

        @property
        def name(self) -> str:
            return self._name

        async def post_execute(self, context):  # type: ignore[no-untyped-def]
            ran.append(self._name)
            return PolicyResult.allow(self._name)

    await pm.add_policy(_SubstitutingPolicy("sub"))
    await pm.add_policy(_Recorder("after"))

    result = await pm.check_post_exec_policies(alice_ctx)

    assert result.substituted
    assert result.allowed  # substitution is a success, not a denial
    assert result.output == "substituted body"
    assert result.policy_name == "sub"
    assert ran == []  # the policy after the substitution never ran


# ── lifecycle ─────────────────────────────────────────────────


async def test_aclose_closes_policies(pm):
    """aclose() calls close() on every policy that exposes one."""
    closed: list[str] = []

    class _Closeable(Policy):
        def __init__(self, name: str) -> None:
            self._name = name

        @property
        def name(self) -> str:
            return self._name

        async def close(self) -> None:
            closed.append(self._name)

    await pm.add_policy(_Closeable("a"))
    await pm.add_policy(
        CustomPolicy(name="b", phase="pre", check=lambda c: True, deny_reason="")
    )

    await pm.aclose()

    assert closed == ["a"]  # only the closeable policy is closed


# ── introspection ────────────────────────────────────────────


async def test_get_policy(pm):
    await pm.add_policy(
        CustomPolicy(name="find_me", phase="pre", check=lambda c: True, deny_reason="")
    )
    p = pm.get_policy("find_me")
    assert p is not None
    assert p.name == "find_me"


async def test_get_policy_not_found(pm):
    assert pm.get_policy("nope") is None


async def test_list_policies(pm):
    await pm.add_policy(CustomPolicy(name="a", phase="pre", check=lambda c: True, deny_reason=""))
    await pm.add_policy(CustomPolicy(name="b", phase="pre", check=lambda c: True, deny_reason=""))
    assert pm.list_policies() == ["a", "b"]


# ── default store ────────────────────────────────────────────


async def test_default_store():
    pm = PolicyManager()  # no store argument
    assert pm.store is not None
    await pm.add_policy(CustomPolicy(name="ok", phase="pre", check=lambda c: True, deny_reason=""))
    ctx = RequestContext(user_id="u", input={})
    assert (await pm.check_pre_exec_policies(ctx)).allowed


# ── export ────────────────────────────────────────────────────


def test_export_empty_manager(pm):
    data = pm.export()
    assert data == {"policies": [], "policy_count": 0}


async def test_export_preserves_registration_order(pm):
    await pm.add_policy(RateLimitPolicy(name="rl", max_requests=10, window_seconds=60))
    await pm.add_policy(TokenLimitPolicy(name="tl", max_input_tokens=500))
    await pm.add_policy(CustomPolicy(name="c", phase="pre", check=lambda ctx: True, deny_reason=""))

    data = pm.export()
    names = [p["name"] for p in data["policies"]]
    assert names == ["rl", "tl", "c"]
    assert data["policy_count"] == 3


async def test_export_json_roundtrip(pm):
    await pm.add_policy(RateLimitPolicy(name="rl", max_requests=5, window_seconds=120))
    await pm.add_policy(
        AccessGroupPolicy(
            name="eng",
            owner="admin",
            users=["alice", "bob"],
            documents=["doc1"],
        )
    )

    data = pm.export()
    raw = json.dumps(data)
    assert json.loads(raw) == data


async def test_export_full_details(pm):
    await pm.add_policy(RateLimitPolicy(name="rate", max_requests=100, window_seconds=3600))
    data = pm.export()

    assert data["policy_count"] == 1
    entry = data["policies"][0]
    assert entry["name"] == "rate"
    assert entry["type"] == "rate_limit"
    assert entry["phase"] == ["pre"]
    assert entry["config"]["max_requests"] == 100
    assert entry["config"]["window_seconds"] == 3600
    # SyftHub-compatible fields
    assert entry["version"] == "1.0"
    assert entry["enabled"] is True
    assert "description" in entry
