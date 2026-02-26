"""BundleSubscriptionPolicy — gates access behind an active subscription."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from policy_manager.policies.base import Policy
from policy_manager.result import PolicyResult

if TYPE_CHECKING:
    from policy_manager.context import RequestContext
    from policy_manager.stores.base import Store

_NS_PREFIX = "bundle_subscription"


class BundleSubscriptionPolicy(Policy):
    """A policy that gates access based on active subscription membership.

    Structurally identical to ``AccessGroupPolicy``: the allowed-subscriber
    list is persisted in the store and can be mutated at runtime via
    ``add_users`` / ``remove_users``.  An external payment component is
    responsible for calling those methods when a subscription is created or
    cancelled — this policy does **not** handle payments.

    On ``pre_execute``:
    * If the user is an active subscriber → returns **allow**.
    * If the user is **not** a subscriber → returns **deny**.

    The subscription plan metadata (``plan_name``, ``price``, ``currency``,
    ``billing_cycle``, ``invoice_url``) is carried for export to SyftHub and
    is not used during access evaluation.

    Parameters:
        name:          Unique policy name.
        users:         Initial list of subscribed user IDs.
        plan_name:     Display name of the subscription plan (e.g. ``"Pro"``).
        price:         Numeric price amount.
        currency:      ISO currency code. Defaults to ``"USD"``.
        billing_cycle: One of ``"one_time"``, ``"monthly"``, ``"yearly"``
                       (or any string — not validated).
        invoice_url:   External billing URL shown to unsubscribed users.
    """

    _policy_type = "bundle_subscription"
    _policy_description = "Gates access behind an active subscription"

    def __init__(
        self,
        *,
        name: str,
        users: list[str] | None = None,
        plan_name: str = "",
        price: float = 0.0,
        currency: str = "USD",
        billing_cycle: str = "",
        invoice_url: str = "",
    ) -> None:
        self._name = name
        self._users: set[str] = set(users or [])
        self._plan_name = plan_name
        self._price = price
        self._currency = currency
        self._billing_cycle = billing_cycle
        self._invoice_url = invoice_url
        self._synced = False

    @property
    def name(self) -> str:
        return self._name

    @property
    def namespace(self) -> str:
        return f"{_NS_PREFIX}:{self._name}"

    def export(self) -> dict[str, Any]:
        data = super().export()
        data["config"] = {
            "plan_name": self._plan_name,
            "price": self._price,
            "currency": self._currency,
            "billing_cycle": self._billing_cycle,
            "invoice_url": self._invoice_url,
        }
        return data

    # ── lifecycle ────────────────────────────────────────────

    async def setup(self, store: Store) -> None:
        await super().setup(store)
        await self._sync_to_store()

    async def _sync_to_store(self) -> None:
        """Persist the initial subscriber list into the store."""
        await self.store.set(
            self.namespace,
            "_config",
            {"users": sorted(self._users)},
        )
        self._synced = True

    async def _load_from_store(self) -> None:
        cfg = await self.store.get(self.namespace, "_config")
        if cfg:
            self._users = set(cfg.get("users", []))

    # ── evaluation ───────────────────────────────────────────

    async def pre_execute(self, context: RequestContext) -> PolicyResult:
        if not self._synced:
            await self._load_from_store()

        if context.user_id not in self._users:
            return PolicyResult.deny(
                self.name,
                f"User '{context.user_id}' does not have an active subscription"
                + (f" to plan '{self._plan_name}'" if self._plan_name else ""),
            )

        return PolicyResult.allow(self.name)

    # ── runtime management ───────────────────────────────────

    async def add_users(self, user_ids: list[str]) -> None:
        """Add subscribers (called by the external payment component)."""
        self._users.update(user_ids)
        if self._synced:
            await self._sync_to_store()

    async def remove_users(self, user_ids: list[str]) -> None:
        """Remove subscribers (called on cancellation or expiry)."""
        self._users -= set(user_ids)
        if self._synced:
            await self._sync_to_store()

    def get_users(self) -> set[str]:
        return set(self._users)
