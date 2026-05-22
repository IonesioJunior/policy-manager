"""X402PayPerRequestPolicy — pay-per-request gate over Tempo USDC transfers.

The crypto layer (HMAC challenge signing, ERC-20 transfer encoding, on-chain
RPC) lives in the Go SDK (``sdk/golang/syfthubapi/mppxgate``).  This policy is
the orchestration shell:

* ``pre_execute`` either issues a challenge **spec** (no secrets — just the
  parameters the caller needs to build a signed transfer) or accepts a
  Go-verified credential and records a ``verified`` row using the Go-supplied
  canonical challenge id as the row's primary key.
* ``post_execute`` updates the row with the on-chain receipt or a failure
  reason.  It is accounting-only and always allows the request.

Settled rows live in a dedicated ``x402_transactions`` SQLite table that
shares the executor's store file (mirroring :mod:`manual_review`).  Any
external process — Wails desktop UI, CLI inspectors — can read it.

Protocol with the Go gate
-------------------------
Round 1 — no ``payment_credential`` in ``context.metadata``:
    Python emits a pending result with ``metadata['x402_challenge_spec']``.
    No DB write is performed; the Go gate is responsible for materializing
    the HMAC-bound challenge from this spec and returning HTTP 402 to the
    caller.

Round 2 — Go gate verified a credential:
    The Go gate sets ``metadata['payment_verified'] = True``,
    ``metadata['payment_challenge_id']`` (the canonical HMAC-derived id),
    and ``metadata['payment_nonce']`` (uint64).  Python inserts a row whose
    primary key **is** ``payment_challenge_id``.  This guarantees Python's
    row id matches whatever the Go gate / receipt later references.

Post-execute:
    The Go gate sets ``metadata['payment_receipt']`` (with ``reference`` =
    on-chain tx hash) or ``metadata['payment_failure']`` (with ``reason``),
    plus ``metadata['payment_challenge_id']``.  Python looks the row up by
    that canonical id and writes the final state.

The HMAC secret itself never crosses this boundary — Python only stores a
``hmac_secret_kid`` (key id).  The Go gate reads the actual secret
out-of-band from its own keystore / config keyed by that id.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import aiosqlite

from policy_manager.policies.base import Policy
from policy_manager.result import PolicyResult
from policy_manager.stores.sqlite import SQLiteStore

if TYPE_CHECKING:
    from policy_manager.context import RequestContext
    from policy_manager.stores.base import Store


_DEFAULT_DB_PATH = "x402_transactions.db"

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS x402_transactions (
    id              TEXT PRIMARY KEY,
    policy_name     TEXT NOT NULL,
    payer           TEXT NOT NULL,
    pay_to          TEXT NOT NULL,
    amount          TEXT NOT NULL,
    currency        TEXT NOT NULL,
    chain_id        INTEGER NOT NULL,
    nonce           INTEGER,
    status          TEXT NOT NULL,
    failure_reason  TEXT,
    tx_hash         TEXT,
    created_at      TEXT NOT NULL,
    settled_at      TEXT
)
"""

_CREATE_INDEX_STATUS = """
CREATE INDEX IF NOT EXISTS idx_x402_status
ON x402_transactions (status, created_at)
"""

_CREATE_INDEX_PAYER = """
CREATE INDEX IF NOT EXISTS idx_x402_payer
ON x402_transactions (payer, created_at)
"""


# Row statuses.  The previous "issued" state was removed: Python no longer
# pre-allocates rows for un-paid challenges, so the row only exists once a
# verified credential has arrived.
_STATUS_VERIFIED = "verified"  # Go gate verified the credential, awaiting settle
_STATUS_SETTLED = "settled"  # on-chain receipt recorded
_STATUS_FAILED = "failed"  # settlement failed


