"""Store protocol — generic key-value persistence for policy state."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class Store(ABC):
    """Abstract base for all storage backends.

    Each policy manages its own *namespace* (e.g. ``"rate_limit:standard"``).
    The store is completely agnostic to what is being stored — it just
    persists ``dict[str, Any]`` blobs keyed by ``(namespace, key)``.
    """

    @abstractmethod
    async def get(self, namespace: str, key: str) -> dict[str, Any] | None:
        """Return the stored value, or ``None`` if not found."""
        ...

    @abstractmethod
    async def set(self, namespace: str, key: str, value: dict[str, Any]) -> None:
        """Create or overwrite a value."""
        ...

    @abstractmethod
    async def delete(self, namespace: str, key: str) -> None:
        """Delete a value.  No-op if the key does not exist."""
        ...

    @abstractmethod
    async def list_keys(self, namespace: str) -> list[str]:
        """Return all keys within a namespace."""
        ...

    @abstractmethod
    async def exists(self, namespace: str, key: str) -> bool:
        """Return ``True`` if the key exists in the namespace."""
        ...

    @abstractmethod
    async def clear_namespace(self, namespace: str) -> None:
        """Delete all keys within a namespace."""
        ...
