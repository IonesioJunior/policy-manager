"""ManualReviewPolicy — post-execution hold for human review.

Held requests are recorded in a dedicated ``manual_reviews`` SQLite table
and the response body is replaced with a short placeholder message.  Any
external process with access to the same database file can poll the table
(filtering on the ``pending`` column) and resolve entries out of band.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import aiosqlite

from policy_manager.policies.base import Policy
from policy_manager.result import PolicyResult
from policy_manager.stores.sqlite import SQLiteStore

if TYPE_CHECKING:
    from policy_manager.context import RequestContext
    from policy_manager.stores.base import Store


# Callback signature: (review_payload) -> {"approved": bool}
ReviewCallback = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]

_DEFAULT_DB_PATH = "manual_reviews.db"
_DEFAULT_MESSAGE = "Request submitted to manual review"

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS manual_reviews (
    review_id     TEXT PRIMARY KEY,
    policy_name   TEXT NOT NULL,
    user_id       TEXT,
    input         TEXT,
    output        TEXT,
    status        TEXT NOT NULL DEFAULT 'pending',
    pending       INTEGER NOT NULL DEFAULT 1,
    reject_reason TEXT,
    created_at    TEXT NOT NULL,
    resolved_at   TEXT
)
"""

_CREATE_INDEX = """
CREATE INDEX IF NOT EXISTS idx_manual_reviews_pending
ON manual_reviews (pending)
"""


