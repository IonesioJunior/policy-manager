"""Tests for PolicyResult."""

from policy_manager import PolicyResult


def test_allow():
    r = PolicyResult.allow("my_policy")
    assert r.allowed is True
    assert r.policy_name == "my_policy"
    assert r.pending is False
    assert r.reason == ""


def test_deny():
    r = PolicyResult.deny("my_policy", "bad request", code=403)
    assert r.allowed is False
    assert r.pending is False
    assert r.policy_name == "my_policy"
    assert r.reason == "bad request"
    assert r.metadata["code"] == 403


def test_pend():
    r = PolicyResult.pend("review", "needs human", ticket="ABC")
    assert r.allowed is False
    assert r.pending is True
    assert r.reason == "needs human"
    assert r.metadata["ticket"] == "ABC"


def test_substitute():
    r = PolicyResult.substitute("review", output="held message", reason="held", ref="abc")
    # A substitution succeeds and carries a replacement body.
    assert r.allowed is True
    assert r.substituted is True
    assert r.pending is False
    assert r.output == "held message"
    assert r.policy_name == "review"
    assert r.reason == "held"
    assert r.metadata["ref"] == "abc"


def test_allow_not_substituted():
    assert PolicyResult.allow("p").substituted is False
    assert PolicyResult.allow("p").output is None
    assert PolicyResult.deny("p", "no").substituted is False


def test_immutable():
    r = PolicyResult.allow()
    try:
        r.allowed = False  # type: ignore[misc]
        raise AssertionError("Should have raised")
    except AttributeError:
        pass