class X402PayPerRequestPolicy(Policy):
    """Pay-per-request gate over MPP/Tempo signed-transfer credentials.

    The policy itself does no crypto.  On ``pre_execute`` it either:

    * Lets allow-listed payers through (free tier), with no DB write.
    * Accepts a Go-verified credential (``metadata['payment_verified']``)
      and records a ``verified`` row keyed by the Go-supplied canonical
      ``payment_challenge_id``.
    * Otherwise returns a pending result with a challenge **spec** in
      ``metadata['x402_challenge_spec']`` that the Go gate consumes to
      sign and present an HTTP 402 to the caller.  **No DB write** —
      Python cannot know the canonical challenge id (the Go gate
      computes it via HMAC over the spec using a secret Python never
      sees), so pre-allocating a row would orphan it forever.

    On ``post_execute`` it folds in the settlement result and always
    allows the request (the post hook is accounting-only).

    Args:
        name: Unique policy instance name.
        pay_to: Recipient EVM address (0x...).
        price: Decimal price string, e.g. ``"0.01"``.  Converted to
            base units using ``decimals``.
        currency: ERC-20 token contract address (0x...).
        decimals: Token decimals (pathUSD = 6).
        chain_id: EVM chain id (Tempo testnet = 42431).
        realm: Logical realm string used by the challenge, e.g.
            ``"syfthub:endpoint:<slug>:<policy_name>"``.
        hmac_secret_kid: Opaque id the Go gate uses to look up the HMAC
            secret in its own keystore.  This is *not* a secret itself
            (it's just a lookup key, like a JWT ``kid``) and is included
            in :meth:`export` so config consumers know which key the Go
            gate will use.  The secret material never lives in this
            process.
        challenge_ttl_seconds: How long a challenge is valid for.
        max_pending_settlements_per_payer: Maximum number of ``verified``
            (paid but not yet settled) rows a single payer may accumulate
            before further verified credentials are denied.  Protects
            against a producer that accepts payments but never settles.
        allow_listed_payers: Optional list of payer ids that bypass
            payment entirely (free tier).
    """

    _policy_type = "x402_pay_per_request"
    _policy_description = "Pay-per-request gate via x402 / Tempo USDC"

    def __init__(
        self,
        *,
        name: str,
        pay_to: str,
        price: str,
        currency: str,
        decimals: int = 6,
        chain_id: int = 42431,
        realm: str,
        hmac_secret_kid: str = "default",
        challenge_ttl_seconds: int = 300,
        max_pending_settlements_per_payer: int = 16,
        allow_listed_payers: list[str] | None = None,
    ) -> None:
        self._name = name
        self._pay_to = pay_to
        self._price = price
        self._currency = currency
        self._decimals = decimals
        self._chain_id = chain_id
        self._realm = realm
        self._hmac_secret_kid = hmac_secret_kid
        self._challenge_ttl_seconds = challenge_ttl_seconds
        self._max_pending_settlements_per_payer = max_pending_settlements_per_payer
        self._allow_listed_payers: set[str] = set(allow_listed_payers or [])

        self._db_path: str | None = None
        self._db: aiosqlite.Connection | None = None

    @property
    def name(self) -> str:
        return self._name

    # ── lifecycle ─────────────────────────────────────────────

    async def setup(self, store: Store) -> None:
        """Resolve the DB path (reuses the executor's store file when SQLite)
        and create the ``x402_transactions`` table.
        """
        await super().setup(store)
        if self._db_path is None:
            self._db_path = (
                store.db_path if isinstance(store, SQLiteStore) else _DEFAULT_DB_PATH
            )
        await self._connect()

    async def _connect(self) -> aiosqlite.Connection:
        if self._db is None:
            assert self._db_path is not None
            self._db = await aiosqlite.connect(self._db_path)
            # WAL + busy_timeout let external processes (Wails, CLI inspectors)
            # read and update rows concurrently with the runner.
            await self._db.execute("PRAGMA journal_mode=WAL")
            await self._db.execute("PRAGMA busy_timeout=5000")
            await self._db.execute(_CREATE_TABLE)
            await self._db.execute(_CREATE_INDEX_STATUS)
            await self._db.execute(_CREATE_INDEX_PAYER)
            await self._db.commit()
        return self._db

    async def close(self) -> None:
        """Close the underlying SQLite connection.  Called by PolicyManager."""
        if self._db is not None:
            await self._db.close()
            self._db = None

    # ── export ────────────────────────────────────────────────

    def export(self) -> dict[str, Any]:
        data = super().export()
        # ``hmac_secret_kid`` is a key *id*, not a key — safe (and useful)
        # to export so config consumers can see which key the Go gate will
        # look up.  The secret material itself is never stored on this
        # policy and therefore cannot leak via export.
        data["config"] = {
            "pay_to": self._pay_to,
            "price": self._price,
            "currency": self._currency,
            "decimals": self._decimals,
            "chain_id": self._chain_id,
            "realm": self._realm,
            "hmac_secret_kid": self._hmac_secret_kid,
            "challenge_ttl_seconds": self._challenge_ttl_seconds,
            "max_pending_settlements_per_payer": (
                self._max_pending_settlements_per_payer
            ),
            "allow_listed_payers": sorted(self._allow_listed_payers),
        }
        return data

    # ── hooks ─────────────────────────────────────────────────

    async def pre_execute(self, context: RequestContext) -> PolicyResult:
        # Free tier: allow-listed payers skip the whole flow — no DB write,
        # no metadata changes (the Go gate has nothing to do for them).
        if context.user_id in self._allow_listed_payers:
            return PolicyResult.allow(self.name)

        meta = context.metadata or {}

        # Round 2: Go gate already verified a payment credential.
        if meta.get("payment_verified") is True:
            return await self._handle_verified_credential(context, meta)

        # Round 1: no credential — return the challenge spec and let the
        # Go gate materialize the HMAC-bound challenge.  Crucially, we do
        # NOT pre-allocate a DB row here: Python cannot derive the
        # canonical challenge id (that's what the Go gate's HMAC secret is
        # for), so any row we wrote now would never match round-2 lookups.
        now = datetime.now(UTC)
        expires_at = now + timedelta(seconds=self._challenge_ttl_seconds)
        spec = {
            "pay_to": self._pay_to,
            "currency": self._currency,
            "decimals": self._decimals,
            "chain_id": self._chain_id,
            "amount": self._amount_base_units(),
            "realm": self._realm,
            "expires_at_iso": expires_at.isoformat(),
        }
        return PolicyResult.pend(
            self.name,
            reason="payment_required",
            x402_challenge_spec=spec,
        )

    async def _handle_verified_credential(
        self, context: RequestContext, meta: dict[str, Any]
    ) -> PolicyResult:
        """Insert a verified row keyed by the Go-supplied canonical id.

        Enforces the per-payer pending-settlement ceiling *before* the
        insert, so a payer cannot use a malicious producer (that never
        settles) to lock themselves out indefinitely beyond the cap.
        """
        challenge_id = meta.get("payment_challenge_id")
        if not isinstance(challenge_id, str) or not challenge_id:
            return PolicyResult.deny(
                self.name,
                "payment_verified set without payment_challenge_id",
            )

        nonce = meta.get("payment_nonce")
        try:
            nonce_int = int(nonce) if nonce is not None else None
        except (TypeError, ValueError):
            nonce_int = None

        # Idempotency: if the canonical id already exists (replay of the
        # same challenge), skip the cap check and return allow — the row
        # is already there.  This makes the second call a no-op rather
        # than a spurious denial when the Go gate retries.
        existing = await self.get_transaction(challenge_id)
        if existing is not None:
            return PolicyResult(
                allowed=True,
                policy_name=self.name,
                metadata={"x402_settlement_id": challenge_id},
            )

        pending = await self._count_pending_settlements(context.user_id)
        if pending >= self._max_pending_settlements_per_payer:
            return PolicyResult.deny(
                self.name,
                "too many unsettled payments; wait for settlement",
                pending_settlements=pending,
                max_pending_settlements_per_payer=(
                    self._max_pending_settlements_per_payer
                ),
            )

        await self._insert_row(
            row_id=challenge_id,
            payer=context.user_id,
            amount=self._amount_base_units(),
            nonce=nonce_int,
            status=_STATUS_VERIFIED,
            created_at=datetime.now(UTC).isoformat(),
        )
        return PolicyResult(
            allowed=True,
            policy_name=self.name,
            metadata={"x402_settlement_id": challenge_id},
        )

    async def post_execute(self, context: RequestContext) -> PolicyResult:
        meta = context.metadata or {}
        receipt = meta.get("payment_receipt")
        failure = meta.get("payment_failure")
        challenge_id = meta.get("payment_challenge_id")

        # post_execute is accounting-only — never block the response.
        if receipt is None and failure is None:
            return PolicyResult.allow(self.name)

        # Without the canonical challenge id we cannot pinpoint the row;
        # skip silently rather than guessing.
        if not isinstance(challenge_id, str) or not challenge_id:
            return PolicyResult.allow(self.name)

        db = await self._connect()
        now_iso = datetime.now(UTC).isoformat()

        if receipt is not None:
            tx_hash = self._extract_tx_hash(receipt)
            await db.execute(
                """
                UPDATE x402_transactions
                SET status = ?, tx_hash = ?, settled_at = ?, failure_reason = NULL
                WHERE id = ? AND policy_name = ?
                """,
                (_STATUS_SETTLED, tx_hash, now_iso, challenge_id, self._name),
            )
            await db.commit()
        else:
            assert failure is not None  # narrowing for type checkers
            reason = (
                failure.get("reason")
                if isinstance(failure, dict)
                else str(failure)
            )
            await db.execute(
                """
                UPDATE x402_transactions
                SET status = ?, failure_reason = ?, settled_at = ?
                WHERE id = ? AND policy_name = ?
                """,
                (_STATUS_FAILED, reason, now_iso, challenge_id, self._name),
            )
            await db.commit()

        return PolicyResult.allow(self.name)

    # ── helpers ───────────────────────────────────────────────

    @staticmethod
    def _extract_tx_hash(receipt: Any) -> str | None:
        """Pull the on-chain reference out of a receipt dict.

        The Go gate uses ``reference`` as the canonical key (matches its
        ``Receipt`` struct); ``tx_hash`` is accepted as a fallback for
        ergonomic Python callers / tests.
        """
        if not isinstance(receipt, dict):
            return None
        value = receipt.get("reference") or receipt.get("tx_hash")
        return value if isinstance(value, str) else None

    def _amount_base_units(self) -> str:
        """Convert ``price * 10**decimals`` to a base-units integer string.

        ``price`` is a decimal string; we avoid float for precision.
        """
        if "." in self._price:
            whole, frac = self._price.split(".", 1)
        else:
            whole, frac = self._price, ""
        # Right-pad / truncate the fractional part to exactly ``decimals``.
        if len(frac) > self._decimals:
            frac = frac[: self._decimals]
        else:
            frac = frac.ljust(self._decimals, "0")
        combined = (whole or "0").lstrip("+") + frac
        # Strip leading zeros but keep at least one digit.
        normalized = combined.lstrip("0") or "0"
        return normalized

    async def _insert_row(
        self,
        *,
        row_id: str,
        payer: str,
        amount: str,
        nonce: int | None,
        status: str,
        created_at: str,
    ) -> None:
        db = await self._connect()
        # INSERT OR IGNORE: if a row with this canonical id already exists
        # (e.g. the Go gate retried after a network blip), keep the
        # original row untouched.  Combined with the explicit
        # ``get_transaction`` check in ``_handle_verified_credential``,
        # this makes round-2 idempotent.
        await db.execute(
            """
            INSERT OR IGNORE INTO x402_transactions
                (id, policy_name, payer, pay_to, amount, currency, chain_id,
                 nonce, status, failure_reason, tx_hash,
                 created_at, settled_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?, NULL)
            """,
            (
                row_id,
                self._name,
                payer,
                self._pay_to,
                amount,
                self._currency,
                self._chain_id,
                nonce,
                status,
                created_at,
            ),
        )
        await db.commit()

    async def _count_pending_settlements(self, payer: str) -> int:
        db = await self._connect()
        cursor = await db.execute(
            """
            SELECT COUNT(*) FROM x402_transactions
            WHERE payer = ? AND policy_name = ? AND status = ?
            """,
            (payer, self._name, _STATUS_VERIFIED),
        )
        row = await cursor.fetchone()
        return int(row[0]) if row else 0

    # ── management helpers (used by tests + Wails readers) ────

    async def list_transactions(
        self,
        *,
        payer: str | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Return recent ledger rows, optionally filtered by payer / status."""
        db = await self._connect()
        clauses: list[str] = ["policy_name = ?"]
        params: list[Any] = [self._name]
        if payer is not None:
            clauses.append("payer = ?")
            params.append(payer)
        if status is not None:
            clauses.append("status = ?")
            params.append(status)
        where = " AND ".join(clauses)
        params.append(int(limit))
        db.row_factory = aiosqlite.Row
        try:
            cursor = await db.execute(
                f"SELECT * FROM x402_transactions WHERE {where} "
                "ORDER BY created_at DESC LIMIT ?",
                params,
            )
            rows = await cursor.fetchall()
        finally:
            db.row_factory = None
        return [dict(r) for r in rows]

    async def get_transaction(self, transaction_id: str) -> dict[str, Any] | None:
        """Return a single ledger row by id, or ``None`` if absent."""
        db = await self._connect()
        db.row_factory = aiosqlite.Row
        try:
            cursor = await db.execute(
                "SELECT * FROM x402_transactions WHERE id = ? AND policy_name = ?",
                (transaction_id, self._name),
            )
            row = await cursor.fetchone()
        finally:
            db.row_factory = None
        return dict(row) if row else None