class ManualReviewPolicy(Policy):
    """Holds responses for manual review before they reach the end user.

    On ``post_execute`` the policy records the real request/response in the
    ``manual_reviews`` table (``pending = 1``) and **substitutes** the
    response body with a short placeholder message.  The request still
    succeeds — the caller receives the placeholder, shaped to match the
    endpoint type (a plain string for model/agent endpoints, a
    single-document list for data_source endpoints) so it passes cleanly
    through the SDK's typed result codec.  The real handler output is held
    in the database for a human (or another process) to resolve.

    Resolution is out of band: any program with access to the same SQLite
    file can ``SELECT ... WHERE pending = 1`` and update rows to
    ``approved`` / ``rejected``.  :meth:`approve` and :meth:`reject` are
    convenience wrappers around that SQL.

    A ``review_callback`` (if provided) runs first — when it returns
    ``{"approved": True}`` the real handler response passes through
    unchanged and the row is recorded as already approved.

    Parameters:
        name:                Unique policy name.
        db_path:             SQLite file holding review records.  When
                             omitted, the policy reuses the executor's
                             SQLite store file (so reviews live alongside
                             policy state); falls back to
                             ``manual_reviews.db`` if the store is not
                             SQLite-backed.
        placeholder_message: Body returned to the caller while a request
                             is held for review.
        review_callback:     Optional async callable for automated review.
    """

    _policy_type = "manual_review"
    _policy_description = "Holds responses for manual review before delivery"

    def __init__(
        self,
        *,
        name: str = "manual_review",
        db_path: str | None = None,
        placeholder_message: str = _DEFAULT_MESSAGE,
        review_callback: ReviewCallback | None = None,
    ) -> None:
        self._name = name
        self._db_path = db_path
        self._placeholder_message = placeholder_message
        self._review_cb = review_callback
        self._db: aiosqlite.Connection | None = None

    @property
    def name(self) -> str:
        return self._name

    async def setup(self, store: Store) -> None:
        """Resolve the review database path and create the table.

        When ``db_path`` was not given explicitly, reuse the executor's
        SQLite store file so review records and policy state share a
        single database.
        """
        await super().setup(store)
        if self._db_path is None:
            self._db_path = (
                store.db_path if isinstance(store, SQLiteStore) else _DEFAULT_DB_PATH
            )
        await self._connect()

    async def _connect(self) -> aiosqlite.Connection:
        if self._db is None:
            assert self._db_path is not None  # set by __init__ or setup()
            self._db = await aiosqlite.connect(self._db_path)
            # WAL + a busy timeout let external processes read and resolve
            # rows concurrently with the (short-lived) runner process.
            await self._db.execute("PRAGMA journal_mode=WAL")
            await self._db.execute("PRAGMA busy_timeout=5000")
            await self._db.execute(_CREATE_TABLE)
            await self._db.execute(_CREATE_INDEX)
            await self._db.commit()
        return self._db

    async def close(self) -> None:
        """Close the review database connection.  Called by PolicyManager."""
        if self._db is not None:
            await self._db.close()
            self._db = None

    def export(self) -> dict[str, Any]:
        data = super().export()
        data["config"] = {
            "has_review_callback": self._review_cb is not None,
            "placeholder_message": self._placeholder_message,
        }
        return data

    async def post_execute(self, context: RequestContext) -> PolicyResult:
        review_id = uuid.uuid4().hex[:12]
        created_at = datetime.now(UTC).isoformat()

        # Automated review first — approval lets the real response through.
        if self._review_cb:
            payload = {
                "review_id": review_id,
                "user_id": context.user_id,
                "input": context.input,
                "output": context.output,
                "timestamp": context.timestamp.isoformat(),
                "status": "pending",
            }
            result = await self._review_cb(payload)
            if result.get("approved", False):
                await self._insert(
                    review_id,
                    context,
                    created_at,
                    status="approved",
                    resolved_at=created_at,
                )
                return PolicyResult.allow(self.name)

        # Otherwise record the real request/response and substitute the
        # response body with a placeholder.  The caller receives the
        # placeholder; the real output is held in the database for review.
        await self._insert(review_id, context, created_at, status="pending")

        return PolicyResult.substitute(
            self.name,
            output=self._placeholder_body(context, review_id),
            reason="Response held for manual review",
            review_id=review_id,
            status="pending",
        )

    def _placeholder_body(self, context: RequestContext, review_id: str) -> Any:
        """Build a placeholder response shaped to match the endpoint type.

        data_source endpoints return a list of documents; model and agent
        endpoints return a plain string.  Matching the native shape lets
        the placeholder pass through the SDK's typed result codec.
        """
        message = f"{self._placeholder_message} (reference: {review_id})"
        if context.input.get("type") == "data_source":
            return [
                {
                    "document_id": f"manual-review:{review_id}",
                    "content": message,
                    "metadata": {"review_id": review_id, "status": "pending"},
                }
            ]
        return message

    # ── persistence ──────────────────────────────────────────

    async def _insert(
        self,
        review_id: str,
        context: RequestContext,
        created_at: str,
        *,
        status: str,
        resolved_at: str | None = None,
    ) -> None:
        db = await self._connect()
        await db.execute(
            """
            INSERT INTO manual_reviews
                (review_id, policy_name, user_id, input, output,
                 status, pending, reject_reason, created_at, resolved_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)
            """,
            (
                review_id,
                self._name,
                context.user_id,
                json.dumps(context.input),
                json.dumps(context.output),
                status,
                1 if status == "pending" else 0,
                created_at,
                resolved_at,
            ),
        )
        await db.commit()

    async def _resolve(
        self,
        review_id: str,
        *,
        status: str,
        reject_reason: str | None = None,
    ) -> bool:
        db = await self._connect()
        cursor = await db.execute(
            """
            UPDATE manual_reviews
            SET status = ?, pending = 0, reject_reason = ?, resolved_at = ?
            WHERE review_id = ?
            """,
            (status, reject_reason, datetime.now(UTC).isoformat(), review_id),
        )
        await db.commit()
        return cursor.rowcount > 0

    # ── management helpers ───────────────────────────────────

    async def approve(self, review_id: str) -> bool:
        """Mark a pending review as approved.  Returns False if not found."""
        return await self._resolve(review_id, status="approved")

    async def reject(self, review_id: str, reason: str = "") -> bool:
        """Mark a pending review as rejected.  Returns False if not found."""
        return await self._resolve(review_id, status="rejected", reject_reason=reason)

    async def get_pending(self) -> list[dict[str, Any]]:
        """Return all unresolved review entries (``pending = 1``)."""
        db = await self._connect()
        db.row_factory = aiosqlite.Row
        try:
            cursor = await db.execute(
                "SELECT * FROM manual_reviews WHERE pending = 1 ORDER BY created_at"
            )
            rows = await cursor.fetchall()
        finally:
            db.row_factory = None
        return [self._row_to_dict(row) for row in rows]

    @staticmethod
    def _row_to_dict(row: aiosqlite.Row) -> dict[str, Any]:
        entry = dict(row)
        entry["input"] = json.loads(entry["input"]) if entry["input"] else {}
        entry["output"] = json.loads(entry["output"]) if entry["output"] else {}
        return entry
