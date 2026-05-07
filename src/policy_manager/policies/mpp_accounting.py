"""MppAccountingPolicy — per-query payments via the Machine Payments Protocol (MPP)."""

from __future__ import annotations

import fnmatch
import os
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any

from policy_manager.exceptions import PaymentRequiredError, PolicyConfigError
from policy_manager.policies.base import Policy
from policy_manager.result import PolicyResult

if TYPE_CHECKING:
    from policy_manager.context import RequestContext

try:
    from mpp.server import Mpp  # type: ignore[import-untyped]

    _MPP_AVAILABLE = True
except ImportError:
    _MPP_AVAILABLE = False


PriceLike = Decimal | int | float | str


def _coerce_price(value: PriceLike) -> Decimal:
    """Convert a price-like value into ``Decimal`` without binary-fp drift.

    Floats are routed through ``str()`` first so ``0.1`` becomes
    ``Decimal("0.1")`` rather than ``Decimal("0.1000000000000000055511151...")``.
    """
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool):  # bool is an int subclass — reject explicitly
        raise ValueError(f"Boolean is not a valid price: {value!r}")
    if isinstance(value, int):
        return Decimal(value)
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError) as e:
        raise ValueError(f"Cannot interpret {value!r} as a price") from e


@dataclass
class _PricingTier:
    """A single pricing tier for MPP accounting.

    Attributes:
        price: Price per query as a ``Decimal``. ``0`` means free — payment logic
            is skipped before contacting the settlement layer.
        applied_to: List of user_id glob patterns this tier applies to.
    """

    price: Decimal
    applied_to: list[str]

    def matches(self, user_id: str) -> bool:
        return any(fnmatch.fnmatch(user_id, pat) for pat in self.applied_to)

    @property
    def specificity(self) -> int:
        """Higher specificity wins when multiple tiers match.

        Non-wildcard patterns score their character length; wildcard-only
        patterns score 0. Ties are broken by config order (Python's ``max``
        keeps the first-seen winner).
        """
        return max((len(p) for p in self.applied_to if "*" not in p), default=0)


