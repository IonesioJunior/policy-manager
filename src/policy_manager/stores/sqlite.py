"""SQLiteStore — durable, single-file storage backend using aiosqlite."""

from __future__ import annotations

import json
from typing import Any

try:
    import aiosqlite
except ImportError as exc:
    raise ImportError(
        "SQLiteStore requires the 'aiosqlite' package. "
        "Install it with: pip install policy-manager[sqlite]"
    ) from exc

from policy_manager.stores.base import Store

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS policy_store (
    namespace TEXT NOT NULL,
    key       TEXT NOT NULL,
    value     TEXT NOT NULL,
    PRIMARY KEY (namespace, key)
)
"""


class SQLiteStore(Store):
    """Persistent store backed by a single SQLite file.

    Parameters:
        db_path: Path to the SQLite database file.  Use ``":memory:"``
                 for an in-memory database (useful for testing).
    """

    def __init__(self, db_path: str = "policy_store.db") -> None:
        self._db_path = db_path
        self._db: aiosqlite.Connection | None = None

    async def _connect(self) -> aiosqlite.Connection:
        if self._db is None:
            self._db = await aiosqlite.connect(self._db_path)
            await self._db.execute(_CREATE_TABLE)
            await self._db.commit()
        return self._db

    async def close(self) -> None:
        if self._db:
            await self._db.close()
            self._db = None

    # ── Store protocol ───────────────────────────────────────

    async def get(self, namespace: str, key: str) -> dict[str, Any] | None:
        db = await self._connect()
        cursor = await db.execute(
            "SELECT value FROM policy_store WHERE namespace = ? AND key = ?",
            (namespace, key),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        result: dict[str, Any] = json.loads(row[0])
        return result

    async def set(self, namespace: str, key: str, value: dict[str, Any]) -> None:
        db = await self._connect()
        await db.execute(
            "INSERT OR REPLACE INTO policy_store (namespace, key, value) VALUES (?, ?, ?)",
            (namespace, key, json.dumps(value)),
        )
        await db.commit()

    async def delete(self, namespace: str, key: str) -> None:
        db = await self._connect()
        await db.execute(
            "DELETE FROM policy_store WHERE namespace = ? AND key = ?",
            (namespace, key),
        )
        await db.commit()

    async def list_keys(self, namespace: str) -> list[str]:
        db = await self._connect()
        cursor = await db.execute(
            "SELECT key FROM policy_store WHERE namespace = ?",
            (namespace,),
        )
        rows = await cursor.fetchall()
        return [row[0] for row in rows]

    async def exists(self, namespace: str, key: str) -> bool:
        db = await self._connect()
        cursor = await db.execute(
            "SELECT 1 FROM policy_store WHERE namespace = ? AND key = ?",
            (namespace, key),
        )
        return (await cursor.fetchone()) is not None

    async def clear_namespace(self, namespace: str) -> None:
        db = await self._connect()
        await db.execute(
            "DELETE FROM policy_store WHERE namespace = ?",
            (namespace,),
        )
        await db.commit()
