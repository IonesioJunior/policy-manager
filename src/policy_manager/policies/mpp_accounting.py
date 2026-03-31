"""MppAccountingPolicy — per-query payments via the Machine Payments Protocol (MPP)."""

from __future__ import annotations

import fnmatch
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar

from policy_manager.exceptions import PaymentRequiredError
from policy_manager.policies.base import Policy
from policy_manager.result import PolicyResult

if TYPE_CHECKING:
    from policy_manager.context import RequestContext

try:
    from mpp.server import Mpp  # type: ignore[import-untyped]

    _MPP_AVAILABLE = True
except ImportError:
    _MPP_AVAILABLE = False


@dataclass
class _PricingTier:
    """A single pricing tier for MPP accounting.

    Attributes:
        price: Price in USD per query. 0.0 means free — payment logic is skipped.
        applied_to: List of user_id glob patterns this tier applies to.
    """

    price: float
    applied_to: list[str]

    def matches(self, user_id: str) -> bool:
        return any(fnmatch.fnmatch(user_id, pat) for pat in self.applied_to)

    @property
    def specificity(self) -> int:
        """Higher specificity wins when multiple tiers match.

        Non-wildcard patterns score their character length; wildcard-only
        patterns score 0.
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

    Pricing is tiered: the most-specific matching tier wins. A price of ``0.0``
    is a free tier that skips all payment logic entirely.

    A class-level cache of ``Mpp`` instances (keyed by
    ``"{wallet_address}:{realm}"``) ensures that the same server instance
    that issued a challenge also verifies the returned credential — required
    for consistent HMAC verification across the 402 → pay → retry round-trip.

    Parameters:
        name: Unique policy instance name. Defaults to ``"mpp_accounting"``.
        wallet_address: Endpoint owner's Tempo wallet address (``0x...``).
            Falls back to the ``MPP_WALLET_ADDRESS`` environment variable.
        realm: Endpoint identifier used as the MPP realm (typically the
            endpoint slug). Falls back to the ``MPP_REALM`` env var.
        pricing_tiers: List of dicts, each with:
            - ``"price"`` (float) — cost in USD per query.
            - ``"applied_to"`` (list[str]) — user_id glob patterns.
            Defaults to a single free-tier ``[{"price": 0.0, "applied_to": ["*"]}]``.
        testnet: Use Tempo testnet RPC when ``True`` (default). Set to
            ``False`` for mainnet.
        secret_key: HMAC secret used to sign and verify MPP challenges.
            Auto-generated and persisted externally if not provided; falls back
            to the ``MPP_SECRET_KEY`` environment variable.

    Raises:
        RuntimeError: If ``pympp`` is not installed.
            Install with ``pip install policy-manager[mpp]``.

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

    # Cached Mpp server instances, keyed by "{wallet_address}:{realm}".
    # Shared across instances so the same Mpp object that issues a challenge
    # is available to verify the credential on the follow-up request.
    _mpp_instances: ClassVar[dict[str, Any]] = {}

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
            {"price": 0.0, "applied_to": ["*"]}
        ]
        self._tiers: list[_PricingTier] = [
            _PricingTier(
                price=float(t["price"]),
                applied_to=list(t.get("applied_to", ["*"])),
            )
            for t in raw_tiers
        ]

    @property
    def name(self) -> str:
        return self._name

    def _get_mpp(self) -> Any:
        """Return (or create and cache) the Mpp server instance for this wallet+realm."""
        key = f"{self._wallet_address}:{self._realm}"
        if key not in MppAccountingPolicy._mpp_instances:
            MppAccountingPolicy._mpp_instances[key] = Mpp(  # type: ignore[name-defined]
                wallet_address=self._wallet_address,
                secret_key=self._secret_key,
                testnet=self._testnet,
            )
        return MppAccountingPolicy._mpp_instances[key]

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

        if tier.price == 0.0:
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

        try:
            result = mpp.charge(
                price=tier.price,
                realm=self._realm,
                authorization=credential,
            )
        except Exception as e:
            # The pympp library raises when no valid credential is present,
            # embedding the challenge in the exception. Surface it as a
            # PaymentRequiredError so the executor can produce a 402.
            challenge = getattr(e, "challenge", None) or getattr(e, "www_authenticate", None)
            if challenge is not None:
                raise PaymentRequiredError(realm=self._realm, challenge=str(challenge)) from e
            return PolicyResult.deny(self.name, f"MPP charge failed: {e}")

        if not isinstance(result, tuple):
            challenge_str = str(getattr(result, "www_authenticate", result))
            raise PaymentRequiredError(realm=self._realm, challenge=challenge_str)

        _, receipt = result
        context.metadata["mpp_payment_receipt"] = receipt
        context.metadata["mpp_price_charged"] = tier.price
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
                {"price": t.price, "applied_to": t.applied_to} for t in self._tiers
            ],
        }
        return data
