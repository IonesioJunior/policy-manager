# Copyright (c) 2024 OpenMined
# SPDX-License-Identifier: Apache-2.0
"""Policy factory for creating policy instances from configuration.

Uses the Registry pattern to map type strings to policy classes,
allowing extensibility without modifying factory code.
"""

from __future__ import annotations

from typing import ClassVar

from policy_manager.policies import (
    AccessGroupPolicy,
    AllOf,
    AnyOf,
    AttributionPolicy,
    CustomPolicy,
    ManualReviewPolicy,
    Not,
    Policy,
    PromptFilterPolicy,
    RateLimitPolicy,
    TokenLimitPolicy,
    TransactionPolicy,
)

from .schema import PolicyConfigSchema


class PolicyFactoryError(Exception):
    """Raised when policy creation fails."""

    pass


class PolicyFactory:
    """Creates policy instances from configuration.

    Uses Registry pattern for extensibility. Policy types are registered
    at class level and can be extended via the `register` class method.

    Composite policies (all_of, any_of, not) are handled specially since
    they reference other policies by name.

    Example:
        factory = PolicyFactory()
        configs = [
            PolicyConfigSchema(name="rate", type="rate_limit", config={"max_requests": 10}),
            PolicyConfigSchema(name="access", type="access_group", config={"users": ["a@b.com"]}),
            PolicyConfigSchema(name="both", type="all_of", config={"policies": ["rate", "access"]}),
        ]
        policies = factory.create_all(configs)
    """

    # Class-level registry mapping type strings to policy classes
    _registry: ClassVar[dict[str, type[Policy]]] = {
        "access_group": AccessGroupPolicy,
        "rate_limit": RateLimitPolicy,
        "token_limit": TokenLimitPolicy,
        "prompt_filter": PromptFilterPolicy,
        "attribution": AttributionPolicy,
        "manual_review": ManualReviewPolicy,
        "transaction": TransactionPolicy,
        "custom": CustomPolicy,
    }

    # Composite types need special handling (reference resolution)
    _composite_types: ClassVar[set[str]] = {"all_of", "any_of", "not"}

    def __init__(self) -> None:
        """Initialize factory with empty instance cache."""
        self._instances: dict[str, Policy] = {}

    @classmethod
    def register(cls, type_name: str, policy_class: type[Policy]) -> None:
        """Register a custom policy type.

        Args:
            type_name: Type string to use in configuration
            policy_class: Policy class to instantiate

        Raises:
            ValueError: If policy_class._policy_type doesn't match type_name

        Example:
            PolicyFactory.register("my_policy", MyCustomPolicy)
        """
        # Validate consistency between _policy_type and registration name
        if hasattr(policy_class, "_policy_type"):
            declared_type = policy_class._policy_type
            if declared_type != "base" and declared_type != type_name:
                raise ValueError(
                    f"Policy {policy_class.__name__} has _policy_type='{declared_type}' "
                    f"but is being registered as '{type_name}'"
                )
        cls._registry[type_name] = policy_class

    @classmethod
    def registered_types(cls) -> list[str]:
        """Return list of registered policy type names."""
        return list(cls._registry.keys()) + list(cls._composite_types)

    def create_all(self, configs: list[PolicyConfigSchema]) -> list[Policy]:
        """Create all policies from configuration list.

        Policies are created in order, allowing later policies to
        reference earlier ones by name (for composite policies).

        Args:
            configs: List of policy configurations

        Returns:
            List of created policy instances

        Raises:
            PolicyFactoryError: If creation fails
        """
        policies: list[Policy] = []

        for config in configs:
            try:
                policy = self._create_one(config)
                self._instances[config.name] = policy
                policies.append(policy)
            except PolicyFactoryError:
                raise
            except Exception as e:
                raise PolicyFactoryError(
                    f"Failed to create policy '{config.name}' of type '{config.type}': {e}"
                ) from e

        return policies

    def _create_one(self, config: PolicyConfigSchema) -> Policy:
        """Create a single policy instance.

        Args:
            config: Policy configuration

        Returns:
            Created policy instance

        Raises:
            PolicyFactoryError: If type is unknown or creation fails
        """
        if config.type in self._composite_types:
            return self._create_composite(config)

        policy_class = self._registry.get(config.type)
        if not policy_class:
            available = ", ".join(sorted(self.registered_types()))
            raise PolicyFactoryError(
                f"Unknown policy type: '{config.type}'. Available types: {available}"
            )

        # Filter config to only include valid kwargs for the policy
        # All concrete policies accept name kwarg, but base Policy doesn't declare it
        return policy_class(name=config.name, **config.config)  # type: ignore[call-arg]

    def _create_composite(self, config: PolicyConfigSchema) -> Policy:
        """Create composite policy, resolving child references.

        Args:
            config: Composite policy configuration

        Returns:
            Created composite policy

        Raises:
            PolicyFactoryError: If child references cannot be resolved
        """
        if config.type == "all_of":
            child_names = config.config.get("policies", [])
            if not child_names:
                raise PolicyFactoryError(f"all_of policy '{config.name}' requires 'policies' list")
            children = [self._resolve(name, config.name) for name in child_names]
            return AllOf(*children, name=config.name)

        if config.type == "any_of":
            child_names = config.config.get("policies", [])
            if not child_names:
                raise PolicyFactoryError(f"any_of policy '{config.name}' requires 'policies' list")
            children = [self._resolve(name, config.name) for name in child_names]
            return AnyOf(*children, name=config.name)

        if config.type == "not":
            child_name = config.config.get("policy")
            if not child_name:
                raise PolicyFactoryError(f"not policy '{config.name}' requires 'policy' reference")
            child = self._resolve(child_name, config.name)
            deny_reason = config.config.get("deny_reason", "Policy condition not met")
            return Not(child, name=config.name, deny_reason=deny_reason)

        raise PolicyFactoryError(f"Unknown composite type: '{config.type}'")

    def _resolve(self, name: str, referrer: str) -> Policy:
        """Resolve policy reference by name.

        Args:
            name: Name of policy to resolve
            referrer: Name of policy making the reference (for error messages)

        Returns:
            Resolved policy instance

        Raises:
            PolicyFactoryError: If policy not found
        """
        if name not in self._instances:
            raise PolicyFactoryError(
                f"Policy '{referrer}' references '{name}', but '{name}' is not defined. "
                f"Ensure '{name}' is defined before '{referrer}' in the policies list."
            )
        return self._instances[name]

    def get_instance(self, name: str) -> Policy | None:
        """Get a created policy instance by name.

        Args:
            name: Policy name

        Returns:
            Policy instance or None if not found
        """
        return self._instances.get(name)