class MppAccountingPolicy(Policy):
    """Per-query payment enforcement via the Machine Payments Protocol (MPP).

    Implements the HTTP 402 Payment Required challenge/credential flow on top
    of the Tempo blockchain. On first access (no ``X-Payment`` credential) the
    policy raises :exc:`~policy_manager.exceptions.PaymentRequiredError` with a
    signed HMAC challenge. The Go SDK forwards this as an HTTP 402 response. The
    client pays on-chain, receives a credential, and retries with
    ``x_payment`` in the request; the policy verifies and stores the receipt.

    Pricing is tiered: the most-specific matching tier wins. A price of ``0``
    is a free tier that skips all payment logic entirely. Prices are stored
    internally as :class:`~decimal.Decimal` to avoid binary-fp drift on money;
    ``str(price)`` is what's handed to the settlement layer and to receipts.

    Cross-process HMAC consistency (across the 402 → pay → retry round-trip)
    is provided by a stable ``secret_key`` — every subprocess builds a fresh
    ``Mpp`` from the same secret. The policy keeps a small *per-instance*
    cache of ``Mpp`` objects only to avoid reconstructing them when one
    ``PolicyManager`` evaluates multiple requests inside a single process.

    Parameters:
        name: Unique policy instance name. Defaults to ``"mpp_accounting"``.
        wallet_address: Endpoint owner's Tempo wallet address (``0x...``).
            Falls back to the ``MPP_WALLET_ADDRESS`` environment variable.
        realm: Endpoint identifier used as the MPP realm (typically the
            endpoint slug). Falls back to the ``MPP_REALM`` env var.
        pricing_tiers: List of dicts, each with:
            - ``"price"`` — cost per query. Accepts ``Decimal``, ``int``,
              ``float``, or numeric ``str``; floats are routed through
              ``str()`` to avoid fp drift.
            - ``"applied_to"`` (list[str]) — user_id glob patterns.
            Defaults to a single free-tier ``[{"price": 0, "applied_to": ["*"]}]``.
        testnet: Use Tempo testnet RPC when ``True`` (default). Set to
            ``False`` for mainnet.
        secret_key: HMAC secret used to sign and verify MPP challenges.
            **Required** when any tier has price > 0. Falls back to the
            ``MPP_SECRET_KEY`` environment variable.

    Raises:
        RuntimeError: If ``pympp`` is not installed.
            Install with ``pip install policy-manager[mpp]``.
        PolicyConfigError: If a paid tier is configured but no ``secret_key``
            (or ``MPP_SECRET_KEY`` env var) is supplied.

    Example:
        >>> policy = MppAccountingPolicy(
        ...     wallet_address="0xAbc123...",
        ...     realm="my-endpoint",
        ...     pricing_tiers=[
        ...         {"price": 0.0,  "applied_to": ["admin@example.com"]},
        ...         {"price": 0.01, "applied_to": ["premium@*"]},
        ...         {"price": 0.05, "applied_to": ["*"]},
        ...     ],
        ... )
    """

    _policy_type = "mpp_accounting"
    _policy_description = "Per-query payments via Machine Payments Protocol on Tempo blockchain"

    def __init__(
        self,
        *,
        name: str = "mpp_accounting",
        wallet_address: str | None = None,
        realm: str | None = None,
        pricing_tiers: list[dict[str, Any]] | None = None,
        testnet: bool = True,
        secret_key: str | None = None,
    ) -> None:
        if not _MPP_AVAILABLE:
            raise RuntimeError(
                "pympp is required for MppAccountingPolicy. "
                "Install with: pip install policy-manager[mpp]"
            )

        self._name = name
        self._wallet_address = wallet_address or os.getenv("MPP_WALLET_ADDRESS", "")
        self._realm = realm or os.getenv("MPP_REALM", "")
        self._testnet = testnet
        self._secret_key = secret_key or os.getenv("MPP_SECRET_KEY", "")

        raw_tiers: list[dict[str, Any]] = pricing_tiers or [
            {"price": 0, "applied_to": ["*"]}
        ]
        self._tiers: list[_PricingTier] = [
            _PricingTier(
                price=_coerce_price(t["price"]),
                applied_to=list(t.get("applied_to", ["*"])),
            )
            for t in raw_tiers
        ]

        # Fail-fast guard: a paid tier without a stable HMAC secret produces
        # silent wallet drains. The runner is invoked subprocess-per-request,
        # so the 402 → pay → retry round-trip crosses processes; without a
        # persistent secret_key, each Mpp() picks a fresh, mutually-incompatible
        # key and HMAC verification of the returned credential always fails,
        # returning a fresh challenge every time.
        if not self._secret_key and any(t.price > 0 for t in self._tiers):
            raise PolicyConfigError(
                self._name,
                "secret_key (or MPP_SECRET_KEY env var) is required when any "
                "pricing tier has price > 0; without it, HMAC verification "
                "cannot succeed across subprocess invocations.",
            )

        # Per-instance cache of Mpp server objects keyed by "{wallet}:{realm}".
        # Cross-process HMAC consistency is provided by ``secret_key``, NOT by
        # an in-memory cache — each subprocess builds a fresh Mpp from the same
        # secret. This cache only saves construction cost when a single
        # PolicyManager evaluates multiple requests in one process.
        self._mpp_cache: dict[str, Any] = {}

    @property
    def name(self) -> str:
        return self._name

    def _get_mpp(self) -> Any:
        """Return (creating if necessary) the Mpp server instance for this wallet+realm."""
        key = f"{self._wallet_address}:{self._realm}"
        cached = self._mpp_cache.get(key)
        if cached is None:
            cached = Mpp(  # type: ignore[name-defined]
                wallet_address=self._wallet_address,
                secret_key=self._secret_key,
                testnet=self._testnet,
            )
            self._mpp_cache[key] = cached
        return cached

    def invalidate_mpp_cache(self) -> None:
        """Drop any cached Mpp instances. Call after rotating wallet/secret_key."""
        self._mpp_cache.clear()

    @staticmethod
    def _extract_challenge(source: Any) -> str | None:
        """Pull a challenge string out of a Challenge-like object or exception.

        pympp signals "payment required" by either raising with ``challenge`` /
        ``www_authenticate`` set on the exception, or returning a Challenge
        object that exposes the same attributes. Anything without one of those
        attributes is treated as a non-challenge.
        """
        # Order matters: Challenge return objects use ``www_authenticate``,
        # while pympp's PaymentRequired exception uses ``challenge``. Probe the
        # serialized form first so a Challenge object whose framework also
        # exposes a richer ``challenge`` field still serializes cleanly.
        for attr in ("www_authenticate", "challenge"):
            value = getattr(source, attr, None)
            if value is not None:
                return str(value)
        return None

    def _resolve_tier(self, user_id: str) -> _PricingTier | None:
        """Return the most-specific pricing tier that matches ``user_id``.

        Returns ``None`` when no tier matches at all.
        """
        return max(
            (t for t in self._tiers if t.matches(user_id)),
            key=lambda t: t.specificity,
            default=None,
        )

    async def pre_execute(self, context: RequestContext) -> PolicyResult:
        """Enforce payment before handler execution.

        Steps:
        1. Find the pricing tier for this user. Deny if none matches.
        2. Allow immediately when price is 0 (free tier).
        3. Confirm wallet and realm are configured; deny otherwise.
        4. Call ``mpp.charge()``:

           - If no valid ``x_payment`` credential is present, the MPP library
             signals a challenge (either by returning a ``Challenge`` object or
             raising). We catch this and raise
             :exc:`~policy_manager.exceptions.PaymentRequiredError` so the
             executor can forward an HTTP 402 to the client.
           - On success, store the receipt and charged price in
             ``context.metadata`` for downstream policies and the executor.

        Args:
            context: The request context. Reads ``context.input["x_payment"]``
                for the credential forwarded from the ``X-Payment`` header.

        Returns:
            :meth:`~policy_manager.result.PolicyResult.allow` when payment is
            verified or the tier is free.

        Raises:
            PaymentRequiredError: When payment is needed and no valid credential
                was supplied. The executor translates this into
                ``RunnerOutput.payment_challenge`` for the Go SDK.
        """
        if not self._tiers:
            return PolicyResult.allow(self.name)

        tier = self._resolve_tier(context.user_id)
        if tier is None:
            return PolicyResult.deny(self.name, "No pricing tier matches your account")

        if tier.price == 0:
            return PolicyResult.allow(self.name)

        if not self._wallet_address:
            return PolicyResult.deny(
                self.name,
                "Endpoint owner has not configured a wallet address "
                "(set wallet_address or MPP_WALLET_ADDRESS env var)",
            )
        if not self._realm:
            return PolicyResult.deny(
                self.name,
                "MPP realm not configured (set realm or MPP_REALM env var)",
            )

        credential: str | None = context.input.get("x_payment")
        mpp = self._get_mpp()

        # pympp signals "payment required" two ways depending on version/path:
        #   (a) it raises, with the challenge attached to the exception, or
        #   (b) it returns a Challenge-like object instead of (cred, receipt).
        # Both map to PaymentRequiredError. Any *other* exception is genuine
        # infrastructure failure (network, RPC, malformed config) and must
        # propagate so the executor reports it as ExecutionError rather than
        # silently masquerading as a policy denial.
        try:
            result = mpp.charge(
                price=str(tier.price),
                realm=self._realm,
                authorization=credential,
            )
        except Exception as e:
            challenge = self._extract_challenge(e)
            if challenge is not None:
                raise PaymentRequiredError(realm=self._realm, challenge=challenge) from e
            raise

        if not isinstance(result, tuple):
            challenge = self._extract_challenge(result)
            if challenge is None:
                raise PaymentRequiredError(realm=self._realm, challenge=str(result))
            raise PaymentRequiredError(realm=self._realm, challenge=challenge)

        _, receipt = result
        context.metadata["mpp_payment_receipt"] = receipt
        context.metadata["mpp_price_charged"] = str(tier.price)
        return PolicyResult.allow(self.name)

    async def post_execute(self, context: RequestContext) -> PolicyResult:
        """Inject the payment receipt into handler output after execution.

        Injects ``context.output["_payment"]["receipt"]`` for callers that read
        the handler result directly (i.e. outside the runner). The runner also
        surfaces the receipt via ``RunnerOutput.payment_receipt``.
        """
        receipt = context.metadata.get("mpp_payment_receipt")
        if receipt is not None:
            context.output.setdefault("_payment", {})["receipt"] = receipt
        return PolicyResult.allow(self.name)

    def export(self) -> dict[str, Any]:
        data = super().export()
        data["config"] = {
            "wallet_address": self._wallet_address or None,
            "realm": self._realm or None,
            "testnet": self._testnet,
            "has_secret_key": bool(self._secret_key),
            "pricing_tiers": [
                {"price": str(t.price), "applied_to": t.applied_to} for t in self._tiers
            ],
        }
        return data
