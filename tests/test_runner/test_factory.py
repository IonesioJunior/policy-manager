"""Tests for the policy factory."""

import pytest

from policy_manager.policies.base import Policy
from policy_manager.runner.factory import PolicyFactory, PolicyFactoryError
from policy_manager.runner.schema import PolicyConfigSchema


class TestPolicyFactory:
    """Tests for PolicyFactory."""

    def test_registered_types_includes_all_policies(self):
        """Test that all expected policy types are registered."""
        types = PolicyFactory.registered_types()

        assert "access_group" in types
        assert "rate_limit" in types
        assert "token_limit" in types
        assert "prompt_filter" in types
        assert "attribution" in types
        assert "manual_review" in types
        assert "transaction" in types
        assert "custom" in types
        # Composite types
        assert "all_of" in types
        assert "any_of" in types
        assert "not" in types

    def test_create_rate_limit_policy(self):
        """Test creating a rate limit policy from config."""
        factory = PolicyFactory()
        configs = [
            PolicyConfigSchema(
                name="rl",
                type="rate_limit",
                config={"max_requests": 10, "window_seconds": 60},
            )
        ]

        policies = factory.create_all(configs)

        assert len(policies) == 1
        assert policies[0].name == "rl"
        data = policies[0].export()
        assert data["type"] == "rate_limit"
        assert data["config"]["max_requests"] == 10

    def test_unknown_type_raises_error(self):
        """Test that unknown policy type raises error."""
        factory = PolicyFactory()
        configs = [
            PolicyConfigSchema(
                name="bad",
                type="unknown_type",
                config={},
            )
        ]

        with pytest.raises(PolicyFactoryError) as exc_info:
            factory.create_all(configs)

        assert "Unknown policy type" in str(exc_info.value)
        assert "unknown_type" in str(exc_info.value)


class TestPolicyTypeConsistency:
    """Tests for _policy_type consistency validation."""

    def test_register_matching_type_succeeds(self):
        """Test that registering with matching _policy_type succeeds."""

        class TestPolicy(Policy):
            _policy_type = "test_policy"

            @property
            def name(self) -> str:
                return "test"

        # Should not raise
        PolicyFactory.register("test_policy", TestPolicy)

        # Clean up
        del PolicyFactory._registry["test_policy"]

    def test_register_mismatched_type_raises(self):
        """Test that registering with mismatched _policy_type raises ValueError."""

        class TestPolicy(Policy):
            _policy_type = "declared_type"

            @property
            def name(self) -> str:
                return "test"

        with pytest.raises(ValueError) as exc_info:
            PolicyFactory.register("different_type", TestPolicy)

        assert "TestPolicy" in str(exc_info.value)
        assert "declared_type" in str(exc_info.value)
        assert "different_type" in str(exc_info.value)

    def test_register_base_type_succeeds(self):
        """Test that policies with _policy_type='base' can be registered with any name."""

        class BaseTypePolicy(Policy):
            _policy_type = "base"  # The default from Policy

            @property
            def name(self) -> str:
                return "test"

        # Should not raise - 'base' is a special case
        PolicyFactory.register("any_name_here", BaseTypePolicy)

        # Clean up
        del PolicyFactory._registry["any_name_here"]

    def test_register_no_policy_type_succeeds(self):
        """Test that policies without explicit _policy_type can be registered."""

        # Create a policy without explicitly setting _policy_type
        # It will inherit 'base' from Policy
        class NoTypePolicy(Policy):
            @property
            def name(self) -> str:
                return "test"

        # Should not raise
        PolicyFactory.register("no_type_policy", NoTypePolicy)

        # Clean up
        del PolicyFactory._registry["no_type_policy"]
