"""InMemoryStore — zero-config, dict-backed storage for development and testing."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from policy_manager.stores.base import Store


class InMemoryStore(Store):
    """In-memory store using nested dicts.  Data is lost on process exit."""

    def __init__(self) -> None:
        self._data: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)

    async def get(self, namespace: str, key: str) -> dict[str, Any] | None:
        return self._data[namespace].get(key)

    async def set(self, namespace: str, key: str, value: dict[str, Any]) -> None:
        self._data[namespace][key] = value

    async def delete(self, namespace: str, key: str) -> None:
        self._data[namespace].pop(key, None)

    async def list_keys(self, namespace: str) -> list[str]:
        return list(self._data[namespace].keys())

    async def exists(self, namespace: str, key: str) -> bool:
        return key in self._data[namespace]

    async def clear_namespace(self, namespace: str) -> None:
        self._data.pop(namespace, None)
