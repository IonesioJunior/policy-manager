"""TransactionPolicy — confirms transactions with external ledger after execution."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

from policy_manager.policies.base import Policy
from policy_manager.result import PolicyResult

if TYPE_CHECKING:
    from policy_manager.context import RequestContext

try:
    import httpx

    _HTTPX_AVAILABLE = True
except ImportError:
    _HTTPX_AVAILABLE = False


class TransactionPolicy(Policy):
    """Confirms transactions with an external ledger after successful execution.

    This policy runs in the post-execution phase. It expects a transaction token
    in the request input (placed there by the client who has already reserved
    funds with the ledger) and confirms the transaction after the handler succeeds.

    Parameters:
        name: Unique policy name.
        ledger_url: Ledger API base URL. Falls back to LEDGER_URL env var.
        api_token: API token for ledger authentication. Falls back to
            LEDGER_API_TOKEN env var.
        token_field: Field name in context.input containing the transaction token.
            Defaults to "transaction_token".
        timeout: HTTP request timeout in seconds. Defaults to 30.0.
        price_per_request: Price charged per request (declared to SyftAPI for
            client visibility). Defaults to 0.0.

    Raises:
        RuntimeError: If httpx is not installed. Install with
            ``pip install policy-manager[ledger]``.

    Example:
        >>> policy = TransactionPolicy(
        ...     ledger_url="https://api.ledger.example.com",
        ...     api_token="at_xxx",
        ... )
        >>> await pm.add_policy(policy)

        Or with environment variables:
        >>> # Set LEDGER_URL and LEDGER_API_TOKEN env vars
        >>> policy = TransactionPolicy()
    """

    def __init__(
        self,
        *,
        name: str = "transaction",
        ledger_url: str | None = None,
        api_token: str | None = None,
        token_field: str = "transaction_token",
        timeout: float = 30.0,
        price_per_request: float = 0.0,
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
        self._price_per_request = price_per_request

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
        """Export policy configuration in canonical format.

        Returns the policy's native configuration. Consumers (like syfthub-api)
        are responsible for transforming this to their specific format needs.
        """
        data = super().export()
        data["config"] = {
            "ledger_url": self._ledger_url or None,
            "token_field": self._token_field,
            "timeout": self._timeout,
            "has_api_token": bool(self._api_token),
            "price_per_request": self._price_per_request,
        }
        return data

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
