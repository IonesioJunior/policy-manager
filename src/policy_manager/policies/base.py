"""Policy ABC — the single abstraction that everything implements."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, ClassVar

from policy_manager.result import PolicyResult

if TYPE_CHECKING:
    from policy_manager.context import RequestContext
    from policy_manager.stores.base import Store


class Policy(ABC):
    """Base class for every policy.

    Subclasses **must** define a ``name`` property (or class attribute).

    Override ``pre_execute`` to run logic *before* the user's function.
    Override ``post_execute`` to run logic *after* the user's function.

    Default implementations return ``PolicyResult.allow`` (pass-through),
    so you only need to override the hook(s) you care about.

    Policies may:
    * Read from ``context.input``, ``context.output``, ``context.metadata``.
    * **Write** to ``context.metadata`` to pass data to downstream policies.
    * Use ``self.store`` for persistent state (injected by the manager).

    Class Variables:
        _policy_type: Type identifier for serialization (e.g., "rate_limit").
        _policy_version: Version string for the policy schema.
        _policy_description: Human-readable description of the policy.
    """

    _policy_type: ClassVar[str] = "base"
    _policy_version: ClassVar[str] = "1.0"
    _policy_description: ClassVar[str] = ""

    store: Store

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique identifier for this policy instance."""
        ...

    async def pre_execute(self, context: RequestContext) -> PolicyResult:
        """Called during the pre-execution chain.  Override to implement."""
        return PolicyResult.allow(self.name)

    async def post_execute(self, context: RequestContext) -> PolicyResult:
        """Called during the post-execution chain.  Override to implement."""
        return PolicyResult.allow(self.name)

    async def setup(self, store: Store) -> None:
        """Called once when the policy is registered with the manager.

        Use this to load initial state, validate configuration, etc.
        The default implementation just stores the reference.
        """
        self.store = store

    # ── introspection ─────────────────────────────────────────

    def _detect_phases(self) -> list[str]:
        """Return which phases this policy overrides (``"pre"`` / ``"post"``)."""
        phases: list[str] = []
        if type(self).pre_execute is not Policy.pre_execute:
            phases.append("pre")
        if type(self).post_execute is not Policy.post_execute:
            phases.append("post")
        return phases

    def export(self) -> dict[str, Any]:
        """Return a JSON-serializable snapshot of this policy.

        Subclasses should call ``super().export()`` and populate the
        ``"config"`` key in the returned dict.

        The export format is SyftHub-compatible, including:
        - name: Policy instance name
        - type: Policy type identifier (e.g., "rate_limit", "transaction")
        - version: Schema version
        - enabled: Whether the policy is active
        - description: Human-readable description
        - phase: Which execution phases this policy runs in
        - config: Policy-specific configuration
        """
        return {
            "name": self.name,
            "type": self._policy_type,
            "version": self._policy_version,
            "enabled": True,
            "description": self._policy_description,
            "phase": self._detect_phases(),
            "config": {},
        }
