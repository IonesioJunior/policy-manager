"""AccessGroupPolicy — controls *who* can access *what* documents/resources."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from policy_manager.policies.base import Policy
from policy_manager.result import PolicyResult

if TYPE_CHECKING:
    from policy_manager.context import RequestContext
    from policy_manager.stores.base import Store

_NS_PREFIX = "access_group"


class AccessGroupPolicy(Policy):
    """A policy that gates access based on user membership in a group.

    On ``pre_execute``:
    * If the user is a member → writes ``context.metadata["resolved_documents"]``
      with the list of document IDs this group grants, then returns **allow**.
    * If the user is **not** a member → returns **deny**.

    Membership and document lists are persisted in the store so they survive
    restarts and can be mutated at runtime via ``add_users`` / ``add_documents``.
    """

    def __init__(
        self,
        *,
        name: str,
        owner: str = "",
        users: list[str] | None = None,
        documents: list[str] | None = None,
    ) -> None:
        self._name = name
        self._owner = owner
        self._users: set[str] = set(users or [])
        self._documents: list[str] = list(documents or [])
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
            "owner": self._owner,
            "users": sorted(self._users),
            "documents": list(self._documents),
        }
        return data

    # ── lifecycle ────────────────────────────────────────────

    async def setup(self, store: Store) -> None:
        await super().setup(store)
        await self._sync_to_store()

    async def _sync_to_store(self) -> None:
        """Persist the initial configuration into the store."""
        await self.store.set(
            self.namespace,
            "_config",
            {
                "owner": self._owner,
                "users": sorted(self._users),
                "documents": self._documents,
            },
        )
        self._synced = True

    async def _load_from_store(self) -> None:
        cfg = await self.store.get(self.namespace, "_config")
        if cfg:
            self._owner = cfg.get("owner", self._owner)
            self._users = set(cfg.get("users", []))
            self._documents = list(cfg.get("documents", []))

    # ── evaluation ───────────────────────────────────────────

    async def pre_execute(self, context: RequestContext) -> PolicyResult:
        if not self._synced:
            await self._load_from_store()

        if context.user_id not in self._users:
            return PolicyResult.deny(
                self.name,
                f"User '{context.user_id}' is not a member of access group '{self._name}'",
            )

        # Accumulate documents into metadata so downstream policies can use them
        existing: list[str] = context.metadata.get("resolved_documents", [])
        merged = list(dict.fromkeys(existing + self._documents))  # dedupe, preserve order
        context.metadata["resolved_documents"] = merged

        return PolicyResult.allow(self.name)

    # ── runtime management ───────────────────────────────────

    async def add_users(self, user_ids: list[str]) -> None:
        self._users.update(user_ids)
        if self._synced:
            await self._sync_to_store()

    async def remove_users(self, user_ids: list[str]) -> None:
        self._users -= set(user_ids)
        if self._synced:
            await self._sync_to_store()

    async def add_documents(self, doc_ids: list[str]) -> None:
        for doc_id in doc_ids:
            if doc_id not in self._documents:
                self._documents.append(doc_id)
        if self._synced:
            await self._sync_to_store()

    async def remove_documents(self, doc_ids: list[str]) -> None:
        self._documents = [d for d in self._documents if d not in set(doc_ids)]
        if self._synced:
            await self._sync_to_store()

    def get_users(self) -> set[str]:
        return set(self._users)

    def get_documents(self) -> list[str]:
        return list(self._documents)
