"""TransactionPolicy — confirms transactions with external ledger after execution."""

from __future__ import annotations

import os
import warnings
from typing import TYPE_CHECKING, Any, Literal

from policy_manager.policies.base import Policy
from policy_manager.result import PolicyResult

if TYPE_CHECKING:
    from policy_manager.context import RequestContext

try:
    import httpx  # type: ignore[import-not-found]

    _HTTPX_AVAILABLE = True
except ImportError:
    _HTTPX_AVAILABLE = False

# Pricing mode types
PricingMode = Literal["per_call", "per_token"]


class TransactionPolicy(Policy):
    """Confirms transactions with an external ledger after successful execution.

    This policy validates transaction tokens in pre-execution and confirms
    transactions with the ledger in post-execution. It expects a transaction token
    in the request input (placed there by the client who has already reserved
    funds with the ledger).

    Parameters:
        name: Unique policy name.
        ledger_url: Ledger API base URL. Falls back to LEDGER_URL env var.
        api_token: API token for ledger authentication. Falls back to
            LEDGER_API_TOKEN env var.
        token_field: Field name in context.input containing the transaction token.
            Defaults to "transaction_token".
        timeout: HTTP request timeout in seconds. Defaults to 30.0.
        pricing_mode: Either "per_call" (flat rate) or "per_token" (usage-based).
        price_per_call: Price per request when using "per_call" mode.
        input_token_price: Price per input token when using "per_token" mode.
        output_token_price: Price per output token when using "per_token" mode.
        currency: Currency code for pricing. Defaults to "USD".
        price_per_request: Deprecated. Use price_per_call instead.

    Raises:
        RuntimeError: If httpx is not installed. Install with
            ``pip install policy-manager[ledger]``.

    Example:
        >>> # Per-call pricing
        >>> policy = TransactionPolicy(
        ...     ledger_url="https://api.ledger.example.com",
        ...     api_token="at_xxx",
        ...     pricing_mode="per_call",
        ...     price_per_call=0.05,
        ... )

        >>> # Per-token pricing
        >>> policy = TransactionPolicy(
        ...     pricing_mode="per_token",
        ...     input_token_price=0.01,
        ...     output_token_price=0.02,
        ... )
    """

    _policy_type = "transaction"
    _policy_description = "Confirms transactions with external ledger for billing"

    def __init__(
        self,
        *,
        name: str = "transaction",
        ledger_url: str | None = None,
        api_token: str | None = None,
        token_field: str = "transaction_token",
        timeout: float = 30.0,
        # Pricing parameters (SyftHub-compatible)
        pricing_mode: PricingMode = "per_call",
        price_per_call: float = 0.0,
        input_token_price: float = 0.0,
        output_token_price: float = 0.0,
        currency: str = "USD",
        # Deprecated (backward compatibility)
        price_per_request: float | None = None,
    ) -> None:
        if not _HTTPX_AVAILABLE:
            raise RuntimeError(
                "httpx is required for TransactionPolicy. "
                "Install with: pip install policy-manager[ledger]"
            )

        self._name = name
        resolved_url = ledger_url or os.getenv("LEDGER_URL", "")
        self._ledger_url = resolved_url.rstrip("/") if resolved_url else ""
        self._api_token = api_token or os.getenv("LEDGER_API_TOKEN", "")
        self._token_field = token_field
        self._timeout = timeout

        # Handle deprecated price_per_request parameter
        if price_per_request is not None:
            warnings.warn(
                "price_per_request is deprecated, use price_per_call instead",
                DeprecationWarning,
                stacklevel=2,
            )
            price_per_call = price_per_request

        self._pricing_mode: PricingMode = pricing_mode
        self._price_per_call = price_per_call
        self._input_token_price = input_token_price
        self._output_token_price = output_token_price
        self._currency = currency

    @property
    def name(self) -> str:
        return self._name

    @staticmethod
    def _extract_transfer_id(token: str) -> str | None:
        """Extract the transfer ID from a confirmation token.

        Token format: transactionId.salt.expiresAt.signature
        """
        try:
            parts = token.split(".")
            if len(parts) != 4:
                return None
            return parts[0] if parts[0] else None
        except Exception:
            return None

    def export(self) -> dict[str, Any]:
        """Export policy configuration in SyftHub-compatible format.

        Returns configuration structured for SyftHub consumption:
        - Per-call mode: {"pricingMode": "per_call", "price": <amount>}
        - Per-token mode: {"costs": {"inputTokens": <price>, "outputTokens": <price>}}
        """
        data = super().export()

        if self._pricing_mode == "per_call":
            data["config"] = {
                "pricingMode": "per_call",
                "price": self._price_per_call,
                "currency": self._currency,
                "ledger_url": self._ledger_url or None,
                "token_field": self._token_field,
                "timeout": self._timeout,
                "has_api_token": bool(self._api_token),
            }
        else:  # per_token
            data["config"] = {
                "pricingMode": "per_token",
                "costs": {
                    "inputTokens": self._input_token_price,
                    "outputTokens": self._output_token_price,
                    "currency": self._currency,
                },
                "ledger_url": self._ledger_url or None,
                "token_field": self._token_field,
                "timeout": self._timeout,
                "has_api_token": bool(self._api_token),
            }
        return data

    async def pre_execute(self, context: RequestContext) -> PolicyResult:
        """Validate the transaction token before handler execution.

        Checks that:
        1. The transaction token is present in context.input
        2. The token format is valid (can extract transfer ID)

        Args:
            context: The request context containing the transaction token.

        Returns:
            PolicyResult.allow() if token is valid,
            PolicyResult.deny() if token is missing or invalid.
        """
        token = context.input.get(self._token_field)
        if not token:
            return PolicyResult.deny(
                self.name,
                f"{self._token_field} required in request input",
            )

        if self._extract_transfer_id(token) is None:
            return PolicyResult.deny(
                self.name,
                "Invalid token format",
            )

        return PolicyResult.allow(self.name)

    async def post_execute(self, context: RequestContext) -> PolicyResult:
        """Confirm the transaction with the ledger after handler success.

        Args:
            context: The request context containing the transaction token.

        Returns:
            PolicyResult.allow() if confirmation succeeds,
            PolicyResult.deny() if confirmation fails or token is missing.
        """
        # Get transaction token from input
        token = context.input.get(self._token_field)
        if not token:
            return PolicyResult.deny(
                self.name,
                f"{self._token_field} required in request input",
            )

        # Extract transfer ID from token
        # Token format: transactionId.salt.expiresAt.signature
        transfer_id = self._extract_transfer_id(token)
        if not transfer_id:
            return PolicyResult.deny(
                self.name,
                "Invalid token format: could not extract transfer ID",
            )

        # Verify ledger is configured
        if not self._ledger_url:
            return PolicyResult.deny(
                self.name,
                "Ledger URL not configured (set ledger_url or LEDGER_URL env var)",
            )

        if not self._api_token:
            return PolicyResult.deny(
                self.name,
                "Ledger API token not configured (set api_token or LEDGER_API_TOKEN env var)",
            )

        try:
            import uuid

            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self._ledger_url}/v1/transfers/{transfer_id}/confirm",
                    json={"confirmation_token": token},
                    headers={
                        "Authorization": f"Bearer {self._api_token}",
                        "Idempotency-Key": str(uuid.uuid4()),
                    },
                    timeout=self._timeout,
                )

                if response.status_code == 200:
                    # Store confirmation result in metadata for downstream access
                    context.metadata[f"{self.name}_confirmed"] = True
                    return PolicyResult.allow(self.name)
                else:
                    return PolicyResult.deny(
                        self.name,
                        f"Ledger confirmation failed: HTTP {response.status_code}",
                    )

        except httpx.TimeoutException:
            return PolicyResult.deny(
                self.name,
                f"Ledger confirmation timed out after {self._timeout} seconds",
            )
        except httpx.ConnectError:
            return PolicyResult.deny(
                self.name,
                "Could not connect to ledger",
            )
        except Exception as e:
            return PolicyResult.deny(
                self.name,
                f"Ledger confirmation error: {e}",
            )
